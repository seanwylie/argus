"""Build portfolio snapshots from current ``runs/*/latest`` artifact state."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.decision.persistence import load_latest_portfolio, load_latest_product_decisions
from argus.escalation.packet import list_packets
from argus.findings.persistence import load_latest_findings
from argus.history.models import (
    PortfolioSnapshot,
    ProductSnapshot,
    ProductSnapshotDelta,
    SnapshotDelta,
)
from argus.history.storage import sanitize_label
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle


def _last_signal_at(bundle: Any) -> str | None:
    if bundle is None:
        return None
    ts = bundle.collected_at_utc
    if not ts and bundle.records:
        obs = [getattr(r, "observed_at", None) for r in bundle.records]
        obs = [o for o in obs if o is not None]
        if obs:
            latest = max(obs)
            if hasattr(latest, "isoformat"):
                return latest.isoformat()
    return ts or None


def _portfolio_rank_by_product(repo_root: Path) -> dict[str, dict[str, Any]]:
    raw = load_latest_portfolio(repo_root)
    if not raw or not isinstance(raw, dict):
        return {}
    ranked = raw.get("ranked") or []
    out: dict[str, dict[str, Any]] = {}
    if isinstance(ranked, list):
        for row in ranked:
            if isinstance(row, dict) and row.get("product_id"):
                out[str(row["product_id"])] = row
    return out


def build_portfolio_snapshot(
    repo_root: Path,
    *,
    snapshot_id: str,
    observed_at_utc: str,
    label: str | None,
    products_dir: Path | None = None,
) -> PortfolioSnapshot:
    """
    Capture current inventory + per-product latest signals/findings/decisions/escalations.

    Does not mutate existing artifact files.
    """
    root = repo_root.resolve()
    inv = build_inventory(root, products_dir=products_dir)
    portfolio_by_pid = _portfolio_rank_by_product(root)
    all_escalations = list_packets(root, limit=500)
    esc_by_pid: dict[str, list[dict[str, Any]]] = {}
    for row in all_escalations:
        pid = row.get("product_id")
        if not pid:
            continue
        esc_by_pid.setdefault(str(pid), []).append(row)

    products: list[ProductSnapshot] = []
    for pid in sorted(inv.valid.keys()):
        rec = inv.valid[pid]
        node = rec.node
        status = node.type_info.status if node.type_info else ""
        state = (
            node.type_info.state
            if node.type_info and node.type_info.state
            else node.lifecycle.stage.value
        )
        lifecycle_stage = node.lifecycle.stage.value

        monthly = node.cost.monthly_usd

        fb = load_latest_findings(root, pid)
        findings_count = len(fb.findings) if fb else 0
        sev_counts: Counter[str] = Counter()
        for f in (fb.findings if fb else []):
            sev_counts[f.severity.value] += 1

        sig_b = load_latest_bundle(root, pid)
        last_sig = _last_signal_at(sig_b)

        dec_raw = load_latest_product_decisions(root, pid)
        top_summary = ""
        priority_score: float | None = None
        confidence: float | None = None
        kill_candidate = False
        lifecycle_scores: dict[str, float] = {}
        if dec_raw:
            lc = dec_raw.get("lifecycle") or {}
            kill_candidate = bool(lc.get("kill_candidate"))
            scores = lc.get("scores")
            if isinstance(scores, dict):
                lifecycle_scores = {
                    str(k): float(v) for k, v in scores.items() if isinstance(v, (int, float))
                }
            cands = dec_raw.get("candidates") or []
            if isinstance(cands, list) and cands:
                c0 = cands[0]
                if isinstance(c0, dict):
                    top_summary = str(c0.get("summary") or "")
                    ps = c0.get("priority_score")
                    priority_score = float(ps) if ps is not None else None
                    if c0.get("confidence") is not None:
                        confidence = float(c0["confidence"])

        prow = portfolio_by_pid.get(pid)
        if prow is not None:
            if priority_score is None and prow.get("priority_score") is not None:
                priority_score = float(prow["priority_score"])
            if not top_summary and prow.get("summary"):
                top_summary = str(prow["summary"])

        products.append(
            ProductSnapshot(
                snapshot_id=snapshot_id,
                product_id=pid,
                observed_at_utc=observed_at_utc,
                state=state,
                status=status,
                lifecycle_stage=lifecycle_stage,
                monthly_cost_usd=monthly,
                last_signal_at=last_sig,
                active_findings_count=findings_count,
                findings_by_severity=dict(sev_counts),
                top_recommended_action=top_summary,
                priority_score=priority_score,
                top_confidence=confidence,
                escalation_count=len(esc_by_pid.get(pid, [])),
                lifecycle_scores=lifecycle_scores,
                kill_candidate=kill_candidate,
                source_paths={
                    "product_yaml": f"products/{pid}/product.yaml",
                    "findings_latest": f"runs/findings/latest/{pid}.json",
                    "signals_latest": f"runs/signals/latest/{pid}.json",
                    "decisions_latest": f"runs/decisions/latest/{pid}.json",
                },
            )
        )

    return PortfolioSnapshot(
        snapshot_id=snapshot_id,
        observed_at_utc=observed_at_utc,
        label=label,
        repo_root=str(root),
        products=products,
    )


def compute_snapshot_delta(older: PortfolioSnapshot, newer: PortfolioSnapshot) -> SnapshotDelta:
    """Diff two portfolio snapshots (older → newer)."""
    by_id_old = {p.product_id: p for p in older.products}
    by_id_new = {p.product_id: p for p in newer.products}
    all_ids = sorted(set(by_id_old) | set(by_id_new))
    deltas: list[ProductSnapshotDelta] = []

    for pid in all_ids:
        o = by_id_old.get(pid)
        n = by_id_new.get(pid)
        if o is None and n is not None:
            o = _empty_product_snapshot(
                n.product_id,
                older.snapshot_id,
                older.observed_at_utc,
                n.source_paths,
            )
        if n is None and o is not None:
            n = _empty_product_snapshot(
                o.product_id,
                newer.snapshot_id,
                newer.observed_at_utc,
                o.source_paths,
            )
        assert o is not None and n is not None

        oc = o.monthly_cost_usd
        nc = n.monthly_cost_usd
        cost_delta: float | None = None
        if oc is not None and nc is not None:
            cost_delta = nc - oc
        elif oc is None and nc is not None:
            cost_delta = nc
        elif oc is not None and nc is None:
            cost_delta = -oc

        ot = o.top_confidence
        nt = n.top_confidence
        conf_delta: float | None = None
        if ot is not None and nt is not None:
            conf_delta = nt - ot
        elif ot is None and nt is not None:
            conf_delta = nt
        elif ot is not None and nt is None:
            conf_delta = -ot

        deltas.append(
            ProductSnapshotDelta(
                product_id=pid,
                active_findings_count_delta=n.active_findings_count - o.active_findings_count,
                monthly_cost_usd_delta=cost_delta,
                top_recommended_action_changed=o.top_recommended_action != n.top_recommended_action,
                previous_top_action=o.top_recommended_action,
                current_top_action=n.top_recommended_action,
                lifecycle_stage_changed=o.lifecycle_stage != n.lifecycle_stage,
                previous_lifecycle_stage=o.lifecycle_stage,
                current_lifecycle_stage=n.lifecycle_stage,
                escalation_count_delta=n.escalation_count - o.escalation_count,
                top_confidence_delta=conf_delta,
                kill_candidate_changed=o.kill_candidate != n.kill_candidate,
                previous_kill_candidate=o.kill_candidate,
                current_kill_candidate=n.kill_candidate,
                last_signal_changed=o.last_signal_at != n.last_signal_at,
                previous_last_signal_at=o.last_signal_at,
                current_last_signal_at=n.last_signal_at,
            )
        )

    return SnapshotDelta(
        from_snapshot_id=older.snapshot_id,
        to_snapshot_id=newer.snapshot_id,
        from_observed_at_utc=older.observed_at_utc,
        to_observed_at_utc=newer.observed_at_utc,
        product_deltas=deltas,
    )


def _empty_product_snapshot(
    product_id: str,
    snapshot_id: str,
    observed_at_utc: str,
    source_paths: dict[str, str],
) -> ProductSnapshot:
    """Synthetic empty row when a product appears in only one of the snapshots."""
    return ProductSnapshot(
        snapshot_id=snapshot_id,
        product_id=product_id,
        observed_at_utc=observed_at_utc,
        state="",
        status="",
        lifecycle_stage="",
        monthly_cost_usd=None,
        last_signal_at=None,
        active_findings_count=0,
        findings_by_severity={},
        top_recommended_action="",
        priority_score=None,
        top_confidence=None,
        escalation_count=0,
        lifecycle_scores={},
        kill_candidate=False,
        source_paths=dict(source_paths),
    )


def new_snapshot_id_and_time(*, label: str | None) -> tuple[str, str]:
    """Return ``(snapshot_id, observed_at_utc ISO)``."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    iso = datetime.now(timezone.utc).isoformat()
    if label:
        safe = sanitize_label(label)
        return f"{ts}_{safe}", iso
    return ts, iso
