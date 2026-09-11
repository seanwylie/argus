"""
Portfolio intervention detector — which products need intervention instead of another routine pass.

Deterministic rules over existing artifacts only (operator snapshots, operator queue, progression,
quiescence, delta report history). Thresholds align with :mod:`argus.portfolio.quiescence` where noted.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.policy.operator_policy import default_operator_policy, load_operator_policy
from argus.portfolio.artifact_index import list_timestamped_portfolio_json_files
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA, portfolio_delta_report_dir
from argus.portfolio.evidence_maturity import thin_evidence_baseline_from_fingerprint
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    operator_queue_output_dir,
)
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir
from argus.portfolio.quiescence import (
    PORTFOLIO_QUIESCENCE_SCHEMA,
    fingerprints_for_operator_queue_entries,
    portfolio_quiescence_dir,
)

PORTFOLIO_INTERVENTION_SCHEMA = "argus.portfolio_intervention.v1"

INTERVENTION_IMPORT_REPAIR = "import_repair"
INTERVENTION_EVIDENCE_REFRESH = "evidence_refresh"
INTERVENTION_HUMAN_REVIEW = "human_review"
INTERVENTION_POLICY_TUNING = "policy_tuning"
INTERVENTION_PRODUCT_CLEANUP = "product_cleanup"
INTERVENTION_SAFE_TO_IGNORE = "safe_to_ignore"
INTERVENTION_CONTINUE_MONITORING = "continue_monitoring"

SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"

CHRONICITY_CHRONIC = "chronic"
CHRONICITY_EMERGING = "emerging"

_inv0 = default_operator_policy()["intervention"]
PROGRESSION_RUNS_WINDOW = int(_inv0["progression_runs_window"])
DELTA_REPORTS_WINDOW = int(_inv0["delta_reports_window"])
MIN_CONSECUTIVE_BLOCKED_OUTCOMES = int(_inv0["min_consecutive_blocked_outcomes"])
MIN_TOTAL_BLOCKED_OUTCOMES = int(_inv0["min_total_blocked_outcomes"])
OSCILLATION_MIN_RUNS = int(_inv0["oscillation_min_runs"])
STAGNATION_MIN_DELTA_REPORTS = int(_inv0["stagnation_min_delta_reports"])
TOP_QUEUE_RANK_CUTOFF = int(_inv0["top_queue_rank_cutoff"])
HIGH_PRIORITY_SCORE = float(_inv0["high_priority_score"])
CHRONIC_MIN_RUNS_SPANNED = int(_inv0["chronic_min_runs_spanned"])
EMERGING_MAX_RUNS_SPANNED = int(_inv0["emerging_max_runs_spanned"])

RC_REPEATED_BLOCKED = "intervention.repeated_blocked_progression"
RC_OSCILLATING_ACTION = "intervention.oscillating_next_action"
RC_TIER_DEBT_STAGNATION = "intervention.readiness_tier_debt_stagnation"
RC_IMPORT_NO_RECOVERY = "intervention.import_partial_or_failed_no_recovery"
RC_LOW_CONFIDENCE_LOOP = "intervention.low_decision_confidence_no_improvement"
RC_QUEUE_PROMINENCE_NO_ADVANCE = "intervention.high_queue_rank_no_advancement"
RC_REPEATED_SAME_NEXT_ACTION = "intervention.repeated_same_next_action_no_readiness_gain"


def portfolio_intervention_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "intervention"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ts_json_files(d: Path) -> list[Path]:
    return list_timestamped_portfolio_json_files(d)


def _load_operator_queue(repo_root: Path) -> dict[str, Any] | None:
    p = operator_queue_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == OPERATOR_QUEUE_SCHEMA:
        return raw
    return None


def _recent_progression_payloads(repo_root: Path, limit: int) -> list[dict[str, Any]]:
    files = _ts_json_files(portfolio_progression_dir(repo_root))[:limit]
    out: list[dict[str, Any]] = []
    for p in files:
        raw = _load_json(p)
        if raw is not None and raw.get("schema") == PORTFOLIO_PROGRESSION_SCHEMA:
            out.append(raw)
    return out


def _recent_delta_report_payloads(repo_root: Path, limit: int) -> list[dict[str, Any]]:
    files = _ts_json_files(portfolio_delta_report_dir(repo_root))[:limit]
    out: list[dict[str, Any]] = []
    for p in files:
        raw = _load_json(p)
        if raw is not None and raw.get("schema") == PORTFOLIO_DELTA_REPORT_SCHEMA:
            out.append(raw)
    return out


def _progression_row(payload: dict[str, Any], pid: str) -> dict[str, Any] | None:
    for row in payload.get("products") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("product_id") or "").strip() == pid:
            return row
    return None


def _blocked_outcome(o: str) -> bool:
    return o in ("blocked_waiting", "blocked_approval")


def _consecutive_blocked_prefix(outcomes: list[str]) -> int:
    n = 0
    for o in outcomes:
        if _blocked_outcome(o):
            n += 1
        else:
            break
    return n


def _oscillates_two_values(seq: list[str | None], *, min_runs: int) -> bool:
    cleaned = [str(x) if x is not None else "" for x in seq]
    cleaned = [c for c in cleaned if c and c.lower() not in ("none", "")]
    if len(cleaned) < min_runs:
        return False
    if len(set(cleaned)) != 2:
        return False
    for i in range(len(cleaned) - 1):
        if cleaned[i] == cleaned[i + 1]:
            return False
    return True


def _tier_rank(tier: object) -> int:
    order = {
        "unprofiled": 0,
        "import_incomplete": 1,
        "observe_gap": 2,
        "interpret_gap": 3,
        "advance_ready": 4,
    }
    return order.get(str(tier or "").strip(), -1)


def _severity_rank(s: str) -> int:
    return {"low": 0, "medium": 1, "high": 2}.get(s, 0)


def _recommended_operator_action_evidence_refresh(fp: dict[str, Any]) -> str:
    """
    Actionable copy for evidence_refresh using queue/orchestration fingerprint fields.
    Does not change detection; only tightens operator guidance.
    """
    na = str(fp.get("next_action") or "").strip()
    na_l = na.lower()
    orch = str(fp.get("orchestration_status") or "").strip()
    orch_l = orch.lower()
    tr = str(fp.get("readiness_tier") or "").strip()

    if orch_l == "stale_refresh_needed" or "stale" in orch_l:
        if na_l == "temporal_refresh":
            return (
                "Refresh temporal + signals observability for this product (queue: temporal_refresh; "
                "orchestration: stale_refresh_needed), then re-run portfolio refresh/cycle."
            )
        if "signal" in na_l or na_l == "signals_collect":
            return (
                f"Refresh signals collection/phase (queue next_action: {na!r}; orchestration: {orch!r}), "
                "then re-run portfolio refresh/cycle."
            )
        return (
            f"Refresh stale signals/temporal/audit artifacts (orchestration: {orch!r}; next_action: {na!r}), "
            "then re-run portfolio refresh/cycle."
        )

    if na_l in ("findings_generate", "findings_ingest") or na_l.startswith("findings_"):
        return (
            f"Run findings pipeline steps for this product (queue next_action: {na!r}; orchestration: {orch!r}). "
            f"Readiness tier is {tr!r}; tier advances only with new grounded evidence."
        )
    if "decision" in na_l or na_l.startswith("decisions_"):
        return (
            f"Advance decisions/review for this product (queue next_action: {na!r}; orchestration: {orch!r})."
        )

    return "Refresh signals, temporal, or findings/decisions so readiness can move."


def evaluate_portfolio_intervention(
    repo_root: Path,
    *,
    operator_policy: dict[str, Any] | None = None,
    operator_queue_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pol = operator_policy if operator_policy is not None else load_operator_policy(root)
    invp = pol["intervention"]
    conf_low = float(pol["confidence"]["low_threshold"])
    debt_delta_mat = float(pol["quiescence"]["debt_delta_material"])
    prog_win = int(invp["progression_runs_window"])
    delta_win = int(invp["delta_reports_window"])
    min_consec_blk = int(invp["min_consecutive_blocked_outcomes"])
    min_total_blk = int(invp["min_total_blocked_outcomes"])
    oscillation_min = int(invp["oscillation_min_runs"])
    stagnation_min_dr = int(invp["stagnation_min_delta_reports"])
    top_rank_cut = int(invp["top_queue_rank_cutoff"])
    high_pri = float(invp["high_priority_score"])
    chronic_min_sp = int(invp["chronic_min_runs_spanned"])
    emerging_max_sp = int(invp["emerging_max_runs_spanned"])

    queue = operator_queue_payload if operator_queue_payload is not None else _load_operator_queue(root)
    entries = list(queue.get("entries") or []) if queue else []
    current_fps = fingerprints_for_operator_queue_entries(root, entries) if entries else {}

    prog_runs = _recent_progression_payloads(root, prog_win)
    delta_runs = _recent_delta_report_payloads(root, delta_win)
    quiescence = _load_json(portfolio_quiescence_dir(root) / "latest.json")
    quiescence_ok = bool(
        quiescence and isinstance(quiescence, dict) and quiescence.get("schema") == PORTFOLIO_QUIESCENCE_SCHEMA
    )

    product_ids = sorted(current_fps.keys()) if current_fps else []

    flagged: list[dict[str, Any]] = []
    stable_benign: list[str] = []
    thin_suppressions: list[dict[str, Any]] = []

    for pid in product_ids:
        fp = current_fps.get(pid)
        if not isinstance(fp, dict):
            continue

        thin_baseline = thin_evidence_baseline_from_fingerprint(fp)
        suppressed_for_product: list[str] = []

        codes: list[str] = []
        category = INTERVENTION_CONTINUE_MONITORING
        severity = SEVERITY_LOW
        chronicity = CHRONICITY_EMERGING
        evidence_parts: list[str] = []
        action = "Re-run operator queue / progression on a normal cadence."

        outcomes: list[str] = []
        next_after_seq: list[str | None] = []
        advanced_flags: list[bool] = []
        for pl in prog_runs:
            row = _progression_row(pl, pid)
            if row is None:
                continue
            oc = str(row.get("outcome") or "")
            outcomes.append(oc)
            next_after_seq.append(row.get("next_action_after"))
            advanced_flags.append(oc == "advanced")

        consec_blk = _consecutive_blocked_prefix(outcomes)
        total_blk = sum(1 for o in outcomes if _blocked_outcome(o))
        runs_spanned = len(outcomes)

        if consec_blk >= min_consec_blk:
            codes.append(RC_REPEATED_BLOCKED)
            evidence_parts.append(
                f"blocked progression outcomes {consec_blk}x consecutively (last {runs_spanned} runs with this product)"
            )
            category = INTERVENTION_HUMAN_REVIEW
            severity = SEVERITY_HIGH
        elif total_blk >= min_total_blk:
            codes.append(RC_REPEATED_BLOCKED)
            evidence_parts.append(f"blocked progression outcomes {total_blk}x in last {runs_spanned} runs")
            category = INTERVENTION_HUMAN_REVIEW
            severity = SEVERITY_HIGH

        if _oscillates_two_values(list(reversed(next_after_seq)), min_runs=oscillation_min):
            codes.append(RC_OSCILLATING_ACTION)
            evidence_parts.append("next_action_after alternates between two values across recent progression runs")
            if category == INTERVENTION_CONTINUE_MONITORING:
                category = INTERVENTION_POLICY_TUNING
                severity = SEVERITY_MEDIUM

        nas = [str(x).strip() for x in next_after_seq if x is not None and str(x).strip().lower() not in ("", "none")]
        if len(nas) >= 4 and len(set(nas)) == 1 and fp.get("readiness_tier"):
            if thin_baseline:
                suppressed_for_product.append(RC_REPEATED_SAME_NEXT_ACTION)
            else:
                codes.append(RC_REPEATED_SAME_NEXT_ACTION)
                evidence_parts.append(
                    f"same next_action_after {nas[0]!r} across {len(nas)} runs without readiness change signal"
                )
                if category == INTERVENTION_CONTINUE_MONITORING:
                    category = INTERVENTION_EVIDENCE_REFRESH
                    severity = SEVERITY_MEDIUM

        tier_hist: list[str] = []
        debt_hist: list[float | None] = []
        for dr in delta_runs:
            bl = dr.get("baseline_for_next_run")
            if not isinstance(bl, dict):
                continue
            pp = bl.get("per_product")
            if not isinstance(pp, dict):
                continue
            row = pp.get(pid)
            if not isinstance(row, dict):
                continue
            tier_hist.append(str(row.get("readiness_tier") or ""))
            d = row.get("understanding_debt")
            try:
                debt_hist.append(float(d) if d is not None else None)
            except (TypeError, ValueError):
                debt_hist.append(None)

        if len(tier_hist) >= stagnation_min_dr:
            same_tier = len(set(tier_hist)) == 1 and bool(tier_hist[0])
            debt_flat_or_worse = True
            for i in range(1, len(debt_hist)):
                prev_d, cur_d = debt_hist[i - 1], debt_hist[i]
                if prev_d is None or cur_d is None:
                    debt_flat_or_worse = False
                    break
                if cur_d < prev_d - debt_delta_mat:
                    debt_flat_or_worse = False
                    break
            if same_tier and debt_flat_or_worse and tier_hist[0] != "advance_ready":
                if thin_baseline:
                    suppressed_for_product.append(RC_TIER_DEBT_STAGNATION)
                else:
                    codes.append(RC_TIER_DEBT_STAGNATION)
                    evidence_parts.append(
                        f"readiness_tier {tier_hist[0]!r} unchanged over {len(tier_hist)} delta baselines; debt not improving ≥{debt_delta_mat}"
                    )
                    if category in (INTERVENTION_CONTINUE_MONITORING, INTERVENTION_POLICY_TUNING):
                        category = INTERVENTION_EVIDENCE_REFRESH
                        severity = max(severity, SEVERITY_MEDIUM, key=_severity_rank)

        fps = str(fp.get("first_pass_status") or "").strip().lower()
        gt = str(fp.get("gating_tier") or "").strip().lower()
        if fps in ("partial", "failed") or gt == "failed":
            codes.append(RC_IMPORT_NO_RECOVERY)
            evidence_parts.append(f"import_health first_pass_status={fps!r} gating_tier={gt!r}")
            category = INTERVENTION_IMPORT_REPAIR
            severity = SEVERITY_HIGH

        tc = fp.get("top_decision_confidence")
        try:
            tcf = float(tc) if tc is not None else None
        except (TypeError, ValueError):
            tcf = None
        if tcf is not None and tcf < conf_low and _tier_rank(fp.get("readiness_tier")) < _tier_rank(
            "advance_ready"
        ):
            if thin_baseline:
                suppressed_for_product.append(RC_LOW_CONFIDENCE_LOOP)
            else:
                codes.append(RC_LOW_CONFIDENCE_LOOP)
                evidence_parts.append(f"top_decision_confidence {tcf} below {conf_low} while not advance_ready")
                if category not in (INTERVENTION_IMPORT_REPAIR, INTERVENTION_HUMAN_REVIEW):
                    category = INTERVENTION_HUMAN_REVIEW
                    severity = max(severity, SEVERITY_MEDIUM, key=_severity_rank)

        try:
            qr = int(fp.get("queue_rank")) if fp.get("queue_rank") is not None else 999
        except (TypeError, ValueError):
            qr = 999
        try:
            pscore = float(fp.get("priority_score")) if fp.get("priority_score") is not None else 0.0
        except (TypeError, ValueError):
            pscore = 0.0
        if (
            qr <= top_rank_cut
            and pscore >= high_pri
            and runs_spanned >= 2
            and not any(advanced_flags)
        ):
            codes.append(RC_QUEUE_PROMINENCE_NO_ADVANCE)
            evidence_parts.append(
                f"queue_rank={qr} priority_score={pscore} but no advanced outcome in last {runs_spanned} progression appearances"
            )
            if category == INTERVENTION_CONTINUE_MONITORING:
                category = INTERVENTION_PRODUCT_CLEANUP
                severity = SEVERITY_MEDIUM

        if runs_spanned >= chronic_min_sp or total_blk >= 3:
            chronicity = CHRONICITY_CHRONIC
        elif runs_spanned <= emerging_max_sp and codes:
            chronicity = CHRONICITY_EMERGING

        if suppressed_for_product:
            thin_suppressions.append(
                {
                    "product_id": pid,
                    "suppressed_codes": sorted(set(suppressed_for_product)),
                    "note": (
                        "Thin-evidence bootstrap: unprofiled with first-pass not yet successful — "
                        "stagnation-style codes withheld until first-pass completes."
                    ),
                }
            )

        if category == INTERVENTION_IMPORT_REPAIR:
            action = "Fix importer / first-pass / product.yaml import_state; re-run first-pass evaluation."
        elif category == INTERVENTION_EVIDENCE_REFRESH:
            action = _recommended_operator_action_evidence_refresh(fp)
        elif category == INTERVENTION_HUMAN_REVIEW:
            action = "Human review: refinement input, approvals, or blockers called out in orchestration."
        elif category == INTERVENTION_POLICY_TUNING:
            action = "Review next_action policy / planning soft priority; reduce oscillation."
        elif category == INTERVENTION_PRODUCT_CLEANUP:
            action = "Validate product posture vs queue attention; consider de-prioritizing or fixing root blockers."
        elif category == INTERVENTION_SAFE_TO_IGNORE:
            action = "No action required beyond routine monitoring."

        if not codes:
            orch = str(fp.get("orchestration_status") or "").lower()
            tr = str(fp.get("readiness_tier") or "")
            debt = fp.get("understanding_debt")
            try:
                df = float(debt) if debt is not None else 1.0
            except (TypeError, ValueError):
                df = 1.0
            if (
                tr == "advance_ready"
                and df <= 0.35
                and "blocked" not in orch
                and str(fp.get("next_action") or "none").lower() in ("none", "")
            ):
                stable_benign.append(pid)
            continue

        flagged.append(
            {
                "product_id": pid,
                "intervention_category": category,
                "severity": severity,
                "detection_reason_codes": sorted(set(codes)),
                "evidence_summary": "; ".join(evidence_parts) if evidence_parts else "—",
                "recommended_operator_action": action,
                "chronicity": chronicity,
            }
        )

    stuck_quiescence: list[dict[str, Any]] = []
    if quiescence_ok:
        stuck_quiescence = list(quiescence.get("products_stuck_or_repeating") or [])
    extra_pids = {str(x.get("product_id")) for x in stuck_quiescence if isinstance(x, dict)}
    existing = {str(x.get("product_id")) for x in flagged if isinstance(x, dict)}
    for pid in sorted(extra_pids - existing):
        flagged.append(
            {
                "product_id": pid,
                "intervention_category": INTERVENTION_HUMAN_REVIEW,
                "severity": SEVERITY_HIGH,
                "detection_reason_codes": ["intervention.quiescence_stuck_repeat_progression"],
                "evidence_summary": "Same blocked progression outcome in two consecutive stamped runs (quiescence)",
                "recommended_operator_action": "Human review: unblock progression or adjust advance guardrails.",
                "chronicity": CHRONICITY_CHRONIC,
            }
        )

    flagged.sort(key=lambda x: (str(x.get("severity")), str(x.get("product_id"))))

    return {
        "schema": PORTFOLIO_INTERVENTION_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "inputs": {
            "operator_queue_present": queue is not None,
            "progression_runs_considered": len(prog_runs),
            "delta_reports_considered": len(delta_runs),
            "quiescence_present": quiescence_ok,
        },
        "thresholds": {
            "PROGRESSION_RUNS_WINDOW": prog_win,
            "DELTA_REPORTS_WINDOW": delta_win,
            "MIN_CONSECUTIVE_BLOCKED_OUTCOMES": min_consec_blk,
            "MIN_TOTAL_BLOCKED_OUTCOMES": min_total_blk,
            "OSCILLATION_MIN_RUNS": oscillation_min,
            "STAGNATION_MIN_DELTA_REPORTS": stagnation_min_dr,
            "DEBT_DELTA_MATERIAL": debt_delta_mat,
            "CONFIDENCE_LOW_THRESHOLD": conf_low,
            "TOP_QUEUE_RANK_CUTOFF": top_rank_cut,
            "HIGH_PRIORITY_SCORE": high_pri,
        },
        "flagged_products": flagged,
        "stable_benign_products": sorted(stable_benign),
        "thin_evidence_suppressions": thin_suppressions,
    }


def render_portfolio_intervention_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio intervention",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Inputs",
        "",
        f"- Progression runs read: **{payload.get('inputs', {}).get('progression_runs_considered')}**",
        f"- Delta reports read: **{payload.get('inputs', {}).get('delta_reports_considered')}**",
        f"- Quiescence present: **{payload.get('inputs', {}).get('quiescence_present')}**",
        "",
        "## Flagged products",
        "",
        "| Product | Category | Severity | Chronicity | Reason codes |",
        "|---------|----------|----------|------------|--------------|",
    ]
    for row in payload.get("flagged_products") or []:
        if not isinstance(row, dict):
            continue
        codes = ", ".join(f"`{c}`" for c in (row.get("detection_reason_codes") or []))
        lines.append(
            f"| `{row.get('product_id')}` | `{row.get('intervention_category')}` | "
            f"`{row.get('severity')}` | `{row.get('chronicity')}` | {codes} |"
        )
    if not (payload.get("flagged_products") or []):
        lines.append("| — | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Details",
            "",
        ]
    )
    for row in payload.get("flagged_products") or []:
        if not isinstance(row, dict):
            continue
        lines.append(f"### `{row.get('product_id')}`")
        lines.append("")
        lines.append(str(row.get("evidence_summary") or "—"))
        lines.append("")
        lines.append(f"**Recommended:** {row.get('recommended_operator_action')}")
        lines.append("")
    sup = payload.get("thin_evidence_suppressions") or []
    if sup:
        lines.extend(
            [
                "## Thin-evidence suppressions",
                "",
                "Stagnation-style codes withheld while first-pass bootstrap is incomplete (unprofiled + non-success first_pass).",
                "",
            ]
        )
        for row in sup:
            if not isinstance(row, dict):
                continue
            pid = row.get("product_id")
            codes = ", ".join(f"`{c}`" for c in (row.get("suppressed_codes") or []))
            lines.append(f"- `{pid}`: {codes or '—'}")
        lines.append("")
    lines.extend(
        [
            "## Stable (benign) products",
            "",
            ", ".join(f"`{p}`" for p in (payload.get("stable_benign_products") or [])) or "—",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_intervention_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = portfolio_intervention_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_intervention_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_intervention(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_intervention(repo_root)
    if write_artifacts:
        write_portfolio_intervention_artifacts(repo_root, payload)
    return payload
