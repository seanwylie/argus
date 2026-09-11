"""
Portfolio-level operator queue — deterministic ranking of which product to touch next.

Uses per-product operator snapshots (``argus.operator_snapshot.v1``) when present; otherwise
``runs/orchestration/latest/<id>.json`` is converted via :func:`argus.orchestrator.operator_snapshot.build_operator_snapshot`.
If neither exists, the row is still listed with a documented fallback score.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.observability.signal_contract import compact_signal_contract_row_fields
from argus.orchestrator.artifact_paths import orchestration_latest_path
from argus.orchestrator.operator_snapshot import (
    OPERATOR_SNAPSHOT_SCHEMA,
    build_operator_snapshot,
    operator_snapshot_json_path,
)
from argus.policy.operator_policy import default_operator_policy, load_operator_policy
from argus.portfolio.evidence_maturity import evidence_maturity_hint_from_snapshot
from argus.portfolio.permission_operator_surface import build_permission_gate_summary
from argus.portfolio.strategy_influence import (
    STRATEGY_INFLUENCE_SCHEMA,
    load_latest_strategic_posture,
    queue_priority_nudge,
)
from argus.products.instrumentation_feedback import (
    load_latest_signal_instrumentation_apply_by_product,
    refine_instrumentation_pressure_with_apply_context,
)
from argus.products.inventory import build_inventory
from argus.products.signal_instrumentation import load_latest_signal_instrumentation_by_product

OPERATOR_QUEUE_SCHEMA = "argus.operator_queue.v1"

# --- Scoring weights (v1): higher priority_score = attend sooner. ---
# Defaults mirror :func:`default_operator_policy` ``queue_scoring``; runtime uses :func:`load_operator_policy`.
_pol0 = default_operator_policy()
_qs0 = _pol0["queue_scoring"]
SCORING_WEIGHTS_VERSION = str(_qs0["weights_version"])
TIER_POINTS: dict[str, float] = dict(_qs0["tier_points"])
TIER_UNKNOWN_POINTS = float(_qs0["tier_unknown_points"])

DEBT_SCALE = float(_qs0["debt_scale"])

FIRST_PASS_POINTS: dict[str, float] = dict(_qs0["first_pass_points"])
FIRST_PASS_MISSING_POINTS = float(_qs0["first_pass_missing_points"])

POINTS_SIGNALS_STALE_BUNDLE = float(_qs0["points_signals_stale_bundle"])
POINTS_SIGNALS_PHASE_ABSENT = float(_qs0["points_signals_phase_absent"])
POINTS_FLAG_SIGNALS_COLLECTION_STALE = float(_qs0["points_flag_signals_collection_stale"])
POINTS_FLAG_SIGNALS_REFRESH = float(_qs0["points_flag_signals_refresh"])
POINTS_FLAG_TEMPORAL_STALE = float(_qs0["points_flag_temporal_stale"])

POINTS_WAITING_INPUTS = float(_qs0["points_waiting_inputs"])
POINTS_BLOCKED_WAITING_STATUS = float(_qs0["points_blocked_waiting_status"])
POINTS_BLOCKERS = float(_qs0["points_blockers"])

POINTS_LOW_DECISION_CONFIDENCE = float(_qs0["points_low_decision_confidence"])
CONFIDENCE_LOW_THRESHOLD = float(_pol0["confidence"]["low_threshold"])
POINTS_MISSING_DECISION_CONFIDENCE = float(_qs0["points_missing_decision_confidence"])

FAMILY_POINTS: dict[str, float] = dict(_qs0["family_points"])
FAMILY_NONE_NON_ADVANCE_EXTRA = float(_qs0["family_none_non_advance_extra"])

LIFECYCLE_POINTS: dict[str, float] = dict(_qs0["lifecycle_points"])
LIFECYCLE_UNKNOWN_POINTS = float(_qs0["lifecycle_unknown_points"])

POINTS_NO_OPERATOR_INPUT = float(_qs0["points_no_operator_input"])


def operator_queue_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "operator_queue"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _parse_iso_utc(raw: object) -> datetime | None:
    if raw is None or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_operator_view(repo_root: Path, product_id: str) -> tuple[dict[str, Any] | None, str]:
    """
    Return ``(view_dict, source)`` where view matches operator snapshot shape.

    Prefer the **freshest durable orchestration truth**: if ``runs/orchestration/latest`` was
    evaluated more recently than a persisted ``operator_snapshot`` (by ``evaluated_at_utc`` or
    file mtime), build the view from orchestration so the queue does not lag behind CLI
    ``orchestration state`` / progression refreshes.

    Source tags include:

    * ``snapshot_file`` — on-disk operator snapshot matches (or is newer than) orchestration view
    * ``orchestration_latest`` — built from ``runs/orchestration/latest/<id>.json`` (no snapshot)
    * ``orchestration_latest_supersedes_snapshot`` — orchestration evaluated_at newer than snapshot
    * ``orchestration_latest_newer_mtime`` — fallback when timestamps missing
    * ``none`` — no inputs
    """
    root = repo_root.resolve()
    snap_path = operator_snapshot_json_path(root, product_id)
    orch_path = orchestration_latest_path(root, product_id)
    raw_snap = _load_json(snap_path)
    orch = _load_json(orch_path)
    snap_ok = raw_snap is not None and raw_snap.get("schema") == OPERATOR_SNAPSHOT_SCHEMA

    if orch is None and not snap_ok:
        return None, "none"
    if orch is None:
        return raw_snap, "snapshot_file"

    built = build_operator_snapshot(root, orch, product_id=product_id)

    if not snap_ok:
        return built, "orchestration_latest"

    oe = _parse_iso_utc(orch.get("evaluated_at_utc"))
    se = _parse_iso_utc(raw_snap.get("source_evaluated_at_utc")) if raw_snap else None
    if oe is not None and se is not None:
        if oe > se:
            return built, "orchestration_latest_supersedes_snapshot"
        return raw_snap, "snapshot_file"

    try:
        mt_orch = orch_path.stat().st_mtime
        mt_snap = snap_path.stat().st_mtime if snap_path.is_file() else 0.0
        if mt_orch > mt_snap:
            return built, "orchestration_latest_newer_mtime"
    except OSError:
        pass

    return raw_snap, "snapshot_file"


def _score_components(
    snap: dict[str, Any],
    *,
    lifecycle_stage: str | None,
    queue_scoring: dict[str, Any],
    confidence_low_threshold: float,
) -> dict[str, float]:
    qs = queue_scoring
    tp = qs["tier_points"]
    fpp = qs["first_pass_points"]
    fam_pts = qs["family_points"]
    lc_pts = qs["lifecycle_points"]
    parts: dict[str, float] = {}
    rd = snap.get("readiness") if isinstance(snap.get("readiness"), dict) else {}
    tier = str(rd.get("readiness_tier") or "").strip()
    if tier in tp:
        parts["readiness_tier"] = float(tp[tier])
    else:
        parts["readiness_tier"] = float(qs["tier_unknown_points"])

    debt = rd.get("understanding_debt")
    try:
        dv = float(debt) if debt is not None else 0.0
    except (TypeError, ValueError):
        dv = 0.0
    dv = max(0.0, min(1.0, dv))
    parts["understanding_debt"] = dv * float(qs["debt_scale"])

    ih = snap.get("import_health") if isinstance(snap.get("import_health"), dict) else {}
    fps = ih.get("first_pass_status")
    if isinstance(fps, str) and fps.strip():
        key = fps.strip().lower()
        parts["first_pass_status"] = float(fpp.get(key, qs["first_pass_missing_points"]))
    else:
        parts["first_pass_status"] = float(qs["first_pass_missing_points"])

    af = snap.get("artifact_freshness_summary")
    if isinstance(af, dict):
        bundles = af.get("bundles") if isinstance(af.get("bundles"), dict) else {}
        sig = bundles.get("signals") if isinstance(bundles.get("signals"), dict) else {}
        if str(sig.get("staleness") or "").lower() == "stale":
            parts["signals_stale"] = float(qs["points_signals_stale_bundle"])
        if str(sig.get("phase") or "").lower() == "absent":
            parts["signals_absent"] = float(qs["points_signals_phase_absent"])
        ef = af.get("eligibility_flags") if isinstance(af.get("eligibility_flags"), dict) else {}
        if ef.get("signals_collection_time_stale") is True:
            parts["flag_signals_collection_stale"] = float(qs["points_flag_signals_collection_stale"])
        if ef.get("signals_refresh_needed") is True:
            parts["flag_signals_refresh"] = float(qs["points_flag_signals_refresh"])
        if ef.get("temporal_freshness_stale") is True:
            parts["flag_temporal_stale"] = float(qs["points_flag_temporal_stale"])

    wb = snap.get("waiting_and_blocking") if isinstance(snap.get("waiting_and_blocking"), dict) else {}
    wi = wb.get("waiting_inputs") if isinstance(wb.get("waiting_inputs"), list) else []
    if len(wi) > 0:
        parts["waiting_inputs"] = float(qs["points_waiting_inputs"])
    orch_st = str(wb.get("orchestration_status") or "").lower()
    if "blocked_waiting" in orch_st:
        parts["blocked_waiting_status"] = float(qs["points_blocked_waiting_status"])
    bl = wb.get("blockers") if isinstance(wb.get("blockers"), list) else []
    if len(bl) > 0:
        parts["blockers"] = float(qs["points_blockers"])

    ds = snap.get("decision_summary") if isinstance(snap.get("decision_summary"), dict) else {}
    tc = ds.get("top_decision_confidence")
    try:
        tcf = float(tc) if tc is not None else None
    except (TypeError, ValueError):
        tcf = None
    if tcf is None:
        parts["decision_confidence_missing"] = float(qs["points_missing_decision_confidence"])
    elif tcf < confidence_low_threshold:
        parts["low_decision_confidence"] = float(qs["points_low_decision_confidence"])

    nap = snap.get("next_action_policy") if isinstance(snap.get("next_action_policy"), dict) else {}
    fam = str(nap.get("action_family") or "none").strip().lower()
    if fam in fam_pts:
        base_f = float(fam_pts[fam])
    else:
        base_f = float(fam_pts["none"])
    parts["action_family"] = base_f
    if fam in ("none", "") and tier != "advance_ready":
        parts["action_family_non_advance_none"] = float(qs["family_none_non_advance_extra"])

    ls = (lifecycle_stage or "").strip().lower()
    if ls in lc_pts:
        parts["lifecycle_stage"] = float(lc_pts[ls])
    else:
        parts["lifecycle_stage"] = float(qs["lifecycle_unknown_points"])

    return parts


def _priority_reason_from_parts(parts: dict[str, float], *, max_labels: int = 4) -> str:
    labels: dict[str, str] = {
        "readiness_tier": "readiness tier",
        "understanding_debt": "understanding debt",
        "first_pass_status": "first-pass status",
        "signals_stale": "signals stale",
        "signals_absent": "signals absent",
        "flag_signals_collection_stale": "signals collection stale",
        "flag_signals_refresh": "signals refresh needed",
        "flag_temporal_stale": "temporal stale",
        "waiting_inputs": "waiting inputs",
        "blocked_waiting_status": "blocked/waiting status",
        "blockers": "blockers",
        "low_decision_confidence": "low top decision confidence",
        "decision_confidence_missing": "missing decision confidence",
        "action_family": "next_action family",
        "action_family_non_advance_none": "no next action (non-advance tier)",
        "lifecycle_stage": "lifecycle stage",
        "no_operator_artifact": "no snapshot/orchestration",
    }
    ordered = sorted(parts.items(), key=lambda x: (-x[1], x[0]))
    out: list[str] = []
    for k, v in ordered:
        if v <= 0:
            continue
        lab = labels.get(k, k)
        out.append(f"{lab} (+{v:.1f})")
        if len(out) >= max_labels:
            break
    return "; ".join(out) if out else "baseline"


def _entry_from_snap(
    product_id: str,
    snap: dict[str, Any],
    *,
    source: str,
    lifecycle_stage: str | None,
    queue_scoring: dict[str, Any],
    confidence_low_threshold: float,
) -> dict[str, Any]:
    parts = _score_components(
        snap,
        lifecycle_stage=lifecycle_stage,
        queue_scoring=queue_scoring,
        confidence_low_threshold=confidence_low_threshold,
    )
    score = float(sum(parts.values()))
    rd = snap.get("readiness") if isinstance(snap.get("readiness"), dict) else {}
    debt = rd.get("understanding_debt")
    try:
        debt_f = float(debt) if debt is not None else None
    except (TypeError, ValueError):
        debt_f = None
    wb = snap.get("waiting_and_blocking") if isinstance(snap.get("waiting_and_blocking"), dict) else {}
    return {
        "product_id": product_id,
        "priority_score": round(score, 4),
        "priority_score_breakdown": {k: round(v, 4) for k, v in sorted(parts.items())},
        "priority_reason": _priority_reason_from_parts(parts),
        "next_action": snap.get("next_action"),
        "readiness_tier": rd.get("readiness_tier"),
        "understanding_debt": debt_f,
        "orchestration_status": wb.get("orchestration_status") or snap.get("orchestration_status"),
        "recommendation": snap.get("operator_recommendation"),
        "operator_view_source": source,
    }


def _entry_fallback(
    product_id: str,
    *,
    lifecycle_stage: str | None,
    queue_scoring: dict[str, Any],
) -> dict[str, Any]:
    qs = queue_scoring
    lc = qs["lifecycle_points"]
    ls = (lifecycle_stage or "").strip().lower()
    parts = {
        "no_operator_artifact": float(qs["points_no_operator_input"]),
        "lifecycle_stage": float(lc.get(ls, qs["lifecycle_unknown_points"])),
    }
    score = float(sum(parts.values()))
    return {
        "product_id": product_id,
        "priority_score": round(score, 4),
        "priority_score_breakdown": {k: round(v, 4) for k, v in sorted(parts.items())},
        "priority_reason": _priority_reason_from_parts(parts),
        "next_action": None,
        "readiness_tier": None,
        "understanding_debt": None,
        "orchestration_status": None,
        "recommendation": (
            "No operator snapshot and no orchestration state — run "
            f"`argus orchestration state --product-id {product_id}`."
        ),
        "operator_view_source": "none",
    }


def build_operator_queue_payload(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    operator_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    ledger_pol = load_operator_policy(root)
    qs_ledger = ledger_pol["queue_scoring"]
    inv = build_inventory(repo_root, products_dir=products_dir)
    strat_posture, _strat_raw = load_latest_strategic_posture(root)
    inst_map = load_latest_signal_instrumentation_by_product(root)
    apply_by = load_latest_signal_instrumentation_apply_by_product(root)
    inst_ref = refine_instrumentation_pressure_with_apply_context(
        inst_by_product=inst_map,
        apply_by_product=apply_by,
    )
    inst_pressure = set(inst_ref["products_under_instrumentation_pressure_effective"])
    inst_followup = set(inst_ref["products_instrumentation_apply_followup"])
    inst_resolved = set(inst_ref["products_instrumentation_resolved_via_apply"])
    raw_inst = set(inst_ref["products_under_instrumentation_pressure_raw"])
    rows: list[dict[str, Any]] = []
    for pid in sorted(inv.valid.keys()):
        rec = inv.valid[pid]
        stage = rec.node.lifecycle.stage.value
        pol_p = operator_policy if operator_policy is not None else load_operator_policy(root, product_id=pid)
        qs = pol_p["queue_scoring"]
        conf_low = float(pol_p["confidence"]["low_threshold"])
        snap, src = load_operator_view(repo_root, pid)
        if snap is not None:
            entry = _entry_from_snap(
                pid,
                snap,
                source=src,
                lifecycle_stage=stage,
                queue_scoring=qs,
                confidence_low_threshold=conf_low,
            )
            entry["operator_view_evaluated_at_utc"] = snap.get("source_evaluated_at_utc")
            entry["evidence_maturity_hint"] = evidence_maturity_hint_from_snapshot(snap)
        else:
            entry = _entry_fallback(pid, lifecycle_stage=stage, queue_scoring=qs)
            entry["operator_view_evaluated_at_utc"] = None
            entry["evidence_maturity_hint"] = None
        entry["lifecycle_stage"] = stage
        if strat_posture:
            nudge, meta = queue_priority_nudge(
                strat_posture,
                lifecycle_stage=stage,
                orchestration_status=str(entry.get("orchestration_status") or ""),
                readiness_tier=str(entry.get("readiness_tier") or ""),
                recommendation=str(entry.get("recommendation") or ""),
            )
            entry["strategy_priority_nudge"] = nudge
            entry["strategy_queue_influence"] = meta
            new_score = round(float(entry["priority_score"]) + nudge, 4)
            entry["priority_score"] = new_score
            bd = dict(entry["priority_score_breakdown"])
            bd["strategy_posture_nudge"] = round(nudge, 4)
            entry["priority_score_breakdown"] = bd
        if pid in inst_pressure:
            st = str((inst_map.get(pid) or {}).get("instrumentation_status") or "")
            rec = str(entry.get("recommendation") or "")
            if pid in inst_followup:
                ast = str((apply_by.get(pid) or {}).get("apply_status") or "")
                extra = (
                    f" Signal instrumentation apply follow-up (`{ast}`) — coverage still weak or validation incomplete "
                    f"(latest scan `{st}`). Prefer richer real telemetry and `argus products instrument-signals` "
                    "after contract changes land."
                )
                entry["signal_instrumentation_band"] = "apply_followup"
            else:
                extra = (
                    f" Signal instrumentation status `{st}` — prefer `argus products instrument-signals --product-id {pid}` "
                    "to improve observability before optimization-style work."
                )
                entry["signal_instrumentation_band"] = "needs_instrumentation"
            entry["recommendation"] = (rec + extra).strip() if rec else extra.strip()
            entry["signal_instrumentation_pressure"] = True
        elif pid in inst_resolved:
            rec = str(entry.get("recommendation") or "")
            extra = (
                " Latest worker signal instrumentation apply recorded adequate post-apply validation while the latest "
                "scan artifact may still read weak/sparse — treat sparse inspect loops as potentially reflecting "
                "product reality; focus on observing and learning."
            )
            entry["recommendation"] = (rec + extra).strip() if rec else extra.strip()
            entry["signal_instrumentation_pressure"] = False
            entry["signal_instrumentation_band"] = "post_apply_adequate"
        elif pid not in raw_inst and apply_by.get(pid):
            ap = apply_by.get(pid) or {}
            ast = str(ap.get("apply_status") or "").strip().lower()
            pa = ap.get("post_apply") if isinstance(ap.get("post_apply"), dict) else {}
            post_st = str(pa.get("instrumentation_status") or "").strip().lower()
            if ast == "success" and post_st == "adequate":
                rec = str(entry.get("recommendation") or "")
                extra = (
                    " Signal instrumentation contract has adequate post-apply validation; await richer real signals "
                    "before expanding observability."
                )
                entry["recommendation"] = (rec + extra).strip() if rec else extra.strip()
                entry["signal_instrumentation_pressure"] = False
                entry["signal_instrumentation_band"] = "applied_awaiting_richer_signals"
            else:
                entry["signal_instrumentation_pressure"] = False
        else:
            entry["signal_instrumentation_pressure"] = False
        entry.update(compact_signal_contract_row_fields(root, pid))
        pg = build_permission_gate_summary(root, pid, products_dir=products_dir)
        entry["permission_gate_one_line"] = str(pg.get("one_line") or "")
        entry["permission_gate_labels"] = list(pg.get("triage_labels") or [])
        rows.append(entry)

    rows.sort(key=lambda r: (-float(r["priority_score"]), str(r["product_id"])))
    weight_ledger = {
        "scope": "repository_baseline_structure",
        "note": "Scores use per-product effective policy (mission); values below are the repository baseline for shape.",
        "version": str(qs_ledger["weights_version"]),
        "TIER_POINTS": dict(qs_ledger["tier_points"]),
        "TIER_UNKNOWN_POINTS": qs_ledger["tier_unknown_points"],
        "DEBT_SCALE": qs_ledger["debt_scale"],
        "FIRST_PASS_POINTS": dict(qs_ledger["first_pass_points"]),
        "FIRST_PASS_MISSING_POINTS": qs_ledger["first_pass_missing_points"],
        "artifact": {
            "signals_stale_bundle": qs_ledger["points_signals_stale_bundle"],
            "signals_phase_absent": qs_ledger["points_signals_phase_absent"],
            "flag_signals_collection_stale": qs_ledger["points_flag_signals_collection_stale"],
            "flag_signals_refresh": qs_ledger["points_flag_signals_refresh"],
            "flag_temporal_stale": qs_ledger["points_flag_temporal_stale"],
        },
        "waiting": {
            "waiting_inputs": qs_ledger["points_waiting_inputs"],
            "blocked_waiting_status": qs_ledger["points_blocked_waiting_status"],
            "blockers": qs_ledger["points_blockers"],
        },
        "decision": {
            "low_confidence_below": float(ledger_pol["confidence"]["low_threshold"]),
            "low_confidence": qs_ledger["points_low_decision_confidence"],
            "missing_confidence": qs_ledger["points_missing_decision_confidence"],
        },
        "FAMILY_POINTS": dict(qs_ledger["family_points"]),
        "FAMILY_NONE_NON_ADVANCE_EXTRA": qs_ledger["family_none_non_advance_extra"],
        "LIFECYCLE_POINTS": dict(qs_ledger["lifecycle_points"]),
        "LIFECYCLE_UNKNOWN_POINTS": qs_ledger["lifecycle_unknown_points"],
        "POINTS_NO_OPERATOR_INPUT": qs_ledger["points_no_operator_input"],
    }
    for i, row in enumerate(rows, start=1):
        row["queue_rank"] = i
    product_ids = [str(r.get("product_id") or "") for r in rows if r.get("product_id")]
    return {
        "schema": OPERATOR_QUEUE_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "orchestration_alignment": {
            "schema": "argus.operator_queue_orchestration_alignment.v1",
            "note": (
                "Per-entry operator_view_evaluated_at_utc matches the orchestration evaluation time "
                "for the view used to score the row. load_operator_view prefers runs/orchestration/latest "
                "when it is newer than a persisted operator_snapshot so the queue converges with "
                "durable orchestration truth after progression/refresh."
            ),
        },
        "signal_instrumentation_context": {
            "artifacts_loaded_count": len(inst_map),
            "apply_artifacts_loaded_count": len(apply_by),
            "products_under_instrumentation_pressure": sorted(inst_pressure),
            "products_under_instrumentation_pressure_raw": sorted(raw_inst),
            "products_instrumentation_apply_followup": sorted(inst_followup),
            "products_instrumentation_resolved_via_apply": sorted(inst_resolved),
        },
        "portfolio_mission_provenance": build_portfolio_mission_provenance(root, product_ids),
        "scoring_weights_version": str(qs_ledger["weights_version"]),
        "weight_ledger": weight_ledger,
        "portfolio_strategy_influence": {
            "schema": STRATEGY_INFLUENCE_SCHEMA,
            "strategic_posture_loaded": strat_posture,
            "queue_nudges_active": strat_posture is not None,
            "note": (
                "When `strategic_posture_loaded` is set, each entry may include "
                "`strategy_priority_nudge` (bounded additive adjustment; no eligibility changes)."
                if strat_posture
                else "No `runs/portfolio/strategy/latest.json` — queue uses base scoring only."
            ),
        },
        "entries": rows,
    }


def render_operator_queue_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio operator queue",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Generated (UTC):** {payload.get('generated_at_utc')}",
        f"**Scoring weights:** v{payload.get('scoring_weights_version')}",
        "",
    ]
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    psi = payload.get("portfolio_strategy_influence") or {}
    if psi.get("queue_nudges_active"):
        lines.append(
            "**Strategy:** bounded `strategy_priority_nudge` may apply per entry when "
            "`runs/portfolio/strategy/latest.json` is present (inspect JSON)."
        )
        lines.append("")
    lines.extend(
        [
            "Higher **priority_score** means attend sooner (deterministic; see `weight_ledger` in JSON).",
            "",
            "| Rank | Product | Score | Tier | Debt | Status | Next action | "
            "Operability | Optimization | Hint | Reason |",
            "|------|---------|-------|------|------|--------|-------------|"
            "------------|-------------|------|--------|",
        ]
    )
    for e in payload.get("entries") or []:
        if not isinstance(e, dict):
            continue
        debt = e.get("understanding_debt")
        debt_s = f"{debt:.2f}" if isinstance(debt, (int, float)) else "—"
        na = e.get("next_action")
        na_s = str(na) if na is not None else "—"
        reason = str(e.get("priority_reason") or "").replace("|", "\\|")
        sop = str(e.get("signal_contract_operability_status") or "—")
        sopt = str(e.get("signal_contract_optimization_status") or "—")
        sh = str(e.get("signal_contract_hint") or "—")
        lines.append(
            f"| {e.get('queue_rank')} | `{e.get('product_id')}` | {e.get('priority_score')} | "
            f"`{e.get('readiness_tier')}` | {debt_s} | `{e.get('orchestration_status')}` | "
            f"`{na_s}` | `{sop}` | `{sopt}` | `{sh}` | {reason} |"
        )
    lines.extend(
        [
            "",
            "## Recommendations (full text)",
            "",
        ]
    )
    for e in payload.get("entries") or []:
        if not isinstance(e, dict):
            continue
        lines.append(f"### `{e.get('product_id')}` (rank {e.get('queue_rank')})")
        lines.append("")
        lines.append(str(e.get("recommendation") or "—"))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_operator_queue(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    pl = payload if payload is not None else build_operator_queue_payload(repo_root, products_dir=products_dir)
    out_dir = operator_queue_output_dir(repo_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    jpath = out_dir / "latest.json"
    mpath = out_dir / "latest.md"
    jpath.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    mpath.write_text(render_operator_queue_markdown(pl), encoding="utf-8")
    return jpath, mpath
