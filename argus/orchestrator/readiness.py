"""
Deterministic readiness model for ``argus.orchestration_state.v1``.

Computes explicit readiness fields from existing artifacts and eligibility facts only
(no LLM, no hidden heuristics beyond documented weights).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.decision.persistence import latest_product_path as decisions_latest_path
from argus.policy.operator_policy import load_operator_policy

ORCHESTRATION_READINESS_SCHEMA = "argus.orchestration_readiness.v1"

# Stable reason codes (prefix: readiness.)
RC_NO_IMPORT_STATE = "readiness.no_import_state"
RC_FIRST_PASS_FAILED = "readiness.first_pass_failed"
RC_FIRST_PASS_PARTIAL = "readiness.first_pass_partial"
RC_FIRST_PASS_SKIPPED = "readiness.first_pass_skipped"
RC_FIRST_PASS_PENDING = "readiness.first_pass_pending"
RC_FIRST_PASS_UNKNOWN = "readiness.first_pass_unknown"
RC_SIGNALS_ABSENT_OR_STALE = "readiness.signals_absent_or_stale"
RC_SPINE_INCOMPLETE = "readiness.spine_incomplete_findings_or_decisions"
RC_AUDIT_GAP_OR_STUB = "readiness.audit_gap_or_security_stub"
RC_TEMPORAL_FRESHNESS_STALE = "readiness.temporal_freshness_stale"
RC_DECISION_CONFIDENCE_LOW = "readiness.decision_top_confidence_below_threshold"
RC_ADVISOR_CONFLICT = "readiness.decision_advisor_conflict"
RC_WAITING_INPUTS = "readiness.waiting_inputs_non_empty"
RC_IMPORT_WAITING = "readiness.import_first_pass_waiting"


def _load_top_decision_confidence(root: Path, product_id: str) -> tuple[float | None, bool]:
    """Return (top candidate confidence or None, advisor_conflict from decision_context)."""
    p = decisions_latest_path(root, product_id)
    if not p.is_file():
        return None, False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None, False
    if not isinstance(raw, dict):
        return None, False
    cands = raw.get("candidates")
    conf: float | None = None
    if isinstance(cands, list) and cands:
        first = cands[0]
        if isinstance(first, dict):
            v = first.get("confidence")
            if isinstance(v, (int, float)):
                conf = float(v)
    ctx = raw.get("decision_context")
    adv_conflict = False
    if isinstance(ctx, dict):
        adv_conflict = bool(ctx.get("advisor_conflict_flag"))
    return conf, adv_conflict


def _waiting_has_import(wi: list[dict[str, Any]]) -> bool:
    for w in wi:
        if not isinstance(w, dict):
            continue
        if str(w.get("kind") or "") == "import_first_pass":
            return True
    return False


def build_readiness_section(
    *,
    product_id: str,
    root: Path,
    import_state: dict[str, Any] | None,
    import_readiness_tier: str | None,
    import_health: dict[str, Any],
    artifacts: dict[str, Any],
    eligibility_facts: dict[str, Any],
    waiting_inputs: list[dict[str, Any]],
    orchestration_status: str,
    next_action: str,
    confidence_threshold: float | None = None,
    operator_policy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build the ``readiness`` object for orchestration state.

    Fields:
    - ``schema``: argus.orchestration_readiness.v1
    - ``readiness_tier``: coarse ladder (see module docstring).
    - ``understanding_debt``: 0.0 (low debt) .. 1.0 (high debt).
    - ``confidence_gate``: open | caution | blocked
    - ``readiness_reason_codes``: sorted unique stable codes
    - ``readiness_policy_hint``: short string hint (distinct from payload ``next_action_policy`` object)
    - ``progression_quiescence``: true when no next automation action and not waiting on import
    - ``metrics``: decision confidence snapshot for the gate (see return payload)

    ``confidence_threshold`` defaults to :func:`load_operator_policy` ``confidence.low_threshold``.
    """
    op_policy = (
        operator_policy
        if operator_policy is not None
        else load_operator_policy(root, product_id=product_id)
    )
    conf_th = (
        float(confidence_threshold)
        if confidence_threshold is not None
        else float(op_policy["confidence"]["low_threshold"])
    )
    rpol = op_policy["readiness"]
    di = rpol["debt_increments"]
    gate_debt = float(rpol["gate_debt_caution"])
    hint_debt = float(rpol["policy_hint_debt_stabilize"])
    w_cap = float(di["waiting_debt_cap"])
    w_unit = float(di["waiting_per_item_unit"])

    codes: list[str] = []
    debt = 0.0

    fps = None
    if import_state and isinstance(import_state.get("first_pass_status"), str):
        fps = str(import_state["first_pass_status"]).strip().lower()

    if import_state is None:
        codes.append(RC_NO_IMPORT_STATE)
        debt += float(di["no_import_state"])
    elif fps in ("pending",):
        codes.append(RC_FIRST_PASS_PENDING)
        debt += float(di["first_pass_pending"])
    elif fps == "failed":
        codes.append(RC_FIRST_PASS_FAILED)
        debt += float(di["first_pass_failed"])
    elif fps == "partial":
        codes.append(RC_FIRST_PASS_PARTIAL)
        debt += float(di["first_pass_partial"])
    elif fps == "skipped":
        codes.append(RC_FIRST_PASS_SKIPPED)
        debt += float(di["first_pass_skipped"])
    elif fps not in ("success", None) and str(fps or "").lower() not in ("success",):
        codes.append(RC_FIRST_PASS_UNKNOWN)
        debt += float(di["first_pass_unknown"])

    sig_art = artifacts.get("signals") if isinstance(artifacts.get("signals"), dict) else {}
    signals_stale = bool(eligibility_facts.get("signals_collection_time_stale"))
    signals_refresh = bool(eligibility_facts.get("signals_refresh_needed"))
    temporal_stale = bool(eligibility_facts.get("temporal_freshness_stale"))
    audit_gap = bool(eligibility_facts.get("audit_product_gap_incomplete"))
    audit_stub = bool(eligibility_facts.get("audit_security_stub"))

    findings_ok = bool(import_health.get("findings_present"))
    decisions_ok = bool(import_health.get("decisions_present"))

    if signals_stale or signals_refresh or sig_art.get("phase") == "absent":
        codes.append(RC_SIGNALS_ABSENT_OR_STALE)
        debt += float(di["signals_absent_or_stale"])
    if not findings_ok or not decisions_ok:
        codes.append(RC_SPINE_INCOMPLETE)
        debt += float(di["spine_incomplete"])
    if audit_gap or audit_stub:
        codes.append(RC_AUDIT_GAP_OR_STUB)
        debt += float(di["audit_gap_or_stub"])
    if temporal_stale:
        codes.append(RC_TEMPORAL_FRESHNESS_STALE)
        debt += float(di["temporal_freshness_stale"])

    top_conf, adv_conflict = _load_top_decision_confidence(root, product_id)
    if top_conf is not None and top_conf < conf_th:
        codes.append(RC_DECISION_CONFIDENCE_LOW)
        debt += float(di["decision_confidence_low"])
    if adv_conflict:
        codes.append(RC_ADVISOR_CONFLICT)
        debt += float(di["advisor_conflict"])

    if waiting_inputs:
        codes.append(RC_WAITING_INPUTS)
        debt += min(w_cap, w_unit * len(waiting_inputs))
    if _waiting_has_import(waiting_inputs):
        codes.append(RC_IMPORT_WAITING)
        debt += float(di["import_waiting"])

    debt = min(1.0, round(debt, 4))

    tier = "unprofiled"
    if import_state is not None:
        if fps is None or str(fps).strip() == "":
            tier = "unprofiled"
        elif import_readiness_tier in ("failed", "partial", "skipped") or fps in ("failed", "partial", "skipped"):
            tier = "import_incomplete"
        elif signals_stale or signals_refresh or sig_art.get("phase") == "absent" or not findings_ok:
            tier = "observe_gap"
        elif not decisions_ok or audit_gap or audit_stub or temporal_stale:
            tier = "interpret_gap"
        else:
            tier = "advance_ready"

    # confidence_gate
    gate = "open"
    if import_state is None:
        gate = "caution"
    if import_readiness_tier == "failed" or fps == "failed":
        gate = "blocked"
    elif import_readiness_tier in ("partial", "skipped") or fps in ("partial", "skipped"):
        gate = "caution"
    elif signals_stale or signals_refresh or not findings_ok or not decisions_ok:
        gate = "caution"
    if gate != "blocked" and debt >= gate_debt:
        gate = "caution"
    if gate == "open" and (top_conf is not None and top_conf < conf_th):
        gate = "caution"

    # Policy hint (string) — distinct from orchestration ``next_action_policy`` object on state payload.
    if fps in ("failed", "partial", "skipped") or import_readiness_tier in ("failed", "partial", "skipped"):
        policy_hint = "complete_import_first_pass"
    elif signals_stale or signals_refresh:
        policy_hint = "refresh_observability"
    elif temporal_stale:
        policy_hint = "refresh_temporal"
    elif audit_gap or audit_stub:
        policy_hint = "close_audit_gaps"
    elif gate == "caution" and debt >= hint_debt:
        policy_hint = "stabilize_interpretation"
    else:
        policy_hint = "advance_when_eligible"

    _blocked_statuses = frozenset({"blocked_waiting_input", "blocked_waiting_approval"})
    quiescent = (
        str(next_action or "").strip().lower() in ("none", "")
        and not _waiting_has_import(waiting_inputs)
        and str(orchestration_status or "").strip() not in _blocked_statuses
    )

    reason_codes = sorted(set(codes))

    return {
        "schema": ORCHESTRATION_READINESS_SCHEMA,
        "readiness_tier": tier,
        "understanding_debt": debt,
        "confidence_gate": gate,
        "readiness_reason_codes": reason_codes,
        "readiness_policy_hint": policy_hint,
        "progression_quiescence": quiescent,
        "metrics": {
            "top_decision_confidence": top_conf,
            "advisor_conflict": adv_conflict,
            "confidence_threshold": conf_th,
        },
    }
