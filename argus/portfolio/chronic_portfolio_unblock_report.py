"""
Chronic portfolio unblock map — per-product blockage view for inspect / evidence-refresh posture.

Reads operator queue, intervention report, and portfolio cycle summary. Writes
``runs/debug/chronic_portfolio_unblock/latest.{json,md}`` for operator triage.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA, portfolio_cycle_dir
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, operator_queue_output_dir

CHRONIC_PORTFOLIO_UNBLOCK_SCHEMA = "argus.debug_chronic_portfolio_unblock.v1"


def chronic_portfolio_unblock_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "debug" / "chronic_portfolio_unblock"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _iso_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _queue_entry_by_id(entries: list[dict[str, Any]], pid: str) -> dict[str, Any] | None:
    for e in entries:
        if not isinstance(e, dict):
            continue
        if str(e.get("product_id") or "").strip() == pid:
            return e
    return None


def _intervention_row(rows: list[dict[str, Any]], pid: str) -> dict[str, Any] | None:
    for r in rows:
        if not isinstance(r, dict):
            continue
        if str(r.get("product_id") or "").strip() == pid:
            return dict(r)
    return None


def _blockage_themes(
    *,
    queue_entry: dict[str, Any] | None,
    intervention: dict[str, Any] | None,
) -> tuple[list[str], list[str]]:
    """Return (theme_codes, material_action_hints)."""
    themes: list[str] = []
    hints: list[str] = []
    codes_inv = list(intervention.get("detection_reason_codes") or []) if intervention else []

    if any("readiness_tier_debt_stagnation" in str(c) for c in codes_inv):
        themes.append("readiness_tier_stagnation")
        hints.append("Produce a material delta in readiness inputs (signals/temporal/findings/decisions) so tier can move.")
    if any("repeated_same_next_action" in str(c) for c in codes_inv):
        themes.append("repeated_same_next_action")
        hints.append("Same queue next_action across progression window — execute that action or refresh upstream artifacts.")

    if not queue_entry:
        return sorted(set(themes)), hints

    orch = str(queue_entry.get("orchestration_status") or "").lower()
    na = str(queue_entry.get("next_action") or "").lower()
    pr = str(queue_entry.get("priority_reason") or "")

    if orch == "stale_refresh_needed" or "stale" in orch:
        themes.append("stale_observability")
        if "temporal" in na or "temporal" in pr.lower():
            hints.append("Temporal/signals freshness: run the worker path that refreshes temporal + signals bundles for this product.")
        elif "signal" in na or "signals" in pr.lower():
            hints.append("Signals phase/staleness: run signals collection or refresh per product orchestration.")
        else:
            hints.append("Orchestration reports stale signals/temporal/audit — refresh observability artifacts before expecting readiness movement.")

    if na == "findings_generate" or "findings" in na:
        themes.append("findings_chain_gap")
        hints.append("Queue points at findings_generate — run findings generation / pipeline so decisions have evidence.")

    if str(queue_entry.get("readiness_tier") or "") == "unprofiled":
        themes.append("readiness_unprofiled")
        hints.append("Tier remains unprofiled until interpretable signals/decisions exist — avoid faking tier advancement.")

    return sorted(set(themes)), hints


def build_chronic_portfolio_unblock_payload(
    repo_root: Path,
    *,
    product_ids: list[str] | None = None,
    queue_rank_cap: int = 8,
) -> dict[str, Any]:
    """
    Build a deterministic cross-artifact map for chronic products.

    When ``product_ids`` is None, uses ``intervention.flagged_products`` product ids
    plus queue ranks ``<= queue_rank_cap``.
    """
    root = repo_root.resolve()
    evaluated_at = _iso_now()

    qp = _load_json(operator_queue_output_dir(root) / "latest.json")
    queue_ok = bool(qp and str(qp.get("schema") or "") == OPERATOR_QUEUE_SCHEMA)
    entries = list(qp.get("entries") or []) if queue_ok else []

    inv = _load_json(root / "runs" / "portfolio" / "intervention" / "latest.json")
    inv_ok = bool(inv and str(inv.get("schema") or "") == PORTFOLIO_INTERVENTION_SCHEMA)
    flagged = [x for x in (inv.get("flagged_products") or []) if isinstance(x, dict)] if inv_ok else []

    cyc = _load_json(portfolio_cycle_dir(root) / "latest.json")
    cyc_ok = bool(cyc and str(cyc.get("schema") or "") == PORTFOLIO_CYCLE_SCHEMA)
    summ = cyc.get("summary") or {} if isinstance(cyc, dict) else {}
    overall = str(summ.get("overall_operator_recommendation") or "").strip()
    rationale = list(summ.get("overall_rationale_codes") or []) if isinstance(summ.get("overall_rationale_codes"), list) else []

    scope: set[str] = set()
    if product_ids:
        scope.update(str(x).strip() for x in product_ids if str(x).strip())
    else:
        for row in flagged:
            pid = str(row.get("product_id") or "").strip()
            if pid:
                scope.add(pid)
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                rnk = int(e.get("queue_rank") or 999)
            except (TypeError, ValueError):
                rnk = 999
            if rnk <= max(1, int(queue_rank_cap)):
                pid = str(e.get("product_id") or "").strip()
                if pid:
                    scope.add(pid)

    products_out: list[dict[str, Any]] = []
    for pid in sorted(scope):
        qe = _queue_entry_by_id(entries, pid)
        ir = _intervention_row(flagged, pid)
        themes, hints = _blockage_themes(queue_entry=qe, intervention=ir)
        products_out.append(
            {
                "product_id": pid,
                "operator_queue": qe,
                "intervention": ir,
                "blockage_themes": themes,
                "material_action_hints": sorted(set(hints)),
            }
        )

    notes: list[str] = []
    if overall == "inspect_specific_products":
        notes.append(
            "Portfolio cycle overall is inspect_specific_products — aligns with quiescence inspect + non-benign intervention flags."
        )
    if not products_out:
        notes.append("No products in scope — pass --product or ensure intervention/queue artifacts exist.")

    return {
        "schema": CHRONIC_PORTFOLIO_UNBLOCK_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "inputs": {
            "product_ids_filter": list(product_ids) if product_ids else None,
            "queue_rank_cap": int(queue_rank_cap),
            "artifacts": {
                "operator_queue": "runs/portfolio/operator_queue/latest.json",
                "intervention": "runs/portfolio/intervention/latest.json",
                "portfolio_cycle": "runs/portfolio/cycle/latest.json",
            },
        },
        "portfolio_cycle_summary": {
            "overall_operator_recommendation": overall if cyc_ok else None,
            "overall_rationale_codes": rationale if cyc_ok else [],
            "cycle_run_id": cyc.get("run_id") if cyc_ok else None,
        },
        "sources_present": {
            "operator_queue": queue_ok,
            "intervention": inv_ok,
            "portfolio_cycle": cyc_ok,
        },
        "products": products_out,
        "notes": notes,
    }


def render_chronic_portfolio_unblock_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Chronic portfolio unblock map",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Portfolio cycle",
        "",
    ]
    pcs = payload.get("portfolio_cycle_summary") or {}
    lines.append(f"- Overall: `{pcs.get('overall_operator_recommendation')}`")
    lines.append(f"- Rationale codes: {pcs.get('overall_rationale_codes')}")
    lines.append("")
    lines.append("## Products")
    lines.append("")
    prods = payload.get("products") or []
    if not prods:
        lines.append("—")
    for p in prods:
        pid = p.get("product_id")
        lines.append(f"### `{pid}`")
        qe = p.get("operator_queue")
        if isinstance(qe, dict) and qe:
            lines.append(
                f"- Queue: rank={qe.get('queue_rank')} next_action=`{qe.get('next_action')}` "
                f"orch=`{qe.get('orchestration_status')}` tier=`{qe.get('readiness_tier')}`"
            )
        else:
            lines.append("- Queue: (no entry)")
        ir = p.get("intervention")
        if isinstance(ir, dict) and ir:
            lines.append(
                f"- Intervention: `{ir.get('intervention_category')}` / `{ir.get('severity')}` "
                f"chronicity=`{ir.get('chronicity')}`"
            )
            lines.append(f"  - Codes: {ir.get('detection_reason_codes')}")
        else:
            lines.append("- Intervention: (not flagged in latest intervention)")
        lines.append(f"- Themes: {p.get('blockage_themes')}")
        for h in p.get("material_action_hints") or []:
            lines.append(f"  - Hint: {h}")
        lines.append("")
    lines.append("## Notes")
    lines.append("")
    for n in payload.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_chronic_portfolio_unblock_artifacts(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    root = repo_root.resolve()
    d = chronic_portfolio_unblock_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    j = d / "latest.json"
    m = d / "latest.md"
    j.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    m.write_text(render_chronic_portfolio_unblock_markdown(payload), encoding="utf-8")
    return j, m


def run_chronic_portfolio_unblock_report(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
    product_ids: list[str] | None = None,
    queue_rank_cap: int = 8,
) -> dict[str, Any]:
    payload = build_chronic_portfolio_unblock_payload(
        repo_root,
        product_ids=product_ids,
        queue_rank_cap=queue_rank_cap,
    )
    if write_artifacts:
        write_chronic_portfolio_unblock_artifacts(repo_root, payload)
    return payload


__all__ = [
    "CHRONIC_PORTFOLIO_UNBLOCK_SCHEMA",
    "build_chronic_portfolio_unblock_payload",
    "chronic_portfolio_unblock_dir",
    "render_chronic_portfolio_unblock_markdown",
    "run_chronic_portfolio_unblock_report",
    "write_chronic_portfolio_unblock_artifacts",
]
