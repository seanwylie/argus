"""
Deterministic operator-facing copy for escalation inbox rows.

Does not affect triggering, fingerprints, or dedupe — only adds readable fields.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from argus.observability.signal_contract import signal_contract_context_for_escalation
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, operator_queue_output_dir

_ENRICHMENT_SCHEMA_NOTE = "argus.escalation_operator_enrichment.v1"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        import json

        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_operator_queue_context(repo_root: Path) -> dict[str, Any]:
    """Snapshot of operator queue for product-specific escalation copy (single read)."""
    root = repo_root.resolve()
    raw = _load_json(operator_queue_output_dir(root) / "latest.json")
    if not raw or str(raw.get("schema") or "") != OPERATOR_QUEUE_SCHEMA:
        return {
            "loaded": False,
            "top_product_id": None,
            "top_entry": None,
            "entries": [],
        }
    entries = [e for e in (raw.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: (int(e.get("queue_rank") or 999), str(e.get("product_id") or "")))
    top = entries[0] if entries else None
    tid = str(top.get("product_id") or "").strip() if top else None
    return {
        "loaded": True,
        "top_product_id": tid or None,
        "top_entry": top,
        "entries": entries,
    }


def _parse_autonomous_evidence(evidence_summary: str) -> tuple[str, list[str]]:
    s = str(evidence_summary or "")
    sr = ""
    codes: list[str] = []
    for part in s.split(";"):
        part = part.strip()
        if part.startswith("stop_reason="):
            sr = part.split("=", 1)[1].strip()
        elif part.startswith("codes="):
            raw = part.split("=", 1)[1].strip()
            try:
                v = ast.literal_eval(raw)
                if isinstance(v, list):
                    codes = [str(x) for x in v]
            except (ValueError, SyntaxError):
                pass
    return sr, codes


def _parse_cycle_evidence(evidence_summary: str) -> str:
    s = str(evidence_summary or "")
    if "overall_operator_recommendation=" in s:
        m = re.search(r"overall_operator_recommendation=(\S+)", s)
        if m:
            return m.group(1).strip().rstrip(")")
    return ""


def derive_escalation_operator_fields(
    item: dict[str, Any],
    *,
    queue_ctx: dict[str, Any],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """
    Return keys: human_readable_state, primary_product, root_cause_summary, recommended_operator_action,
    plus enrichment_schema for inspectability.
    """
    src = str(item.get("source") or "")
    ev = str(item.get("evidence_summary") or "")
    req = str(item.get("requested_action") or "")
    cat = str(item.get("category") or "")
    item_category = cat
    sev = str(item.get("severity") or "")
    pid = item.get("product_id")
    primary = str(pid).strip() if pid else None

    top_pid = queue_ctx.get("top_product_id")
    top_e = queue_ctx.get("top_entry") if isinstance(queue_ctx.get("top_entry"), dict) else {}
    top_product = str(top_pid).strip() if top_pid else None
    orch = str(top_e.get("orchestration_status") or "").strip()
    next_act = str(top_e.get("next_action") or "").strip()
    rec_line = str(top_e.get("recommendation") or "").strip()
    em_hint = str(top_e.get("evidence_maturity_hint") or "").strip()
    qrank = top_e.get("queue_rank")

    state = "Operator attention is required."
    cause = ev or req or "See evidence_summary and linked source artifacts."
    action = req or "Open the referenced `source_refs` paths and follow the next orchestration action for the product."

    if src in ("autonomous_runner", "scheduler"):
        ev_parse = ev.replace("scheduler ", "").strip() if src == "scheduler" else ev
        sr, codes = _parse_autonomous_evidence(ev_parse)
        primary = primary or top_product
        _sched = src == "scheduler"

        if sr in (
            "portfolio_refresh_failed",
            "portfolio_cycle_failed",
            "portfolio_lifecycle_failed",
            "operator_summary_failed",
            "operator_narrative_failed",
        ):
            state = (
                "The portfolio scheduler pipeline failed mid-session."
                if _sched
                else "The autonomous portfolio pipeline failed mid-session."
            )
            cause = f"Stop reason `{sr}` — stage failure before dashboard refresh completed."
            action = (
                "Inspect stderr/log context for the failing stage, fix the underlying error, "
                "then re-run `argus portfolio run-autonomous` (or the failed stage alone)."
            )
        elif sr == "empty_portfolio":
            state = "Autonomous session stopped: portfolio is empty (zero-state)."
            cause = (
                "No validated `products/<id>/product.yaml` entries — nothing to refresh or cycle yet. "
                "This is not a pipeline failure."
            )
            action = (
                "Add a product (`argus products create`, `argus products propose-creation`, or "
                "`python tools/import_product.py`), then run `argus portfolio refresh` and re-start autonomous."
            )
        elif sr == "intervention_heavy_streak":
            state = "Automation stopped: repeated intervention-heavy portfolio cycles."
            cause = "Intervention load crossed the configured streak threshold."
            action = (
                "Review `runs/portfolio/intervention/latest.json` and triage flagged products; "
                "reduce recurring intervention categories before resuming autonomous runs."
            )
        elif sr == "cycle_overall_recommendation":
            state = "Automation stopped: the portfolio cycle overall recommendation required operator review."
            cause = (
                f"Autonomous guardrail: `cycle_overall_recommendation` with codes {codes!r}. "
                "This usually means inspect, import repair, or human review."
            )
            tp = top_product or "the top-ranked queue product"
            if any("inspect" in c for c in codes):
                action = (
                    f"Review products starting with `{tp}` (queue rank {qrank or 'n/a'}): "
                    "open `runs/portfolio/cycle/latest.md`, follow overall recommendation, "
                    "and clear orchestration blockers (imports, stale refresh, findings backlog) before resuming."
                )
            else:
                action = (
                    f"Confirm operator intent to continue; address queue health starting with `{tp}` "
                    "and re-read `runs/portfolio/cycle/latest.json` summary."
                )
            if item_category == "approval_needed":
                action += (
                    " **Approval gate:** explicitly decide whether to resume autonomous runs; "
                    "do not restart until you accept portfolio risk."
                )
        elif sr == "quiescence_recommendation":
            if any("quiescence.wait" in c for c in codes):
                tp_label = top_product or "the top-ranked product"
                sctx = signal_contract_context_for_escalation(
                    repo_root,
                    top_product,
                    top_e if isinstance(top_e, dict) else None,
                )
                op_sc = str(sctx.get("operability_status") or "")
                missing_g = list(sctx.get("missing_golden") or [])
                stale_g = list(sctx.get("stale_golden") or [])
                if op_sc == "blocked" and missing_g and top_product:
                    state = (
                        "Automation stopped: the top queue product lacks required operability signals "
                        "(signal contract golden set)."
                    )
                    cause = (
                        f"`{top_product}` is missing required golden signals: {', '.join(missing_g)}. "
                        f"Orchestration status `{orch or 'unknown'}`."
                        + (f" {rec_line[:280]}" if rec_line else "")
                    )
                    action = (
                        f"For `{top_product}`: restore operability — collect or refresh "
                        f"`runs/signals/latest/{top_product}.json` "
                        f"so golden slots are satisfied ({', '.join(missing_g)}); "
                        f"run `argus portfolio signal-contract {top_product}`. "
                        f"Then address orchestration `next_action` if still listed "
                        f"(`{next_act or 'see runs/orchestration/latest/<product>.json'}`)."
                    )
                    if em_hint == "thin_bootstrap":
                        action += (
                            " Evidence is thin — prioritize `signals_collect` / bootstrap artifacts until posture improves."
                        )
                elif op_sc == "limited" and stale_g and top_product:
                    state = (
                        "Automation stopped: the top queue product has stale operability signals (signal contract)."
                    )
                    cause = (
                        f"`{top_product}` has stale golden signals: {', '.join(stale_g)}. "
                        f"Orchestration status `{orch or 'unknown'}`."
                        + (f" {rec_line[:280]}" if rec_line else "")
                    )
                    action = (
                        f"For `{top_product}`: refresh signal collection so golden signals are current; "
                        f"run `argus portfolio signal-contract {top_product}`. "
                        f"Next orchestration action when ready: "
                        f"`{next_act or 'see runs/orchestration/latest/<product>.json'}`."
                    )
                else:
                    state = (
                        "Automation stopped: portfolio quiescence recommends pausing "
                        "(orchestration-listed inputs or external approvals)."
                    )
                    cause = (
                        f"Queue focus `{tp_label}` — orchestration status `{orch or 'unknown'}`."
                        + (f" {rec_line[:280]}" if rec_line else "")
                    )
                    orch_path = (
                        f"`runs/orchestration/latest/{top_product}.json`" if top_product else "orchestration artifacts"
                    )
                    action = (
                        f"For `{tp_label}`: resolve items listed under **waiting_inputs** in {orch_path} "
                        f"(refresh signals/temporal/audit if stale; complete external approvals). "
                        f"Next orchestration action: `{next_act or 'see runs/orchestration/latest/<product>.json'}`."
                    )
                    if em_hint == "thin_bootstrap":
                        action += " Evidence is thin — prioritize `signals_collect` / bootstrap artifacts until posture improves."
            elif any("quiescence.inspect" in c for c in codes):
                state = "Automation stopped: quiescence recommends inspection before more cycles."
                tp = top_product or "top queue product"
                cause = "Quiescence `inspect` class stop — portfolio may need targeted product/import review."
                action = (
                    f"Inspect `{tp}` and peers in `runs/portfolio/cycle/latest.md`; "
                    "address import health and orchestration `next_action` rows before resuming."
                )
            elif any("quiescence.human_review" in c for c in codes):
                state = "Automation stopped: quiescence requests human review."
                cause = "Portfolio state triggered a human-review quiescence code."
                action = "Review operator queue and latest cycle/quiescence artifacts; decide whether to continue automation."
            elif any("import_refresh" in c for c in codes):
                state = "Automation stopped: imports need refresh before blind cycles."
                cause = "Quiescence cited import refresh / repair."
                action = "Repair importer posture for affected products, then re-run portfolio refresh and autonomous session."
            else:
                state = "Automation stopped on a quiescence guardrail."
                cause = f"Codes: {codes!r}"
                action = req
        elif sr == "explicit_stop_sentinel":
            state = "Autonomous session exited because the operator STOP sentinel was present."
            cause = "Expected stop — not an error."
            action = "Remove or touch `runs/portfolio/autonomous_runner/STOP` when you want the service to continue."
        elif sr == "max_cycles_reached":
            state = "Autonomous session finished after reaching the configured max cycle cap."
            cause = "Normal completion without guardrail stop."
            action = "Increase `--max-cycles` if you want longer runs, or start a new session."
        elif sr == "no_material_change_streak":
            state = "Autonomous session noted a no-material-change streak (informational)."
            cause = ev
            action = "If stagnation is unexpected, run portfolio delta and operator queue review; otherwise continue on schedule."
        else:
            state = (
                f"Portfolio scheduler stopped (`{sr}`)."
                if _sched
                else f"Autonomous session stopped (`{sr}`)."
            )
            cause = ev or req
            action = req

    elif src == "portfolio_cycle":
        overall = _parse_cycle_evidence(ev)
        primary = primary or top_product
        state = f"Latest portfolio cycle overall recommendation is `{overall or 'unknown'}` — automation should pause for review."
        cause = (
            f"Cycle synthesis flagged `{overall}` (see `runs/portfolio/cycle/latest.json`). "
            "This is a portfolio-level guardrail, not a single-product bug by itself."
        )
        tp = top_product or "top queue product"
        if overall == "inspect_specific_products":
            action = (
                f"Inspect orchestration for `{tp}` first, then work down the operator queue; "
                "resolve stale refresh, import failures, or findings backlogs called out in the cycle summary."
            )
        elif overall == "repair_imports":
            action = "Repair importer / first-pass issues for products listed in the cycle summary, then re-run cycle."
        elif overall == "request_human_review":
            action = "Perform human review per cycle notes; confirm risk before resuming autonomous promotion or cycles."
        else:
            action = req

    elif src == "intervention_inbox":
        state = "Intervention signal crossed the escalation inbox threshold."
        cause = str(item.get("evidence_summary") or "")[:900] or "Intervention row requires review."
        primary = primary or None
        action = (
            str(item.get("requested_action") or "").strip()
            or "Review the intervention row, then execute the recommended operator action for that product."
        )
        if primary:
            action = f"For `{primary}`: {action}"

    elif src == "blocked_promotion":
        state = "A lifecycle promotion action is blocked."
        cause = str(item.get("evidence_summary") or req)[:900]
        bp = str(item.get("product_id") or "").strip()
        primary = primary or bp or None
        if primary:
            action = (
                f"Resolve the block for `{primary}`: adjust directories, inventory, or proposals per the reason text, "
                "then retry promotion when safe."
            )
        else:
            action = "Resolve the blocked promotion using the reason in evidence_summary, then retry."

    elif src == "manual":
        state = "Manual escalation item (operator-curated)."
        cause = str(item.get("evidence_summary") or req)[:900]
        action = req or "Follow the requested_action for this manual item."

    # Orchestration-aware nudge when top product shows stale refresh (portfolio-level escalations)
    if src in ("autonomous_runner", "portfolio_cycle", "scheduler") and orch == "stale_refresh_needed" and top_product:
        if "stale" not in cause.lower():
            cause += f" Top queue product `{top_product}` has orchestration status `stale_refresh_needed`."
        if "refresh" not in action.lower() and "signals" not in action.lower():
            action += (
                f" Refresh artifacts for `{top_product}` (signals/temporal/audit per orchestration **waiting_inputs**) "
                "so orchestration can advance."
            )

    if str(cat).strip() == "unsafe_to_continue" and str(sev).strip() == "critical":
        state = "Unsafe to continue — critical pipeline failure."
        action = "Stop and fix the failing stage before any further autonomous iterations."

    return {
        "human_readable_state": state.strip(),
        "primary_product": primary,
        "root_cause_summary": cause.strip(),
        "recommended_operator_action": action.strip(),
        "operator_escalation_enrichment_schema": _ENRICHMENT_SCHEMA_NOTE,
    }


def apply_escalation_operator_enrichment(
    item: dict[str, Any],
    *,
    queue_ctx: dict[str, Any],
    repo_root: Path | None = None,
) -> dict[str, Any]:
    """Merge enrichment into a copy of the item (idempotent keys)."""
    out = dict(item)
    enr = derive_escalation_operator_fields(item, queue_ctx=queue_ctx, repo_root=repo_root)
    out.update(enr)
    return out


__all__ = [
    "apply_escalation_operator_enrichment",
    "derive_escalation_operator_fields",
    "load_operator_queue_context",
]
