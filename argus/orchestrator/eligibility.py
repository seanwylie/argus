"""Deterministic eligibility from artifact snapshots (no network, no LLM)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from argus.autonomy.quotas import check_experiment_create_allowed
from argus.decision.persistence import latest_product_path as decisions_latest_path
from argus.experiments.execution_apply import pending_unapplied_execution_outcomes_for_product
from argus.experiments.models import ExperimentStatus
from argus.experiments.prioritize import prioritization_latest_has_ranked_for_product
from argus.experiments.registry import can_transition
from argus.experiments.store import list_experiments
from argus.findings.persistence import latest_path as findings_latest_path
from argus.importer.import_state import extract_import_state, load_product_yaml_dict
from argus.orchestrator import eligibility_watermarks as _elig_wm
from argus.orchestrator.artifact_paths import (
    experiments_proposals_latest_product_path,
    ideas_bundle_latest_path,
)
from argus.orchestrator.artifact_snapshot import (
    AuditSnapshot,
    ExecutionSnapshot,
    SignalsSnapshot,
    TemporalSnapshot,
    list_refinement_sessions_for_product,
    load_audit_snapshot,
    load_execution_snapshot,
    load_pending_approvals_for_product,
    load_signals_snapshot,
    load_temporal_snapshot,
    parse_iso_timestamp,
    pick_latest_session,
    utc_now,
)
from argus.orchestrator.execution_feedback import load_orchestration_feedback_summary
from argus.orchestrator.readiness import build_readiness_section
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_CONSIDER,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXECUTION_OUTCOMES_APPLY,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_TEMPORAL_REFRESH,
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_COMPLETE,
    ORCH_STATUS_ELIGIBLE,
    ORCH_STATUS_ESCALATED,
    ORCH_STATUS_STALE_REFRESH_NEEDED,
    ORCHESTRATION_POSTURE_SCHEMA,
    PHASE_ABSENT,
    PHASE_FINALIZED,
    PHASE_FRESH,
    PHASE_IN_PROGRESS,
    PHASE_NONE,
    PHASE_PRESENT,
    PHASE_STALE,
    PHASE_TERMINAL_REJECTED,
    REFINEMENT_REVIEW_ABSENT,
    REFINEMENT_REVIEW_AWAITING_REVIEW_INPUT,
    REFINEMENT_REVIEW_FINALIZED,
    REFINEMENT_REVIEW_HUMAN_REVIEW_REQUIRED,
    REFINEMENT_REVIEW_IN_PROGRESS,
    REFINEMENT_REVIEW_NOT_CONVERGED_STUCK,
    REFINEMENT_REVIEW_REJECTED,
    STATUS_BLOCKED_EXHAUSTED,
    STATUS_BLOCKED_WAITING_INPUT,
    STATUS_ELIGIBLE,
    STATUS_NO_ACTION,
    WAITING_KIND_EXECUTION_APPROVAL,
    WAITING_KIND_IMPL_PLAN_PREREQUISITE,
    WAITING_KIND_IMPORT_FIRST_PASS,
    WAITING_KIND_OBSERVABILITY_AUDIT,
    WAITING_KIND_OBSERVABILITY_SIGNALS,
    WAITING_KIND_OBSERVABILITY_TEMPORAL,
    WAITING_KIND_REFINEMENT_GROUNDED_INPUT,
)
from argus.planning.snapshot import planning_latest_path
from argus.refinement.models import ArtifactType, RefinementSessionSnap, SessionStatus
from argus.refinement.queries import (
    refinement_cycle_incomplete,
    refinement_not_converged_stuck,
    refinement_reviews_in_missing,
    reviews_in_path,
    reviews_path,
    reviews_round_has_blocking_grounded,
)
from argus.signals.persistence import latest_path as signals_latest_path
from argus.strategy.snapshot import strategy_latest_path

SIGNAL_STALE_HOURS = 48.0
AUDIT_STALE_DAYS = 7.0
REVIEW_INPUT_WAIT_ESCALATION_HOURS = 72.0

# Machine-readable reason codes (stable strings for automation).
RC_SIGNALS_TIME_STALE = "signals_collection_time_stale"
RC_TEMPORAL_WORST_STALE = "temporal_worst_freshness_stale"
RC_TEMPORAL_LATEST_ABSENT = "temporal_latest_bundle_absent"
RC_AUDIT_TIME_STALE = "audit_bundle_time_stale"
RC_AUDIT_PRODUCT_GAP_INCOMPLETE = "audit_product_gap_stub_or_missing"
RC_AUDIT_PRODUCT_GAP_PARTIAL = "audit_product_gap_partial"
RC_AUDIT_SECURITY_STUB = "audit_security_stub"
RC_REVIEW_INPUT_WAIT_EXCEEDED = "review_input_wait_exceeded"
RC_COMPOUND_CRITICAL_ARTIFACTS = "compound_critical_artifact_gaps"
RC_CONVERGENCE_BLOCKING_GROUNDED = "convergence_failed_blocking_grounded_reviews"
RC_APPROVAL_PENDING = "approval_pending_execution_gate"
RC_SIGNALS_BUNDLE_ABSENT = "signals_latest_bundle_absent"
RC_ORCH_FEEDBACK_FAILED = "orchestration_execution_feedback_failed"
RC_ORCH_FEEDBACK_QUEUED_UNHANDLED = "orchestration_execution_feedback_queued_unhandled"
RC_ORCH_SAME_ACTION_AFTER_SUCCESS = "orchestration_same_action_after_executed_success"
RC_ORCH_FAILED_ACTION_DEPRIORITIZED = "orchestration_failed_action_deprioritized"
RC_ORCH_UNHANDLED_ACTION_DEPRIORITIZED = "orchestration_unhandled_action_deprioritized"
RC_ORCH_FEEDBACK_REPEATED_FAILURES = "orchestration_execution_feedback_repeated_failures"
RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED = "orchestration_execution_feedback_repeated_queued_unhandled"
RC_ORCH_RETRY_REOPENED = "orchestration_retry_reopened"
RC_FINDINGS_GENERATE_SIGNALS_READY = "findings_generate_signals_ready"
RC_DECISIONS_GENERATE_FINDINGS_READY = "decisions_generate_findings_ready"
RC_IDEAS_GENERATE_DECISIONS_READY = "ideas_generate_decisions_ready"
RC_EXPERIMENTS_PROPOSE_READY = "experiments_propose_ready"
RC_EXPERIMENTS_PRIORITIZE_READY = "experiments_prioritize_ready"
RC_EXPERIMENTS_CREATE_READY = "experiments_create_ready"
RC_EXPERIMENTS_ACTIVATE_READY = "experiments_activate_ready"
RC_EXPERIMENTS_EVALUATE_READY = "experiments_evaluate_ready"
RC_EXPERIMENTS_CLOSE_STALE_READY = "experiments_close_stale_ready"
RC_EXPERIMENTS_SURFACE_FINDINGS_READY = "experiments_surface_findings_ready"
RC_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS_READY = "decisions_refresh_from_surfaced_findings_ready"
RC_IDEAS_REFRESH_FROM_SURFACED_FINDINGS_READY = "ideas_refresh_from_surfaced_findings_ready"
RC_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION_READY = "strategy_refresh_from_decision_evolution_ready"
RC_PLANNING_REFRESH_FROM_STRATEGY_READY = "planning_refresh_from_strategy_ready"
RC_REFINEMENT_START_IDEA_READY = "refinement_start_idea_ready"
RC_ESCALATION_PACKET_GENERATE_POSTURE = "escalation_packet_generate_posture_ready"
RC_IMPORT_FIRST_PASS_FAILED = "import_first_pass_failed"
RC_IMPORT_FIRST_PASS_PARTIAL = "import_first_pass_partial"

# Repeated per-action outcomes in execution-feedback lookback (see load_orchestration_feedback_summary).
EXECUTION_FEEDBACK_REPEAT_FAIL_THRESHOLD = 2
EXECUTION_FEEDBACK_REPEAT_QUEUED_UNHANDLED_THRESHOLD = 2

# Implementation plan progression (product_spec → implementation_plan), deterministic gates.
RC_PRODUCT_SPEC_FINALIZED_NO_IMPL_PLAN = "product_spec_finalized_no_implementation_plan"
RC_IMPL_PLAN_BLOCKED_REFINEMENT_WAITING = "implementation_plan_generate_blocked_refinement_waiting"
RC_IMPL_PLAN_BLOCKED_SIGNALS_STALE = "implementation_plan_generate_blocked_signals_or_temporal_stale"
RC_IMPL_PLAN_BLOCKED_AUDIT_STALE = "implementation_plan_generate_blocked_audit_stale"
RC_IMPL_PLAN_BLOCKED_AUDIT_PRODUCT_GAP = "implementation_plan_generate_blocked_audit_product_gap_incomplete"
RC_IMPL_PLAN_BLOCKED_AUDIT_SECURITY_STUB = "implementation_plan_generate_blocked_audit_security_stub"


@dataclass
class EligibleAction:
    action_id: str
    reason: str
    reason_codes: tuple[str, ...] = ()


@dataclass
class _OrchestrationEvalContext:
    """Mutable pipeline state for :func:`evaluate_product_orchestration` stages."""

    root: Path
    product_id: str
    now: datetime
    now_iso: str
    sig: SignalsSnapshot
    temp: TemporalSnapshot
    aud: AuditSnapshot
    ex: ExecutionSnapshot
    pending_approvals: list[dict[str, Any]]
    sessions: list[RefinementSessionSnap]
    ps_sess: RefinementSessionSnap | None
    ip_sess: RefinementSessionSnap | None
    artifacts: dict[str, Any] = field(default_factory=dict)
    eligible: list[EligibleAction] = field(default_factory=list)
    blockers: list[dict[str, Any]] = field(default_factory=list)
    waiting: bool = False
    impl_elig: bool = False
    impl_codes: tuple[str, ...] = ()
    ps_final: bool = False
    ip_absent: bool = True
    progression_impl_plan: dict[str, Any] = field(default_factory=dict)
    compound_dimensions: int = 0
    deduped: list[EligibleAction] = field(default_factory=list)
    human_exhausted: bool = False
    stuck: bool = False
    rejected: bool = False
    overall: str = STATUS_NO_ACTION
    eligibility_facts: dict[str, Any] = field(default_factory=dict)
    escalation_triggers: list[dict[str, Any]] = field(default_factory=list)
    escalation_eligible: bool = False
    stale_refresh_needed: bool = False
    orch_escalated: bool = False
    approval_pending: bool = False
    orch_status: str = ""
    orch_reason: str = ""
    waiting_inputs: list[dict[str, Any]] = field(default_factory=list)
    orchestration_status_reason_codes: list[str] = field(default_factory=list)
    next_action: str = "none"
    orchestration_feedback_summary: dict[str, Any] = field(default_factory=dict)
    orchestration_feedback_waiting_inputs: list[dict[str, Any]] = field(default_factory=list)
    orchestration_failed_deprioritize_suppressed_action_ids: list[str] = field(default_factory=list)
    eligible_actions_order_rule: str | None = None
    import_state: dict[str, Any] | None = None
    import_readiness_tier: str | None = None
    import_health: dict[str, Any] = field(default_factory=dict)
    readiness_reason: str = ""
    import_orchestration_status_override: tuple[str, str] | None = None
    import_waiting_inputs_extra: list[dict[str, Any]] = field(default_factory=list)
    next_action_policy: dict[str, Any] = field(default_factory=dict)


def _load_import_state(root: Path, product_id: str) -> dict[str, Any] | None:
    path = root / "products" / product_id / "product.yaml"
    raw, err = load_product_yaml_dict(path)
    if err or raw is None:
        return None
    return extract_import_state(raw)


def _import_gating_tier(import_state: dict[str, Any] | None) -> str | None:
    """
    Tier string for readiness gating, or None when ``import_state`` is absent / has no usable status
    (orchestration behavior matches pre-import gating).
    """
    if not import_state:
        return None
    raw = import_state.get("first_pass_status")
    if raw is None or str(raw).strip() == "":
        return None
    s = str(raw).strip().lower()
    if s in ("success", "failed", "partial", "skipped"):
        return s
    return "unknown"


# Inspectable ordering when ``execution_outcomes_apply`` is eligible with pending files.
ELIGIBLE_ORDER_RULE_EOA_PENDING_BASE = "eligible_actions_order_execution_outcomes_apply_pending_v1"
ELIGIBLE_ORDER_RULE_EOA_AFTER_RIN = f"{ELIGIBLE_ORDER_RULE_EOA_PENDING_BASE}_after_refinement_submit_reviews_in"
ELIGIBLE_ORDER_RULE_EOA_AFTER_IMPL = f"{ELIGIBLE_ORDER_RULE_EOA_PENDING_BASE}_after_implementation_plan_generate"
ELIGIBLE_ORDER_RULE_EOA_FIRST = f"{ELIGIBLE_ORDER_RULE_EOA_PENDING_BASE}_first"

# When signals are time-fresh but temporal is missing or worst-FR stale, prefer recomputing temporal
# from ``runs/signals/latest`` before ``signals_collect`` (broader adapter run).
ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS = (
    "eligible_actions_order_temporal_refresh_before_signals_collect_v1"
)

# Phase-2: canonical observe → interpret → decide → ideas → govern ordering among handled chain ids
# (``signals_collect`` participates when stale-collection refresh is needed before downstream analysis).
ELIGIBLE_ORDER_RULE_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN = (
    "eligible_actions_order_phase2_observe_interpret_decide_govern_v1"
)

# Semicolon-separated segments in ``eligible_actions_order_rule`` (stage order: EOA pending,
# temporal vs signals, then Phase-2 chain).
ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR = ";"


def _append_eligible_actions_order_rule(ctx: _OrchestrationEvalContext, segment: str) -> None:
    """Append a rule segment after prior segments (deterministic, oldest segment leftmost)."""
    if ctx.eligible_actions_order_rule:
        ctx.eligible_actions_order_rule = (
            f"{ctx.eligible_actions_order_rule}{ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR}{segment}"
        )
    else:
        ctx.eligible_actions_order_rule = segment


# -----------------------------------------------------------------------------
# State hygiene: ``eligible_actions`` ordering (deterministic, inspectable).
#
# Applied immediately after duplicate ``action_id`` merge and **before** execution-feedback
# deprioritization (so Phase-2 canonical ordering does not undo ``executed`` tail moves).
#
# Stages run in a fixed sequence. Stage 1 sets the first ``eligible_actions_order_rule`` segment
# when it reorders. Stages 2–3 may append further segments. No other reordering hooks belong here.
# -----------------------------------------------------------------------------

# Fixed order for Phase-2 progression when multiple chain members are simultaneously eligible.
# ``refinement_start_idea`` follows ``ideas_generate`` (same ideas bundle) and precedes escalation packet.
_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN_ORDER: tuple[str, ...] = (
    ACTION_SIGNALS_COLLECT,
    ACTION_FINDINGS_GENERATE,
    ACTION_DECISIONS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_ESCALATION_PACKET_GENERATE,
)


def _state_hygiene_order_phase2_observe_interpret_decide_govern(
    ctx: _OrchestrationEvalContext,
) -> list[EligibleAction]:
    """
    Stage 3: place ``signals_collect``, ``findings_generate``, ``decisions_generate``,
    ``ideas_generate``, ``refinement_start_idea``, ``escalation_packet_generate`` in canonical pipeline order
    at the first chain-member index.

    Does not remove or add eligibility; only normalizes relative order among these ids (deterministic).
    """
    deduped = list(ctx.deduped)
    chain_set = frozenset(_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN_ORDER)
    indices = [i for i, a in enumerate(deduped) if a.action_id in chain_set]
    if len(indices) < 2:
        return deduped
    idx_first = min(indices)
    ordered_chain: list[EligibleAction] = []
    picked: set[str] = set()
    for aid in _PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN_ORDER:
        for a in deduped:
            if a.action_id == aid and aid not in picked:
                ordered_chain.append(a)
                picked.add(aid)
                break
    if not ordered_chain:
        return deduped
    tail = [a for a in deduped[idx_first:] if a.action_id not in chain_set]
    head = deduped[:idx_first]
    out = head + ordered_chain + tail
    if out != deduped:
        _append_eligible_actions_order_rule(ctx, ELIGIBLE_ORDER_RULE_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN)
    return out


def _apply_state_hygiene_eligible_actions_order(ctx: _OrchestrationEvalContext) -> None:
    """Run staged reorderings; populate ``ctx.deduped`` and ``ctx.eligible_actions_order_rule``."""
    ctx.eligible_actions_order_rule = None
    ctx.deduped = _state_hygiene_order_execution_outcomes_apply_pending(ctx)
    ctx.deduped = _state_hygiene_order_temporal_refresh_before_signals_collect(ctx)
    ctx.deduped = _state_hygiene_order_phase2_observe_interpret_decide_govern(ctx)


def _state_hygiene_order_temporal_refresh_before_signals_collect(ctx: _OrchestrationEvalContext) -> list[EligibleAction]:
    """
    Stage 2: move ``temporal_refresh`` before ``signals_collect`` when both are eligible and
    :func:`_eligible_temporal_refresh` holds (signals not time-stale — direct path).

    Runs after :func:`_state_hygiene_order_execution_outcomes_apply_pending`; does not reorder
    unrelated rows except the relative placement of those two action_ids.
    """
    deduped = list(ctx.deduped)
    if not _eligible_temporal_refresh(ctx.sig, ctx.temp, ctx.now):
        return deduped
    tr_rows = [a for a in deduped if a.action_id == ACTION_TEMPORAL_REFRESH]
    sc_rows = [a for a in deduped if a.action_id == ACTION_SIGNALS_COLLECT]
    if not tr_rows or not sc_rows:
        return deduped
    idx_tr = next(i for i, a in enumerate(deduped) if a.action_id == ACTION_TEMPORAL_REFRESH)
    idx_sc = next(i for i, a in enumerate(deduped) if a.action_id == ACTION_SIGNALS_COLLECT)
    if idx_tr < idx_sc:
        return deduped
    cut = min(idx_tr, idx_sc)
    pos = sum(
        1
        for i in range(cut)
        if deduped[i].action_id not in (ACTION_SIGNALS_COLLECT, ACTION_TEMPORAL_REFRESH)
    )
    without = [a for a in deduped if a.action_id not in (ACTION_SIGNALS_COLLECT, ACTION_TEMPORAL_REFRESH)]
    out = without[:pos] + [tr_rows[0], sc_rows[0]] + without[pos:]
    _append_eligible_actions_order_rule(ctx, ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS)
    return out


def _state_hygiene_order_execution_outcomes_apply_pending(ctx: _OrchestrationEvalContext) -> list[EligibleAction]:
    """
    Stage 1: move ``execution_outcomes_apply`` earlier when unapplied execution JSON exists, without
    displacing refinement review submission (waiting) or implementation_plan_generate when
    that progression gate is active (same conditions as ``next_action`` prefer_impl).
    """
    deduped = list(ctx.deduped)
    if not pending_unapplied_execution_outcomes_for_product(ctx.root, ctx.product_id):
        return deduped
    eoa = [a for a in deduped if a.action_id == ACTION_EXECUTION_OUTCOMES_APPLY]
    if not eoa:
        return deduped
    rest = [a for a in deduped if a.action_id != ACTION_EXECUTION_OUTCOMES_APPLY]

    if ctx.waiting and any(a.action_id == ACTION_REFINEMENT_SUBMIT_REVIEWS_IN for a in deduped):
        rin = [a for a in deduped if a.action_id == ACTION_REFINEMENT_SUBMIT_REVIEWS_IN]
        rest_wo = [a for a in rest if a.action_id != ACTION_REFINEMENT_SUBMIT_REVIEWS_IN]
        ctx.eligible_actions_order_rule = ELIGIBLE_ORDER_RULE_EOA_AFTER_RIN
        return rin + eoa + rest_wo

    prefer_impl = (
        any(a.action_id == ACTION_IMPLEMENTATION_PLAN_GENERATE for a in deduped)
        and not ctx.waiting
        and not ctx.stuck
        and not ctx.human_exhausted
        and not ctx.rejected
    )
    if prefer_impl:
        impl = [a for a in deduped if a.action_id == ACTION_IMPLEMENTATION_PLAN_GENERATE]
        rest_wo = [a for a in rest if a.action_id != ACTION_IMPLEMENTATION_PLAN_GENERATE]
        ctx.eligible_actions_order_rule = ELIGIBLE_ORDER_RULE_EOA_AFTER_IMPL
        return impl + eoa + rest_wo

    ctx.eligible_actions_order_rule = ELIGIBLE_ORDER_RULE_EOA_FIRST
    return eoa + rest


def _is_stale_signals(s: SignalsSnapshot, now) -> bool:
    if not s.present or not s.collected_at_utc:
        return True
    t = parse_iso_timestamp(s.collected_at_utc)
    if t is None:
        return True
    return now - t > timedelta(hours=SIGNAL_STALE_HOURS)


def _is_stale_audit(a: AuditSnapshot, now) -> bool:
    if not a.present or not a.generated_at_utc:
        return True
    t = parse_iso_timestamp(a.generated_at_utc)
    if t is None:
        return True
    return now - t > timedelta(days=AUDIT_STALE_DAYS)


def _temporal_worst_is_stale(temp: TemporalSnapshot) -> bool:
    """True when aggregate temporal SLA view is past threshold (artifact truth)."""
    if not temp.present:
        return False
    ws = (temp.worst_freshness_status or "").strip().lower()
    return ws in ("stale", "expired")


def _eligible_temporal_refresh(sig: SignalsSnapshot, temp: TemporalSnapshot, now) -> bool:
    """True when fresh signals exist and temporal is missing or worst-FR is stale (recompute without adapters)."""
    if not sig.present:
        return False
    if _is_stale_signals(sig, now):
        return False
    if not temp.present:
        return True
    return _temporal_worst_is_stale(temp)


def _audit_product_gap_incomplete(aud: AuditSnapshot) -> bool:
    st = aud.angle_status.get("product_gap", "")
    return not aud.present or not st or st == "stub"


def _audit_product_gap_partial(aud: AuditSnapshot) -> bool:
    return aud.angle_status.get("product_gap", "") == "partial"


def _audit_security_stub(aud: AuditSnapshot) -> bool:
    return aud.angle_status.get("security", "") == "stub"


def _is_stale_signals_combined(
    sig: SignalsSnapshot,
    temp: TemporalSnapshot,
    now,
) -> bool:
    return _is_stale_signals(sig, now) or _temporal_worst_is_stale(temp)


def _compound_escalation_dimensions(sig: SignalsSnapshot, temp: TemporalSnapshot, aud: AuditSnapshot, now) -> int:
    """
    Orthogonal "dimensions" for compound escalation (each counts at most once):
    signals/temporal refresh line, audit bundle age, audit bundle content gaps (when bundle exists).
    """
    n = 0
    if _is_stale_signals_combined(sig, temp, now):
        n += 1
    if _is_stale_audit(aud, now):
        n += 1
    if aud.present and (_audit_product_gap_incomplete(aud) or _audit_security_stub(aud)):
        n += 1
    return n


def _findings_generate_gates(ctx: _OrchestrationEvalContext) -> bool:
    """``runs/signals/latest`` present and not time-stale — same predicate as prior inline emission."""
    return bool(ctx.sig.present and not _is_stale_signals(ctx.sig, ctx.now))


def _decisions_generate_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Fresh signals and findings latest on disk — same predicate as prior inline emission."""
    root, pid = ctx.root, ctx.product_id
    if not (ctx.sig.present and not _is_stale_signals(ctx.sig, ctx.now)):
        return False
    return findings_latest_path(root, pid).is_file()


def _ideas_generate_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Fresh signals plus findings and decisions latest (post-decide spine)."""
    root, pid = ctx.root, ctx.product_id
    if not (ctx.sig.present and not _is_stale_signals(ctx.sig, ctx.now)):
        return False
    if not findings_latest_path(root, pid).is_file():
        return False
    if not decisions_latest_path(root, pid).is_file():
        return False
    return True


def _experiments_propose_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Same Phase-2 spine as ideas_generate (fresh signals, findings, decisions); proposals are not execution authority."""
    return _ideas_generate_gates(ctx)


def _experiments_prioritize_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Post-decide spine plus persisted proposals artifact (canonical input for ranking; advisory only)."""
    if not _experiments_propose_gates(ctx):
        return False
    return experiments_proposals_latest_product_path(ctx.root, ctx.product_id).is_file()


def _experiments_create_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Prioritization present with ranked rows, quota/policy allows creation; materializes tracked experiment JSON only."""
    if not _experiments_prioritize_gates(ctx):
        return False
    if not prioritization_latest_has_ranked_for_product(ctx.root, ctx.product_id):
        return False
    ok, _msg = check_experiment_create_allowed(ctx.root)
    return bool(ok)


def _experiments_activate_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Phase-2 spine plus at least one proposed experiment; registry allows proposed -> active (in-repo lifecycle only)."""
    if not _ideas_generate_gates(ctx):
        return False
    for e in list_experiments(ctx.root, product_id=ctx.product_id):
        if e.status == ExperimentStatus.PROPOSED and can_transition(
            ExperimentStatus.PROPOSED, ExperimentStatus.ACTIVE
        ):
            return True
    return False


def _experiments_evaluate_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Phase-2 spine plus at least one non-terminal persisted experiment (evaluation updates experiment JSON; not execution authority)."""
    if not _ideas_generate_gates(ctx):
        return False
    rows = list_experiments(ctx.root, product_id=ctx.product_id)
    if not rows:
        return False
    return any(e.status not in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED) for e in rows)


def _experiments_close_stale_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Phase-2 spine, ≥1 experiment on disk, and ≥1 matches deterministic stale-close age rule (in-repo hygiene only)."""
    if not _ideas_generate_gates(ctx):
        return False
    from argus.experiments.stale_close import experiment_eligible_for_stale_close

    rows = list_experiments(ctx.root, product_id=ctx.product_id)
    if not rows:
        return False
    return any(experiment_eligible_for_stale_close(e, ctx.now) for e in rows)


def _experiments_surface_findings_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Phase-2 spine, ≥1 experiment, ≥1 with terminal outcome or persisted evaluation verdict."""
    if not _ideas_generate_gates(ctx):
        return False
    from argus.findings.experiment_surfaced import has_usable_experiment_outcome

    rows = list_experiments(ctx.root, product_id=ctx.product_id)
    if not rows:
        return False
    return any(has_usable_experiment_outcome(e) for e in rows)


def _decisions_refresh_from_surfaced_findings_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Fresh signals + findings latest, ≥1 loadable surfaced finding, surface newer than decisions when decisions exists."""
    root, pid = ctx.root, ctx.product_id
    if not (ctx.sig.present and not _is_stale_signals(ctx.sig, ctx.now)):
        return False
    if not findings_latest_path(root, pid).is_file():
        return False
    from argus.findings.experiment_surfaced import (
        EXPERIMENT_SURFACED_SCHEMA,
        experiment_surfaced_latest_path,
        load_experiment_surfaced_findings,
    )
    from argus.findings.persistence import load_latest_findings

    if load_latest_findings(root, pid) is None:
        return False
    extra = load_experiment_surfaced_findings(root, pid)
    if len(extra) < 1:
        return False
    sp = experiment_surfaced_latest_path(root, pid)
    if not sp.is_file():
        return False
    try:
        raw_s = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    if not isinstance(raw_s, dict) or str(raw_s.get("schema") or "") != EXPERIMENT_SURFACED_SCHEMA:
        return False
    dp = decisions_latest_path(root, pid)
    if not dp.is_file():
        return True
    try:
        raw_d = json.loads(dp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True
    if not isinstance(raw_d, dict) or str(raw_d.get("product_id") or "") != pid:
        return True
    ts_s = parse_iso_timestamp(str(raw_s.get("generated_at_utc") or ""))
    ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    if ts_s is None:
        return False
    if ts_dec is None:
        return True
    return ts_s > ts_dec


def _strategy_refresh_from_decision_evolution_gates(ctx: _OrchestrationEvalContext) -> bool:
    """
    Loadable decisions latest; refresh when strategy missing/invalid or older than max(decisions, surfaced).
    """
    root, pid = ctx.root, ctx.product_id
    dp = decisions_latest_path(root, pid)
    if not dp.is_file():
        return False
    try:
        raw_d = json.loads(dp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    if not isinstance(raw_d, dict) or str(raw_d.get("product_id") or "") != pid:
        return False
    ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    if ts_dec is None:
        return False
    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sp = experiment_surfaced_latest_path(root, pid)
    ts_surf: datetime | None = None
    if sp.is_file():
        try:
            raw_s = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_s = None
        if isinstance(raw_s, dict):
            ts_surf = parse_iso_timestamp(str(raw_s.get("generated_at_utc") or ""))
    evidence_ts = ts_dec if ts_surf is None else max(ts_dec, ts_surf)
    strat_p = strategy_latest_path(root, pid)
    if not strat_p.is_file():
        return True
    try:
        raw_st = json.loads(strat_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True
    if not isinstance(raw_st, dict) or str(raw_st.get("product_id") or "") != pid:
        return True
    ts_st = parse_iso_timestamp(str(raw_st.get("generated_at_utc") or ""))
    if ts_st is None:
        return True
    return ts_st < evidence_ts


def _planning_refresh_from_strategy_gates(ctx: _OrchestrationEvalContext) -> bool:
    """
    Loadable strategy latest; refresh when planning missing/invalid or older than
    max(strategy, decisions, experiment-surfaced) timestamps.
    """
    root, pid = ctx.root, ctx.product_id
    strat_p = strategy_latest_path(root, pid)
    if not strat_p.is_file():
        return False
    try:
        raw_st = json.loads(strat_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA

    if not isinstance(raw_st, dict) or str(raw_st.get("schema") or "") != STRATEGY_SNAPSHOT_SCHEMA:
        return False
    if str(raw_st.get("product_id") or "") != pid:
        return False
    ts_strat = parse_iso_timestamp(str(raw_st.get("generated_at_utc") or ""))
    if ts_strat is None:
        return False

    dp = decisions_latest_path(root, pid)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict) and str(raw_d.get("product_id") or "") == pid:
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))

    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sp = experiment_surfaced_latest_path(root, pid)
    ts_surf: datetime | None = None
    if sp.is_file():
        try:
            raw_s = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_s = None
        if isinstance(raw_s, dict):
            ts_surf = parse_iso_timestamp(str(raw_s.get("generated_at_utc") or ""))

    evidence_ts = ts_strat
    for t in (ts_dec, ts_surf):
        if t is not None:
            evidence_ts = max(evidence_ts, t)

    plan_p = planning_latest_path(root, pid)
    if not plan_p.is_file():
        return True
    try:
        raw_p = json.loads(plan_p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True
    from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA

    if not isinstance(raw_p, dict) or str(raw_p.get("schema") or "") != PLANNING_SNAPSHOT_SCHEMA:
        return True
    if str(raw_p.get("product_id") or "") != pid:
        return True
    ts_plan = parse_iso_timestamp(str(raw_p.get("generated_at_utc") or ""))
    if ts_plan is None:
        return True
    return ts_plan < evidence_ts


def _ideas_refresh_from_surfaced_findings_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Phase-2 spine, canonical findings latest, ≥1 loadable experiment-surfaced finding, surface newer than ideas when ideas exists."""
    if not _ideas_generate_gates(ctx):
        return False
    from argus.findings.experiment_surfaced import (
        EXPERIMENT_SURFACED_SCHEMA,
        experiment_surfaced_latest_path,
        load_experiment_surfaced_findings,
    )
    from argus.findings.persistence import load_latest_findings
    root, pid = ctx.root, ctx.product_id
    if load_latest_findings(root, pid) is None:
        return False
    extra = load_experiment_surfaced_findings(root, pid)
    if len(extra) < 1:
        return False
    sp = experiment_surfaced_latest_path(root, pid)
    if not sp.is_file():
        return False
    try:
        raw_s = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return False
    if not isinstance(raw_s, dict) or str(raw_s.get("schema") or "") != EXPERIMENT_SURFACED_SCHEMA:
        return False
    ip = ideas_bundle_latest_path(root)
    if not ip.is_file():
        return True
    try:
        raw_i = json.loads(ip.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return True
    if not isinstance(raw_i, dict) or str(raw_i.get("product_id") or "") != pid:
        return True
    ts_s = parse_iso_timestamp(str(raw_s.get("generated_at_utc") or ""))
    ts_i = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    if ts_s is None:
        return False
    if ts_i is None:
        return True
    return ts_s > ts_i


def _refinement_start_idea_gates(ctx: _OrchestrationEvalContext) -> bool:
    """Product-scoped ideas latest, deterministic pick, no blocking idea session for that source."""
    from argus.orchestrator.refinement_idea_pick import (
        deterministic_idea_id_for_refinement_orchestration,
        has_non_terminal_idea_refinement_for_source,
    )

    root, pid = ctx.root, ctx.product_id
    iid = deterministic_idea_id_for_refinement_orchestration(root, pid)
    if not iid:
        return False
    if has_non_terminal_idea_refinement_for_source(root, pid, iid):
        return False
    return True


def _escalation_packet_generate_gates(ctx: _OrchestrationEvalContext) -> bool:
    """
    Structural escalation posture for packet generation (subset of full ``escalation_triggers``:
    excludes execution-feedback-derived rows, which are not known until after eligible-actions merge).
    """
    root, pid = ctx.root, ctx.product_id
    if not findings_latest_path(root, pid).is_file():
        return False
    sig, temp, aud, now = ctx.sig, ctx.temp, ctx.aud, ctx.now
    if _compound_escalation_dimensions(sig, temp, aud, now) >= 2:
        return True
    if _audit_product_gap_incomplete(aud) or _audit_security_stub(aud) or _audit_product_gap_partial(aud):
        return True
    for s in ctx.sessions:
        if refinement_not_converged_stuck(root, s):
            return True
        if s.status == SessionStatus.REJECTED.value:
            return True
    if ctx.ps_sess and ctx.ps_sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        return True
    if ctx.ip_sess and ctx.ip_sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        return True
    for s in ctx.sessions:
        if refinement_reviews_in_missing(root, s):
            t = parse_iso_timestamp(s.updated_at_utc)
            if t is not None and now - t > timedelta(hours=REVIEW_INPUT_WAIT_ESCALATION_HOURS):
                return True
    return False


# Declarative wiring for Phase-2 eligible rows (gate + watermark + reason codes + eligibility_facts flag).
_ORDERED_REGISTRY_ELIGIBLE_EMISSION: tuple[str, ...] = (
    ACTION_FINDINGS_GENERATE,
    ACTION_DECISIONS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_ESCALATION_PACKET_GENERATE,
)

# Import first-pass tier: ``failed`` blocks the Phase-2 spine; ``partial`` / ``skipped`` allow
# observe→interpret→decide but not downstream ideas/experiments/govern progression until success.
_IMPORT_FAILED_BLOCKED_ACTION_IDS: frozenset[str] = frozenset(_ORDERED_REGISTRY_ELIGIBLE_EMISSION) | frozenset(
    {ACTION_IMPLEMENTATION_PLAN_GENERATE}
)

_IMPORT_PARTIAL_BLOCKED_ACTION_IDS: frozenset[str] = frozenset(
    {
        ACTION_IDEAS_GENERATE,
        ACTION_EXPERIMENTS_PROPOSE,
        ACTION_EXPERIMENTS_PRIORITIZE,
        ACTION_EXPERIMENTS_CREATE,
        ACTION_EXPERIMENTS_ACTIVATE,
        ACTION_EXPERIMENTS_EVALUATE,
        ACTION_EXPERIMENTS_CLOSE_STALE,
        ACTION_EXPERIMENTS_SURFACE_FINDINGS,
        ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_REFINEMENT_START_IDEA,
        ACTION_ESCALATION_PACKET_GENERATE,
        ACTION_IMPLEMENTATION_PLAN_GENERATE,
    }
)


def _import_blocks_action_id(tier: str | None, action_id: str) -> bool:
    if tier is None or tier == "success" or tier == "unknown":
        return False
    if tier == "failed":
        return action_id in _IMPORT_FAILED_BLOCKED_ACTION_IDS
    if tier in ("partial", "skipped"):
        return action_id in _IMPORT_PARTIAL_BLOCKED_ACTION_IDS
    return False


def _apply_import_readiness_gates(ctx: _OrchestrationEvalContext) -> None:
    """Filter ``deduped`` by importer first-pass tier; populate ``import_health`` / ``readiness_reason``."""
    root = ctx.root
    pid = ctx.product_id
    tier = _import_gating_tier(ctx.import_state)
    ctx.import_readiness_tier = tier

    fps = None
    imported_at = None
    if ctx.import_state:
        fps = ctx.import_state.get("first_pass_status")
        v = ctx.import_state.get("imported_at_utc")
        if v is not None and str(v).strip():
            imported_at = str(v).strip()

    ctx.import_health = {
        "import_state_present": ctx.import_state is not None,
        "first_pass_status": fps if isinstance(fps, str) or fps is None else str(fps),
        "imported_at_utc": imported_at,
        "signals_present": bool(ctx.sig.present),
        "findings_present": findings_latest_path(root, pid).is_file(),
        "decisions_present": decisions_latest_path(root, pid).is_file(),
        "gating_tier": tier,
    }

    if tier is None:
        ctx.readiness_reason = "no import_state.first_pass_status — import readiness not enforced"
        return
    if tier == "unknown":
        ctx.readiness_reason = (
            f"import_state.first_pass_status={fps!r} is unrecognized — import readiness not enforced"
        )
        return
    if tier == "success":
        ctx.readiness_reason = "first_pass_status=success — full pipeline eligible"
        return

    ctx.deduped = [a for a in ctx.deduped if not _import_blocks_action_id(tier, a.action_id)]

    if tier == "failed":
        ctx.readiness_reason = (
            "first_pass_status=failed — Phase-2 pipeline actions blocked until import succeeds "
            "(observability, refinement, and escalation remain available when otherwise eligible)"
        )
    elif tier in ("partial", "skipped"):
        ctx.readiness_reason = (
            f"first_pass_status={tier} — limited pipeline: ideas/experiments/govern progression blocked "
            "until first_pass_status=success"
        )
    else:
        ctx.readiness_reason = f"first_pass_status={tier} — import tier gating applied"

    if not ctx.deduped:
        ctx.import_waiting_inputs_extra.append(
            {
                "kind": WAITING_KIND_IMPORT_FIRST_PASS,
                "reason_codes": [RC_IMPORT_FIRST_PASS_FAILED if tier == "failed" else RC_IMPORT_FIRST_PASS_PARTIAL],
                "detail": ctx.readiness_reason,
            }
        )
        msg = (
            "import first_pass_status is failed; repair import or re-run importer"
            if tier == "failed"
            else "import first_pass_status is partial or skipped; complete a successful first pass to unlock the full pipeline"
        )
        ctx.import_orchestration_status_override = (ORCH_STATUS_BLOCKED_WAITING_INPUT, msg)


ACTION_ELIGIBILITY_REGISTRY: dict[str, dict[str, Any]] = {
    ACTION_FINDINGS_GENERATE: {
        "gate_fn": _findings_generate_gates,
        "watermark_fn": _elig_wm.watermark_findings_generate,
        "reason_code": RC_FINDINGS_GENERATE_SIGNALS_READY,
        "reason_codes": (RC_FINDINGS_GENERATE_SIGNALS_READY,),
        "eligible_flag_name": "findings_generate_eligible",
        "reason": "runs/signals/latest present and not time-stale — can derive findings",
    },
    ACTION_DECISIONS_GENERATE: {
        "gate_fn": _decisions_generate_gates,
        "watermark_fn": _elig_wm.watermark_decisions_generate,
        "reason_code": RC_DECISIONS_GENERATE_FINDINGS_READY,
        "reason_codes": (RC_DECISIONS_GENERATE_FINDINGS_READY,),
        "eligible_flag_name": "decisions_generate_eligible",
        "reason": "runs/signals/latest fresh and runs/findings/latest present — can derive decisions",
    },
    ACTION_IDEAS_GENERATE: {
        "gate_fn": _ideas_generate_gates,
        "watermark_fn": _elig_wm.watermark_ideas_generate_or_refinement_start_idea,
        "reason_code": RC_IDEAS_GENERATE_DECISIONS_READY,
        "reason_codes": (RC_IDEAS_GENERATE_DECISIONS_READY,),
        "eligible_flag_name": "ideas_generate_eligible",
        "reason": "runs/signals fresh, findings and decisions latest present — can derive ideas bundle",
    },
    ACTION_EXPERIMENTS_PROPOSE: {
        "gate_fn": _experiments_propose_gates,
        "watermark_fn": _elig_wm.watermark_experiments_propose,
        "reason_code": RC_EXPERIMENTS_PROPOSE_READY,
        "reason_codes": (RC_EXPERIMENTS_PROPOSE_READY,),
        "eligible_flag_name": "experiments_propose_eligible",
        "reason": (
            "runs/signals fresh, findings and decisions latest present — "
            "deterministic experiment proposals (tracked hypotheses only; not approval or execution authority)"
        ),
    },
    ACTION_EXPERIMENTS_PRIORITIZE: {
        "gate_fn": _experiments_prioritize_gates,
        "watermark_fn": _elig_wm.watermark_experiments_prioritize,
        "reason_code": RC_EXPERIMENTS_PRIORITIZE_READY,
        "reason_codes": (RC_EXPERIMENTS_PRIORITIZE_READY,),
        "eligible_flag_name": "experiments_prioritize_eligible",
        "reason": (
            "runs/experiments/proposals/latest present with fresh signals and findings/decisions — "
            "deterministic experiment prioritization (advisory ranking only; not approval or execution authority)"
        ),
    },
    ACTION_EXPERIMENTS_CREATE: {
        "gate_fn": _experiments_create_gates,
        "watermark_fn": _elig_wm.watermark_experiments_create,
        "reason_code": RC_EXPERIMENTS_CREATE_READY,
        "reason_codes": (RC_EXPERIMENTS_CREATE_READY,),
        "eligible_flag_name": "experiments_create_eligible",
        "reason": (
            "prioritization latest has ranked proposals; quota allows — "
            "materialize top-ranked proposal to runs/experiments (tracked work object; not external execution)"
        ),
    },
    ACTION_EXPERIMENTS_ACTIVATE: {
        "gate_fn": _experiments_activate_gates,
        "watermark_fn": _elig_wm.watermark_experiments_activate,
        "reason_code": RC_EXPERIMENTS_ACTIVATE_READY,
        "reason_codes": (RC_EXPERIMENTS_ACTIVATE_READY,),
        "eligible_flag_name": "experiments_activate_eligible",
        "reason": (
            "Phase-2 spine and proposed experiment present — "
            "deterministic proposed -> active (in-repo lifecycle; not external execution)"
        ),
    },
    ACTION_EXPERIMENTS_EVALUATE: {
        "gate_fn": _experiments_evaluate_gates,
        "watermark_fn": _elig_wm.watermark_experiments_evaluate,
        "reason_code": RC_EXPERIMENTS_EVALUATE_READY,
        "reason_codes": (RC_EXPERIMENTS_EVALUATE_READY,),
        "eligible_flag_name": "experiments_evaluate_eligible",
        "reason": (
            "Phase-2 spine and non-terminal experiments present — "
            "deterministic evaluation updates experiment JSON (analysis only; not execution authority)"
        ),
    },
    ACTION_EXPERIMENTS_CLOSE_STALE: {
        "gate_fn": _experiments_close_stale_gates,
        "watermark_fn": _elig_wm.watermark_experiments_close_stale,
        "reason_code": RC_EXPERIMENTS_CLOSE_STALE_READY,
        "reason_codes": (RC_EXPERIMENTS_CLOSE_STALE_READY,),
        "eligible_flag_name": "experiments_close_stale_eligible",
        "reason": (
            "Phase-2 spine and stale non-terminal experiments (age rule) — "
            "deterministic in-repo failed closure (hygiene only; not external shutdown authority)"
        ),
    },
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: {
        "gate_fn": _experiments_surface_findings_gates,
        "watermark_fn": _elig_wm.watermark_experiments_surface_findings,
        "reason_code": RC_EXPERIMENTS_SURFACE_FINDINGS_READY,
        "reason_codes": (RC_EXPERIMENTS_SURFACE_FINDINGS_READY,),
        "eligible_flag_name": "experiments_surface_findings_eligible",
        "reason": (
            "Phase-2 spine and experiments with outcome evidence — "
            "write experiment-surfaced finding sidecar for decisions (derived evidence; not raw telemetry)"
        ),
    },
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: {
        "gate_fn": _decisions_refresh_from_surfaced_findings_gates,
        "watermark_fn": _elig_wm.watermark_decisions_refresh_from_surfaced_findings,
        "reason_code": RC_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS_READY,
        "reason_codes": (RC_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS_READY,),
        "eligible_flag_name": "decisions_refresh_from_surfaced_findings_eligible",
        "reason": (
            "Fresh signals + findings, experiment-surfaced sidecar with loadable findings newer than decisions — "
            "re-materialize decisions bundle from merged findings (derived; not new external evidence)"
        ),
    },
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: {
        "gate_fn": _ideas_refresh_from_surfaced_findings_gates,
        "watermark_fn": _elig_wm.watermark_ideas_refresh_from_surfaced_findings,
        "reason_code": RC_IDEAS_REFRESH_FROM_SURFACED_FINDINGS_READY,
        "reason_codes": (RC_IDEAS_REFRESH_FROM_SURFACED_FINDINGS_READY,),
        "eligible_flag_name": "ideas_refresh_from_surfaced_findings_eligible",
        "reason": (
            "Phase-2 spine, experiment-surfaced sidecar with loadable findings newer than ideas — "
            "re-materialize ideas bundle from merged findings (derived; not new external evidence)"
        ),
    },
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION: {
        "gate_fn": _strategy_refresh_from_decision_evolution_gates,
        "watermark_fn": _elig_wm.watermark_strategy_refresh_from_decision_evolution,
        "reason_code": RC_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION_READY,
        "reason_codes": (RC_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION_READY,),
        "eligible_flag_name": "strategy_refresh_from_decision_evolution_eligible",
        "reason": (
            "Decisions latest loadable — derive strategy snapshot when strategy missing or older than "
            "decisions / experiment-surfaced evidence (deterministic; inspectable)"
        ),
    },
    ACTION_PLANNING_REFRESH_FROM_STRATEGY: {
        "gate_fn": _planning_refresh_from_strategy_gates,
        "watermark_fn": _elig_wm.watermark_planning_refresh_from_strategy,
        "reason_code": RC_PLANNING_REFRESH_FROM_STRATEGY_READY,
        "reason_codes": (RC_PLANNING_REFRESH_FROM_STRATEGY_READY,),
        "eligible_flag_name": "planning_refresh_from_strategy_eligible",
        "reason": (
            "Strategy snapshot loadable — derive planning snapshot when planning missing or older than "
            "strategy / decisions / experiment-surfaced evidence (deterministic; inspectable)"
        ),
    },
    ACTION_REFINEMENT_START_IDEA: {
        "gate_fn": _refinement_start_idea_gates,
        "watermark_fn": _elig_wm.watermark_ideas_generate_or_refinement_start_idea,
        "reason_code": RC_REFINEMENT_START_IDEA_READY,
        "reason_codes": (RC_REFINEMENT_START_IDEA_READY,),
        "eligible_flag_name": "refinement_start_idea_eligible",
        "reason": (
            "ideas latest matches product; deterministic top idea available; "
            "no active idea refinement for that source"
        ),
    },
    ACTION_ESCALATION_PACKET_GENERATE: {
        "gate_fn": _escalation_packet_generate_gates,
        "watermark_fn": _elig_wm.watermark_escalation_packet_generate,
        "reason_code": RC_ESCALATION_PACKET_GENERATE_POSTURE,
        "reason_codes": (RC_ESCALATION_PACKET_GENERATE_POSTURE,),
        "eligible_flag_name": "escalation_packet_generate_eligible",
        "reason": "findings present and structural escalation posture — can materialize escalation packet",
    },
}


def _append_registry_eligible_rows(ctx: _OrchestrationEvalContext, eligible: list[EligibleAction]) -> None:
    for aid in _ORDERED_REGISTRY_ELIGIBLE_EMISSION:
        spec = ACTION_ELIGIBILITY_REGISTRY[aid]
        if not spec["gate_fn"](ctx):
            continue
        eligible.append(EligibleAction(aid, spec["reason"], spec["reason_codes"]))


def _merge_eligible(actions: list[EligibleAction]) -> list[EligibleAction]:
    """Merge duplicate action_ids; union reason_codes; join reasons."""
    by: dict[str, EligibleAction] = {}
    order: list[str] = []
    for a in actions:
        if a.action_id not in by:
            order.append(a.action_id)
            by[a.action_id] = a
            continue
        old = by[a.action_id]
        codes = tuple(sorted({*old.reason_codes, *a.reason_codes}))
        reason = old.reason if old.reason == a.reason else f"{old.reason}; {a.reason}"
        by[a.action_id] = EligibleAction(a.action_id, reason, codes)
    return [by[i] for i in order]


def _failure_finished_at_utc_from_feedback(by_action_id: dict[str, Any], action_id: str) -> datetime | None:
    fb = by_action_id.get(action_id)
    if not isinstance(fb, dict):
        return None
    return parse_iso_timestamp(str(fb.get("finished_at_utc") or "").strip() or None)


def _relevant_product_evidence_watermark_utc(action_id: str, ctx: _OrchestrationEvalContext) -> datetime | None:
    """
    Latest timestamp among existing artifacts that can change preconditions for ``action_id``.

    Used only to decide whether a **failed** feedback deprioritization should be skipped
    (newer evidence than the failure time). Narrow per-action mapping — unrelated artifacts
    must not clear another action's failure posture.
    """
    return _elig_wm.relevant_product_evidence_watermark_utc(action_id, ctx)


def _failed_feedback_superseded_by_newer_product_evidence(
    action_id: str,
    by_action_id: dict[str, Any],
    ctx: _OrchestrationEvalContext,
) -> bool:
    fin = _failure_finished_at_utc_from_feedback(by_action_id, action_id)
    if fin is None:
        return False
    wm = _relevant_product_evidence_watermark_utc(action_id, ctx)
    if wm is None:
        return False
    return wm > fin


def _reorder_eligible_for_execution_feedback(
    ctx: _OrchestrationEvalContext,
    by_action_id: dict[str, Any],
) -> list[EligibleAction]:
    """
    If the **latest outcome for the first eligible ``action_id``** (per ``by_action_id``) is
    ``failed``, ``queued_unhandled``, or ``executed``, move that action after others and tag codes.

    Repeats deterministically while the new head still has a deprioritizing status (bounded by list
    length) so Phase-2 chains (e.g. ``findings_generate`` then ``decisions_generate``) do not leave a
    later action stuck behind consecutive executed heads in one evaluation.

    Unrelated eligible actions are unchanged except for ordering when rows move.

    For ``failed`` only: deprioritization is skipped when existing artifact timestamps show
    product evidence newer than the failure time (see :func:`_relevant_product_evidence_watermark_utc`).
    """
    deduped: list[EligibleAction] = list(ctx.deduped)
    ctx.orchestration_failed_deprioritize_suppressed_action_ids = []
    max_passes = max(1, len(deduped))
    for _ in range(max_passes):
        if len(deduped) < 2:
            break
        first_id = deduped[0].action_id
        fb = by_action_id.get(first_id)
        if not isinstance(fb, dict):
            break
        st = str(fb.get("orchestration_action_status") or "")
        if st == ACTION_STATUS_FAILED:
            if _failed_feedback_superseded_by_newer_product_evidence(first_id, by_action_id, ctx):
                ctx.orchestration_failed_deprioritize_suppressed_action_ids.append(first_id)
                break
            code = RC_ORCH_FAILED_ACTION_DEPRIORITIZED
        elif st == ACTION_STATUS_QUEUED_UNHANDLED:
            code = RC_ORCH_UNHANDLED_ACTION_DEPRIORITIZED
        elif st == ACTION_STATUS_EXECUTED:
            code = RC_ORCH_SAME_ACTION_AFTER_SUCCESS
        else:
            break
        rest = [a for a in deduped if a.action_id != first_id]
        same: list[EligibleAction] = []
        for a in deduped:
            if a.action_id != first_id:
                continue
            codes = tuple(sorted({*a.reason_codes, code}))
            same.append(EligibleAction(a.action_id, a.reason, codes))
        new_deduped = rest + same
        if new_deduped == deduped:
            break
        deduped = new_deduped
    return deduped


def _execution_feedback_headline_suffix(
    *,
    feedback_summary: dict[str, Any] | None,
    next_action: str,
    deduped_action_ids: set[str],
) -> str | None:
    """
    Deterministic addendum for orchestration_status_reason from execution-feedback summary facts.

    Does not replace blockers/waiting_inputs; only surfaces posture when feedback intersects
    next_action or other still-eligible action ids.
    """
    if not isinstance(feedback_summary, dict):
        return None
    failed_ids = {str(x) for x in (feedback_summary.get("failed_action_ids") or []) if str(x).strip()}
    unhandled_ids = {
        str(x) for x in (feedback_summary.get("queued_unhandled_action_ids") or []) if str(x).strip()
    }
    if not failed_ids and not unhandled_ids:
        return None

    na = str(next_action or "").strip()
    if na == "none":
        na = ""

    fragments: list[str] = []

    if na:
        if na in failed_ids:
            fragments.append(
                f"next_action {na} has recent failed in-process execution (see blockers)"
            )
        if na in unhandled_ids:
            fragments.append(
                f"next_action {na} has recent queued_unhandled execution (see waiting_inputs)"
            )

    failed_other = sorted(failed_ids & deduped_action_ids - ({na} if na else set()))
    if failed_other:
        fragments.append(
            f"recent failure also recorded for eligible action_id(s): {', '.join(failed_other)}"
        )

    uh_other = sorted(unhandled_ids & deduped_action_ids - ({na} if na else set()))
    if uh_other:
        fragments.append(
            f"queued_unhandled also recorded for eligible action_id(s): {', '.join(uh_other)}"
        )

    if not fragments:
        return None
    return "; ".join(fragments)


def _retry_reopened_posture(
    *,
    feedback_summary: dict[str, Any] | None,
    suppressed_action_ids: list[str],
    next_action: str,
    deduped: list[EligibleAction],
) -> dict[str, Any]:
    """
    Structured retry-reopened detection (same criteria as :func:`_retry_reopened_headline_suffix`).
    """
    empty: dict[str, Any] = {"active": False, "action_ids": []}
    if not suppressed_action_ids:
        return empty
    if not isinstance(feedback_summary, dict):
        return empty
    failed_ids = {str(x) for x in (feedback_summary.get("failed_action_ids") or []) if str(x).strip()}
    sup = {str(x) for x in suppressed_action_ids if str(x).strip()}
    deduped_ids = {a.action_id for a in deduped}
    material = sorted(sup & failed_ids & deduped_ids)
    if not material:
        return empty

    na = str(next_action or "").strip()
    if na == "none":
        na = ""
    first_id = deduped[0].action_id if deduped else None
    relevant = [
        a
        for a in material
        if (na and a == na) or (first_id is not None and a == first_id)
    ]
    if not relevant:
        return empty

    return {"active": True, "action_ids": sorted(relevant)}


def _execution_feedback_crosswalk(
    feedback_summary: dict[str, Any] | None,
    next_action: str,
    deduped_action_ids: set[str],
) -> dict[str, Any]:
    """Structured view of execution-feedback overlap with ``next_action`` / eligible ids."""
    base = {
        "next_action_matches_recent_failed_execution": False,
        "next_action_matches_recent_queued_unhandled": False,
        "eligible_action_ids_with_recent_failed_execution": [],
        "eligible_action_ids_with_recent_queued_unhandled": [],
    }
    if not isinstance(feedback_summary, dict):
        return base
    failed_ids = {str(x) for x in (feedback_summary.get("failed_action_ids") or []) if str(x).strip()}
    unhandled_ids = {
        str(x) for x in (feedback_summary.get("queued_unhandled_action_ids") or []) if str(x).strip()
    }
    na = str(next_action or "").strip()
    if na == "none":
        na = ""
    return {
        "next_action_matches_recent_failed_execution": bool(na and na in failed_ids),
        "next_action_matches_recent_queued_unhandled": bool(na and na in unhandled_ids),
        "eligible_action_ids_with_recent_failed_execution": sorted(failed_ids & deduped_action_ids),
        "eligible_action_ids_with_recent_queued_unhandled": sorted(unhandled_ids & deduped_action_ids),
    }


def _repeat_threshold_posture_from_escalation_triggers(
    escalation_triggers: list[dict[str, Any]] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Extract repeated-failure / repeated-queued_unhandled rows already in ``escalation_triggers``."""
    failures: list[dict[str, Any]] = []
    queued_unhandled: list[dict[str, Any]] = []
    for t in escalation_triggers or []:
        if not isinstance(t, dict):
            continue
        c = str(t.get("code") or "")
        if c == RC_ORCH_FEEDBACK_REPEATED_FAILURES:
            failures.append(
                {
                    "action_id": str(t.get("action_id") or ""),
                    "recent_fail_count": t.get("recent_fail_count"),
                    "threshold": t.get("threshold"),
                }
            )
        elif c == RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED:
            queued_unhandled.append(
                {
                    "action_id": str(t.get("action_id") or ""),
                    "recent_queued_unhandled_count": t.get("recent_queued_unhandled_count"),
                    "threshold": t.get("threshold"),
                }
            )
    failures.sort(key=lambda x: x.get("action_id", ""))
    queued_unhandled.sort(key=lambda x: x.get("action_id", ""))
    return {"failures": failures, "queued_unhandled": queued_unhandled}


def _build_orchestration_posture(ctx: _OrchestrationEvalContext) -> dict[str, Any]:
    deduped_ids = {a.action_id for a in ctx.deduped}
    return {
        "schema": ORCHESTRATION_POSTURE_SCHEMA,
        "execution_feedback_crosswalk": _execution_feedback_crosswalk(
            ctx.orchestration_feedback_summary,
            ctx.next_action,
            deduped_ids,
        ),
        "retry_reopened": _retry_reopened_posture(
            feedback_summary=ctx.orchestration_feedback_summary,
            suppressed_action_ids=ctx.orchestration_failed_deprioritize_suppressed_action_ids,
            next_action=ctx.next_action,
            deduped=ctx.deduped,
        ),
        "repeat_execution_feedback_thresholds": _repeat_threshold_posture_from_escalation_triggers(
            ctx.escalation_triggers
        ),
    }


def _retry_reopened_headline_suffix(
    *,
    feedback_summary: dict[str, Any] | None,
    suppressed_action_ids: list[str],
    next_action: str,
    deduped: list[EligibleAction],
) -> str | None:
    """
    When failed-action deprioritization is suppressed (newer evidence), clarify retry vs history.

    Uses only ``feedback_summary`` failed ids and ``suppressed_action_ids`` from reorder; does not
    alter blockers. Omits output when nothing is both suppressed and still failed per summary.
    """
    r = _retry_reopened_posture(
        feedback_summary=feedback_summary,
        suppressed_action_ids=suppressed_action_ids,
        next_action=next_action,
        deduped=deduped,
    )
    if not r.get("active"):
        return None
    ids = [str(x) for x in (r.get("action_ids") or []) if str(x).strip()]
    if not ids:
        return None

    return (
        "retry posture reopened for action_id(s) "
        f"{', '.join(ids)} "
        "(newer relevant product evidence than failure time; historical failure retained — see blockers)"
    )


def _refinement_review_state(
    root: Path,
    sessions: list[RefinementSessionSnap],
    ps_sess: RefinementSessionSnap | None,
    ip_sess: RefinementSessionSnap | None,
    waiting: bool,
) -> str:
    if waiting:
        return REFINEMENT_REVIEW_AWAITING_REVIEW_INPUT
    for s in sessions:
        if refinement_not_converged_stuck(root, s):
            return REFINEMENT_REVIEW_NOT_CONVERGED_STUCK
    for s in sessions:
        if s.status == SessionStatus.REJECTED.value:
            return REFINEMENT_REVIEW_REJECTED
    for s in sessions:
        if s.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
            return REFINEMENT_REVIEW_HUMAN_REVIEW_REQUIRED
    ps_ok = ps_sess and ps_sess.status in (
        SessionStatus.APPROVED.value,
        SessionStatus.APPROVED_WITH_RISKS.value,
    )
    ip_ok = ip_sess and ip_sess.status in (
        SessionStatus.APPROVED.value,
        SessionStatus.APPROVED_WITH_RISKS.value,
    )
    if ps_ok and ip_ok:
        return REFINEMENT_REVIEW_FINALIZED
    if ps_sess or ip_sess:
        return REFINEMENT_REVIEW_IN_PROGRESS
    return REFINEMENT_REVIEW_ABSENT


def _orchestration_status_and_reason(
    *,
    waiting: bool,
    escalated: bool,
    approval_pending: bool,
    stale_signals_or_audit: bool,
    has_eligible: bool,
) -> tuple[str, str]:
    """Single headline status (deterministic priority)."""
    if waiting:
        return (
            ORCH_STATUS_BLOCKED_WAITING_INPUT,
            "refinement awaiting grounded review files or interrupted cycle (see waiting_inputs)",
        )
    if escalated:
        return ORCH_STATUS_ESCALATED, "refinement requires escalation or human handoff"
    if approval_pending:
        return (
            ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
            "execution gated: pending approval record(s) under runs/approval/records/",
        )
    if stale_signals_or_audit:
        return (
            ORCH_STATUS_STALE_REFRESH_NEEDED,
            "signals/temporal/audit artifacts need refresh (see waiting_inputs)",
        )
    if has_eligible:
        return ORCH_STATUS_ELIGIBLE, "at least one next action is eligible"
    return ORCH_STATUS_COMPLETE, "no blockers and no eligible actions"


def _impl_plan_stale_vs_product_spec(
    ps: RefinementSessionSnap | None,
    ip: RefinementSessionSnap | None,
) -> bool:
    """
    True when a non-final implementation_plan session predates a newer product_spec update
    (durable timestamps on refinement sessions).
    """
    if ps is None or ip is None:
        return False
    if ip.artifact_type != ArtifactType.IMPLEMENTATION_PLAN.value:
        return False
    if ip.status in (SessionStatus.APPROVED.value, SessionStatus.APPROVED_WITH_RISKS.value):
        return False
    tps = parse_iso_timestamp(ps.updated_at_utc)
    tip = parse_iso_timestamp(ip.updated_at_utc)
    if tps is None or tip is None:
        return False
    return tps > tip


def _implementation_plan_generate_gates(
    *,
    ps_final: bool,
    ip_sess: RefinementSessionSnap | None,
    waiting: bool,
    sig: SignalsSnapshot,
    temp: TemporalSnapshot,
    aud: AuditSnapshot,
    now,
) -> tuple[bool, tuple[str, ...]]:
    """
    Whether ``implementation_plan_generate`` may be emitted.

    Returns ``(eligible, reason_codes)``. When eligible, ``reason_codes`` includes
    ``RC_PRODUCT_SPEC_FINALIZED_NO_IMPL_PLAN``. When ineligible but the product is in the
    product_spec→plan gap, ``reason_codes`` list blocking codes (may be multiple, sorted).
    """
    if not ps_final or ip_sess is not None:
        return False, ()
    if waiting:
        return False, (RC_IMPL_PLAN_BLOCKED_REFINEMENT_WAITING,)
    blocked: list[str] = []
    if _is_stale_signals_combined(sig, temp, now):
        blocked.append(RC_IMPL_PLAN_BLOCKED_SIGNALS_STALE)
    if _is_stale_audit(aud, now):
        blocked.append(RC_IMPL_PLAN_BLOCKED_AUDIT_STALE)
    if _audit_product_gap_incomplete(aud):
        blocked.append(RC_IMPL_PLAN_BLOCKED_AUDIT_PRODUCT_GAP)
    if _audit_security_stub(aud):
        blocked.append(RC_IMPL_PLAN_BLOCKED_AUDIT_SECURITY_STUB)
    if blocked:
        return False, tuple(sorted(set(blocked)))
    return True, (RC_PRODUCT_SPEC_FINALIZED_NO_IMPL_PLAN,)


def _refinement_phase(status: str) -> str:
    if status in (SessionStatus.APPROVED.value, SessionStatus.APPROVED_WITH_RISKS.value):
        return PHASE_FINALIZED
    if status in (SessionStatus.REJECTED.value,):
        return PHASE_TERMINAL_REJECTED
    if status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        return "human_review_required"
    if status in (
        SessionStatus.DRAFT.value,
        SessionStatus.IN_REVIEW.value,
        SessionStatus.REFINING.value,
    ):
        return PHASE_IN_PROGRESS
    return PHASE_IN_PROGRESS


def _stage_load_snapshots(repo_root: Path, product_id: str) -> _OrchestrationEvalContext:
    root = repo_root.resolve()
    now = utc_now()
    now_iso = now.isoformat()
    sig = load_signals_snapshot(root, product_id)
    temp = load_temporal_snapshot(root, product_id)
    aud = load_audit_snapshot(root, product_id)
    ex = load_execution_snapshot(root, product_id)
    pending_approvals = load_pending_approvals_for_product(root, product_id)
    sessions = list_refinement_sessions_for_product(root, product_id)
    ps_sess = pick_latest_session(sessions, ArtifactType.PRODUCT_SPEC)
    ip_sess = pick_latest_session(sessions, ArtifactType.IMPLEMENTATION_PLAN)
    return _OrchestrationEvalContext(
        root=root,
        product_id=product_id,
        now=now,
        now_iso=now_iso,
        sig=sig,
        temp=temp,
        aud=aud,
        ex=ex,
        pending_approvals=pending_approvals,
        sessions=sessions,
        ps_sess=ps_sess,
        ip_sess=ip_sess,
        import_state=_load_import_state(root, product_id),
    )


def _stage_build_artifacts_dict(ctx: _OrchestrationEvalContext) -> None:
    now = ctx.now
    sig = ctx.sig
    temp = ctx.temp
    aud = ctx.aud
    ex = ctx.ex
    pending_approvals = ctx.pending_approvals
    ps_sess = ctx.ps_sess
    ip_sess = ctx.ip_sess
    idea_sess = pick_latest_session(ctx.sessions, ArtifactType.IDEA)

    def sess_blob(s: RefinementSessionSnap | None) -> dict[str, Any]:
        if s is None:
            return {
                "phase": PHASE_ABSENT,
                "session_id": None,
                "status": None,
                "current_round": None,
            }
        return {
            "phase": _refinement_phase(s.status),
            "session_id": s.session_id,
            "status": s.status,
            "current_round": s.current_round,
            "max_rounds": s.max_rounds,
            "updated_at_utc": s.updated_at_utc,
        }

    ctx.artifacts = {
        "product_spec": sess_blob(ps_sess),
        "implementation_plan": {
            **sess_blob(ip_sess),
            "stale_vs_product_spec": _impl_plan_stale_vs_product_spec(ps_sess, ip_sess),
        },
        "refinement": {
            "review_state": REFINEMENT_REVIEW_ABSENT,
            "idea": sess_blob(idea_sess),
            "primary_sessions": [
                {"artifact_type": "product_spec", "session_id": ps_sess.session_id if ps_sess else None},
                {
                    "artifact_type": "implementation_plan",
                    "session_id": ip_sess.session_id if ip_sess else None,
                },
                {"artifact_type": "idea", "session_id": idea_sess.session_id if idea_sess else None},
            ],
            "allowed_review_states": [
                REFINEMENT_REVIEW_ABSENT,
                REFINEMENT_REVIEW_IN_PROGRESS,
                REFINEMENT_REVIEW_AWAITING_REVIEW_INPUT,
                REFINEMENT_REVIEW_FINALIZED,
                REFINEMENT_REVIEW_HUMAN_REVIEW_REQUIRED,
                REFINEMENT_REVIEW_REJECTED,
                REFINEMENT_REVIEW_NOT_CONVERGED_STUCK,
            ],
        },
        "signals": {
            "phase": PHASE_PRESENT if sig.present else PHASE_ABSENT,
            "staleness": (
                PHASE_STALE if _is_stale_signals_combined(sig, temp, now) else PHASE_FRESH
            ),
            "collected_at_utc": sig.collected_at_utc,
            "path": sig.path,
        },
        "temporal": {
            "phase": PHASE_PRESENT if temp.present else PHASE_ABSENT,
            "worst_freshness_status": temp.worst_freshness_status,
            "staleness": (
                PHASE_STALE
                if _temporal_worst_is_stale(temp)
                else (PHASE_FRESH if temp.present else PHASE_ABSENT)
            ),
            "collected_at_utc": temp.collected_at_utc,
            "path": temp.path,
        },
        "audit": {
            "phase": PHASE_PRESENT if aud.present else PHASE_ABSENT,
            "staleness": PHASE_STALE if _is_stale_audit(aud, now) else PHASE_FRESH,
            "generated_at_utc": aud.generated_at_utc,
            "path": aud.path,
            "angle_status": dict(sorted(aud.angle_status.items())),
        },
        "execution": {
            "phase": PHASE_NONE if ex.record_count == 0 else PHASE_PRESENT,
            "record_count": ex.record_count,
            "path": ex.path,
            "pending_approvals": pending_approvals,
        },
    }


def _stage_evaluate_blockers(ctx: _OrchestrationEvalContext) -> None:
    root = ctx.root
    sessions = ctx.sessions
    eligible = ctx.eligible
    blockers = ctx.blockers

    for s in sessions:
        if refinement_cycle_incomplete(root, s):
            blockers.append(
                {
                    "kind": "refinement_cycle_incomplete",
                    "detail": (
                        f"session {s.session_id} round {s.current_round}: "
                        "draft exists but reviews/round file missing"
                    ),
                    "session_id": s.session_id,
                }
            )
            if refinement_reviews_in_missing(root, s):
                eligible.append(
                    EligibleAction(
                        ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
                        "reviews_in/round file missing while session is in_review",
                    )
                )

    ctx.waiting = any(b.get("kind") == "refinement_cycle_incomplete" for b in blockers)

    for s in sessions:
        if refinement_not_converged_stuck(root, s):
            blockers.append(
                {
                    "kind": "refinement_not_converged_stuck",
                    "detail": (
                        f"session {s.session_id}: at max rounds with convergence.converged false "
                        f"(see convergence/round_{s.max_rounds - 1}.json)"
                    ),
                    "session_id": s.session_id,
                }
            )
            eligible.append(
                EligibleAction(
                    ACTION_ESCALATION_CONSIDER,
                    "refinement failed to converge before max rounds (artifact evidence)",
                )
            )

    for s in sessions:
        if s.status == SessionStatus.REJECTED.value:
            eligible.append(
                EligibleAction(
                    ACTION_ESCALATION_CONSIDER,
                    f"session {s.session_id} terminal status rejected",
                )
            )


def _stage_evaluate_eligible_actions(ctx: _OrchestrationEvalContext) -> None:
    root = ctx.root
    now = ctx.now
    sig = ctx.sig
    temp = ctx.temp
    aud = ctx.aud
    sessions = ctx.sessions
    ps_sess = ctx.ps_sess
    ip_sess = ctx.ip_sess
    eligible = ctx.eligible
    waiting = ctx.waiting

    ctx.ps_final = bool(
        ps_sess
        and ps_sess.status
        in (
            SessionStatus.APPROVED.value,
            SessionStatus.APPROVED_WITH_RISKS.value,
        )
    )
    ctx.impl_elig, ctx.impl_codes = _implementation_plan_generate_gates(
        ps_final=ctx.ps_final,
        ip_sess=ip_sess,
        waiting=waiting,
        sig=sig,
        temp=temp,
        aud=aud,
        now=now,
    )
    ctx.ip_absent = ip_sess is None
    ctx.progression_impl_plan = {
        "implementation_plan_session_absent": ctx.ip_absent,
        "product_spec_finalized": bool(ctx.ps_final),
        "generate_eligible": ctx.impl_elig,
        "blocked_reason_codes": (
            list(ctx.impl_codes)
            if (bool(ctx.ps_final) and ctx.ip_absent and not ctx.impl_elig and ctx.impl_codes)
            else []
        ),
        "implementation_plan_stale_vs_product_spec": _impl_plan_stale_vs_product_spec(
            ps_sess, ip_sess
        ),
    }
    if _is_stale_signals(sig, now):
        eligible.append(
            EligibleAction(
                ACTION_SIGNALS_COLLECT,
                f"signals missing or older than {SIGNAL_STALE_HOURS}h",
                (RC_SIGNALS_TIME_STALE,),
            )
        )
    if _temporal_worst_is_stale(temp):
        w = temp.worst_freshness_status or ""
        eligible.append(
            EligibleAction(
                ACTION_SIGNALS_COLLECT,
                f"temporal worst_freshness_status is {w!r} (refresh signals collection)",
                (RC_TEMPORAL_WORST_STALE,),
            )
        )
    if _eligible_temporal_refresh(sig, temp, now):
        codes = (RC_TEMPORAL_LATEST_ABSENT,) if not temp.present else (RC_TEMPORAL_WORST_STALE,)
        eligible.append(
            EligibleAction(
                ACTION_TEMPORAL_REFRESH,
                "recompute temporal bundle from runs/signals/latest without re-running adapters",
                codes,
            )
        )
    _append_registry_eligible_rows(ctx, eligible)
    if _is_stale_audit(aud, now):
        eligible.append(
            EligibleAction(
                ACTION_AUDIT_RUN,
                f"audit missing or older than {AUDIT_STALE_DAYS}d",
                (RC_AUDIT_TIME_STALE,),
            )
        )
    if _audit_product_gap_incomplete(aud):
        eligible.append(
            EligibleAction(
                ACTION_AUDIT_RUN,
                "audit bundle: product_gap missing or stub — run `argus audit run`",
                (RC_AUDIT_PRODUCT_GAP_INCOMPLETE,),
            )
        )
        eligible.append(
            EligibleAction(
                ACTION_ESCALATION_CONSIDER,
                "audit product_gap missing or stub — review coverage vs doctrine",
                (RC_AUDIT_PRODUCT_GAP_INCOMPLETE,),
            )
        )
    if _audit_security_stub(aud):
        eligible.append(
            EligibleAction(
                ACTION_AUDIT_RUN,
                "audit security angle is stub — run `argus audit run`",
                (RC_AUDIT_SECURITY_STUB,),
            )
        )
        eligible.append(
            EligibleAction(
                ACTION_ESCALATION_CONSIDER,
                "audit security stub — governance review",
                (RC_AUDIT_SECURITY_STUB,),
            )
        )
    if _audit_product_gap_partial(aud):
        eligible.append(
            EligibleAction(
                ACTION_ESCALATION_CONSIDER,
                "audit product_gap is partial — review coverage",
                (RC_AUDIT_PRODUCT_GAP_PARTIAL,),
            )
        )

    if ps_sess is None:
        eligible.append(
            EligibleAction(
                ACTION_REFINEMENT_START_PRODUCT_SPEC,
                "no product_spec refinement session",
            )
        )
    if ctx.impl_elig:
        eligible.append(
            EligibleAction(
                ACTION_IMPLEMENTATION_PLAN_GENERATE,
                "product_spec finalized; observability baseline satisfied; no implementation_plan session",
                ctx.impl_codes,
            )
        )

    idea_sess = pick_latest_session(sessions, ArtifactType.IDEA)
    for s in (ps_sess, ip_sess, idea_sess):
        if s is None:
            continue
        if s.status not in (
            SessionStatus.DRAFT.value,
            SessionStatus.REFINING.value,
            SessionStatus.IN_REVIEW.value,
        ):
            continue
        if refinement_cycle_incomplete(root, s):
            continue
        eligible.append(
            EligibleAction(
                ACTION_REFINEMENT_RUN,
                f"session {s.session_id} may accept `argus refine run`",
            )
        )

    if ps_sess and ps_sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        eligible.append(
            EligibleAction(
                ACTION_ESCALATION_CONSIDER,
                "product_spec refinement requires human_review_required",
            )
        )
    if ip_sess and ip_sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        eligible.append(
            EligibleAction(
                ACTION_ESCALATION_CONSIDER,
                "implementation_plan refinement requires human_review_required",
            )
        )

    ctx.compound_dimensions = _compound_escalation_dimensions(sig, temp, aud, now)
    if ctx.compound_dimensions >= 2:
        eligible.append(
            EligibleAction(
                ACTION_ESCALATION_CONSIDER,
                f"{ctx.compound_dimensions} orthogonal artifact gaps (signals/temporal vs audit age vs audit content) — operator escalation",
                (RC_COMPOUND_CRITICAL_ARTIFACTS,),
            )
        )

    for s in sessions:
        if refinement_reviews_in_missing(root, s):
            t = parse_iso_timestamp(s.updated_at_utc)
            if t is not None and now - t > timedelta(hours=REVIEW_INPUT_WAIT_ESCALATION_HOURS):
                eligible.append(
                    EligibleAction(
                        ACTION_ESCALATION_CONSIDER,
                        f"session {s.session_id}: reviews_in missing beyond "
                        f"{REVIEW_INPUT_WAIT_ESCALATION_HOURS:.0f}h while in_review",
                        (RC_REVIEW_INPUT_WAIT_EXCEEDED,),
                    )
                )

    if pending_unapplied_execution_outcomes_for_product(root, ctx.product_id):
        eligible.append(
            EligibleAction(
                ACTION_EXECUTION_OUTCOMES_APPLY,
                "runs/execution JSON pending apply to experiments (see experiments/execution_apply)",
                (),
            )
        )

    ctx.deduped = _merge_eligible(eligible)
    _apply_state_hygiene_eligible_actions_order(ctx)


def _stage_apply_orchestration_execution_feedback(ctx: _OrchestrationEvalContext) -> None:
    """Fold durable step-executor outcomes into eligibility (facts, blockers, ordering, waiting hints)."""
    summary = load_orchestration_feedback_summary(ctx.root, ctx.product_id, now=ctx.now)
    ctx.orchestration_feedback_summary = summary

    by_action_id = summary.get("by_action_id")
    if not isinstance(by_action_id, dict):
        by_action_id = {}

    for aid in summary.get("failed_action_ids") or []:
        detail = by_action_id.get(aid)
        if not isinstance(detail, dict):
            continue
        ctx.blockers.append(
            {
                "kind": "orchestration_execution_failed",
                "action_id": aid,
                "detail": (
                    f"latest in-process feedback for {aid} is failed: "
                    f"{detail.get('execution_error') or 'see runs/execution/ orchestration_feedback_*.json'}"
                ),
                "finished_at_utc": detail.get("finished_at_utc"),
            }
        )

    unhandled_ids = list(summary.get("queued_unhandled_action_ids") or [])
    if unhandled_ids:
        ctx.orchestration_feedback_waiting_inputs.append(
            {
                "kind": "orchestration_execution_feedback",
                "reason_codes": [RC_ORCH_FEEDBACK_QUEUED_UNHANDLED],
                "action_ids": unhandled_ids,
                "detail": (
                    "queued_unhandled (no in-process executor) for action_id(s): "
                    + ", ".join(unhandled_ids)
                    + " — extend step_executor or run work out-of-band"
                ),
            }
        )

    ctx.deduped = _reorder_eligible_for_execution_feedback(ctx, by_action_id)

    ex = ctx.artifacts.get("execution")
    if isinstance(ex, dict):
        ex["orchestration_feedback"] = summary


def _append_execution_feedback_repeat_threshold_triggers(
    ctx: _OrchestrationEvalContext,
    escalation_triggers: list[dict[str, Any]],
) -> None:
    """Stronger escalation when the same action_id accumulates repeated failed or queued_unhandled rows."""
    fb = ctx.orchestration_feedback_summary
    if not isinstance(fb, dict):
        return
    fail_counts = fb.get("recent_fail_count_by_action_id") or {}
    uh_counts = fb.get("recent_queued_unhandled_count_by_action_id") or {}
    if not isinstance(fail_counts, dict):
        fail_counts = {}
    if not isinstance(uh_counts, dict):
        uh_counts = {}
    suppressed = {str(x) for x in (ctx.orchestration_failed_deprioritize_suppressed_action_ids or []) if str(x).strip()}

    for aid in sorted(fail_counts.keys()):
        raw_n = fail_counts.get(aid)
        try:
            n = int(raw_n)
        except (TypeError, ValueError):
            continue
        if n < EXECUTION_FEEDBACK_REPEAT_FAIL_THRESHOLD:
            continue
        sa = str(aid).strip()
        if sa in suppressed:
            continue
        escalation_triggers.append(
            {
                "code": RC_ORCH_FEEDBACK_REPEATED_FAILURES,
                "action_id": sa,
                "recent_fail_count": n,
                "threshold": EXECUTION_FEEDBACK_REPEAT_FAIL_THRESHOLD,
                "detail": (
                    f"action_id {sa!r} has {n} failed orchestration feedback row(s) in lookback "
                    f"(threshold {EXECUTION_FEEDBACK_REPEAT_FAIL_THRESHOLD})"
                ),
            }
        )

    for aid in sorted(uh_counts.keys()):
        raw_n = uh_counts.get(aid)
        try:
            n = int(raw_n)
        except (TypeError, ValueError):
            continue
        if n < EXECUTION_FEEDBACK_REPEAT_QUEUED_UNHANDLED_THRESHOLD:
            continue
        sa = str(aid).strip()
        escalation_triggers.append(
            {
                "code": RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
                "action_id": sa,
                "recent_queued_unhandled_count": n,
                "threshold": EXECUTION_FEEDBACK_REPEAT_QUEUED_UNHANDLED_THRESHOLD,
                "detail": (
                    f"action_id {sa!r} has {n} queued_unhandled orchestration feedback row(s) in lookback "
                    f"(threshold {EXECUTION_FEEDBACK_REPEAT_QUEUED_UNHANDLED_THRESHOLD})"
                ),
            }
        )


def _repeat_threshold_headline_suffix(ctx: _OrchestrationEvalContext) -> str | None:
    frags: list[str] = []
    for t in ctx.escalation_triggers or []:
        if not isinstance(t, dict):
            continue
        c = str(t.get("code") or "")
        if c == RC_ORCH_FEEDBACK_REPEATED_FAILURES:
            aid = str(t.get("action_id") or "")
            n = t.get("recent_fail_count")
            frags.append(
                f"repeated failed orchestration feedback for {aid} (lookback count {n}, threshold {EXECUTION_FEEDBACK_REPEAT_FAIL_THRESHOLD})"
            )
        elif c == RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED:
            aid = str(t.get("action_id") or "")
            n = t.get("recent_queued_unhandled_count")
            frags.append(
                f"repeated queued_unhandled orchestration feedback for {aid} (lookback count {n}, threshold {EXECUTION_FEEDBACK_REPEAT_QUEUED_UNHANDLED_THRESHOLD})"
            )
    if not frags:
        return None
    return "; ".join(sorted(frags))


_IDEA_REFINEMENT_TERMINAL_STATUSES = frozenset(
    {
        SessionStatus.APPROVED.value,
        SessionStatus.APPROVED_WITH_RISKS.value,
        SessionStatus.REJECTED.value,
    }
)


def _idea_refinement_fleet_eligibility_facts(ctx: _OrchestrationEvalContext) -> dict[str, Any]:
    """Compact corridor flags for fleet / operator surfaces (uses final ``ctx.deduped``)."""
    idea_sess = pick_latest_session(ctx.sessions, ArtifactType.IDEA)
    present_nt = bool(
        idea_sess is not None and str(idea_sess.status) not in _IDEA_REFINEMENT_TERMINAL_STATUSES
    )
    sub_elig = any(a.action_id == ACTION_REFINEMENT_SUBMIT_REVIEWS_IN for a in ctx.deduped)
    run_idea = False
    if idea_sess:
        sid = idea_sess.session_id
        for a in ctx.deduped:
            if a.action_id == ACTION_REFINEMENT_RUN and sid in str(a.reason or ""):
                run_idea = True
                break
    return {
        "idea_refinement_session_present_non_terminal": present_nt,
        "refinement_submit_reviews_in_eligible": sub_elig,
        "refinement_run_eligible_for_idea_session": run_idea,
    }


def _stage_determine_overall_and_review_state(ctx: _OrchestrationEvalContext) -> None:
    root = ctx.root
    sessions = ctx.sessions
    ps_sess = ctx.ps_sess
    ip_sess = ctx.ip_sess
    deduped = ctx.deduped
    waiting = ctx.waiting

    ctx.human_exhausted = any(
        sess and sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value
        for sess in (ps_sess, ip_sess)
    )
    ctx.stuck = any(refinement_not_converged_stuck(root, s) for s in sessions)
    ctx.rejected = any(s.status == SessionStatus.REJECTED.value for s in sessions)

    ctx.artifacts["refinement"]["review_state"] = _refinement_review_state(
        root, sessions, ps_sess, ip_sess, waiting
    )

    if waiting:
        ctx.overall = STATUS_BLOCKED_WAITING_INPUT
    elif ctx.stuck or ctx.human_exhausted or ctx.rejected:
        ctx.overall = STATUS_BLOCKED_EXHAUSTED
    elif deduped:
        ctx.overall = STATUS_ELIGIBLE
    else:
        ctx.overall = STATUS_NO_ACTION

    ctx.eligibility_facts = {
        "signals_collection_time_stale": _is_stale_signals(ctx.sig, ctx.now),
        "temporal_worst_freshness_status": ctx.temp.worst_freshness_status,
        "temporal_freshness_stale": _temporal_worst_is_stale(ctx.temp),
        "signals_refresh_needed": _is_stale_signals_combined(ctx.sig, ctx.temp, ctx.now),
        "audit_bundle_time_stale": _is_stale_audit(ctx.aud, ctx.now),
        "audit_product_gap_incomplete": _audit_product_gap_incomplete(ctx.aud),
        "audit_product_gap_partial": _audit_product_gap_partial(ctx.aud),
        "audit_security_stub": _audit_security_stub(ctx.aud),
        "compound_escalation_dimensions": ctx.compound_dimensions,
        "product_spec_finalized": bool(ctx.ps_final),
        "implementation_plan_session_absent": ctx.ip_absent,
        "implementation_plan_generate_eligible": bool(ctx.impl_elig)
        and not _import_blocks_action_id(ctx.import_readiness_tier, ACTION_IMPLEMENTATION_PLAN_GENERATE),
        **{
            spec["eligible_flag_name"]: spec["gate_fn"](ctx)
            and not _import_blocks_action_id(ctx.import_readiness_tier, aid)
            for aid, spec in ACTION_ELIGIBILITY_REGISTRY.items()
        },
        "execution_outcomes_apply_pending": pending_unapplied_execution_outcomes_for_product(
            root, ctx.product_id
        ),
        "orchestration_feedback_latest": ctx.orchestration_feedback_summary.get("latest"),
        "orchestration_feedback_by_action_id": ctx.orchestration_feedback_summary.get("by_action_id"),
        "orchestration_feedback_failed_action_ids": list(
            ctx.orchestration_feedback_summary.get("failed_action_ids") or []
        ),
        "orchestration_feedback_queued_unhandled_action_ids": list(
            ctx.orchestration_feedback_summary.get("queued_unhandled_action_ids") or []
        ),
        "orchestration_feedback_recent_failed": ctx.orchestration_feedback_summary.get("recent_failed", False),
        "orchestration_feedback_recent_queued_unhandled": ctx.orchestration_feedback_summary.get(
            "recent_queued_unhandled", False
        ),
        "orchestration_feedback_failed_deprioritize_suppressed_action_ids": list(
            ctx.orchestration_failed_deprioritize_suppressed_action_ids
        ),
        "orchestration_feedback_recent_fail_count_by_action_id": dict(
            ctx.orchestration_feedback_summary.get("recent_fail_count_by_action_id") or {}
        ),
        "orchestration_feedback_recent_queued_unhandled_count_by_action_id": dict(
            ctx.orchestration_feedback_summary.get("recent_queued_unhandled_count_by_action_id") or {}
        ),
        "orchestration_feedback_last_success_finished_at_utc_by_action_id": dict(
            ctx.orchestration_feedback_summary.get("last_success_finished_at_utc_by_action_id") or {}
        ),
        **_idea_refinement_fleet_eligibility_facts(ctx),
        "import_readiness_tier": ctx.import_readiness_tier,
    }


def _stage_evaluate_escalation_posture(ctx: _OrchestrationEvalContext) -> None:
    root = ctx.root
    now = ctx.now
    aud = ctx.aud
    sessions = ctx.sessions
    ps_sess = ctx.ps_sess
    ip_sess = ctx.ip_sess
    compound_dimensions = ctx.compound_dimensions

    escalation_triggers: list[dict[str, Any]] = []
    for s in sessions:
        if refinement_not_converged_stuck(root, s):
            escalation_triggers.append({"code": "refinement_not_converged_stuck", "session_id": s.session_id})
            lr = max(0, s.max_rounds - 1)
            if reviews_round_has_blocking_grounded(root, s.session_id, lr):
                escalation_triggers.append(
                    {
                        "code": RC_CONVERGENCE_BLOCKING_GROUNDED,
                        "session_id": s.session_id,
                        "round": lr,
                        "detail": "converged false at max rounds with grounded blocking reviews",
                    }
                )
    for s in sessions:
        if s.status == SessionStatus.REJECTED.value:
            escalation_triggers.append({"code": "refinement_session_rejected", "session_id": s.session_id})
    if ps_sess and ps_sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        escalation_triggers.append(
            {
                "code": "refinement_human_review_required",
                "session_id": ps_sess.session_id,
                "artifact_type": "product_spec",
            }
        )
    if ip_sess and ip_sess.status == SessionStatus.HUMAN_REVIEW_REQUIRED.value:
        escalation_triggers.append(
            {
                "code": "refinement_human_review_required",
                "session_id": ip_sess.session_id,
                "artifact_type": "implementation_plan",
            }
        )
    for s in sessions:
        if refinement_reviews_in_missing(root, s):
            t = parse_iso_timestamp(s.updated_at_utc)
            if t is not None and now - t > timedelta(hours=REVIEW_INPUT_WAIT_ESCALATION_HOURS):
                escalation_triggers.append(
                    {
                        "code": RC_REVIEW_INPUT_WAIT_EXCEEDED,
                        "session_id": s.session_id,
                        "detail": (
                            f"in_review with missing reviews_in beyond {REVIEW_INPUT_WAIT_ESCALATION_HOURS:.0f}h"
                        ),
                    }
                )
    if _audit_product_gap_incomplete(aud):
        escalation_triggers.append({"code": RC_AUDIT_PRODUCT_GAP_INCOMPLETE})
    if _audit_security_stub(aud):
        escalation_triggers.append({"code": RC_AUDIT_SECURITY_STUB})
    if _audit_product_gap_partial(aud):
        escalation_triggers.append({"code": RC_AUDIT_PRODUCT_GAP_PARTIAL})
    if compound_dimensions >= 2:
        escalation_triggers.append(
            {"code": RC_COMPOUND_CRITICAL_ARTIFACTS, "compound_escalation_dimensions": compound_dimensions}
        )

    fb = ctx.orchestration_feedback_summary
    if fb.get("recent_failed"):
        escalation_triggers.append(
            {
                "code": RC_ORCH_FEEDBACK_FAILED,
                "detail": "orchestration in-process step failed within feedback lookback (see blockers / eligibility_facts)",
            }
        )
    if fb.get("recent_queued_unhandled"):
        escalation_triggers.append(
            {
                "code": RC_ORCH_FEEDBACK_QUEUED_UNHANDLED,
                "detail": "orchestration step returned queued_unhandled within lookback (executor gap)",
            }
        )

    _append_execution_feedback_repeat_threshold_triggers(ctx, escalation_triggers)

    ctx.escalation_triggers = escalation_triggers
    ctx.escalation_eligible = bool(escalation_triggers)
    ctx.stale_refresh_needed = (
        _is_stale_signals_combined(ctx.sig, ctx.temp, ctx.now)
        or _is_stale_audit(ctx.aud, ctx.now)
        or _audit_product_gap_incomplete(ctx.aud)
        or _audit_security_stub(ctx.aud)
    )
    ctx.orch_escalated = ctx.stuck or ctx.human_exhausted or ctx.rejected
    ctx.approval_pending = bool(ctx.pending_approvals)


def _stage_determine_orchestration_headline_status(ctx: _OrchestrationEvalContext) -> None:
    if ctx.import_orchestration_status_override is not None:
        ctx.orch_status, ctx.orch_reason = ctx.import_orchestration_status_override
        return
    orch_status, orch_reason = _orchestration_status_and_reason(
        waiting=ctx.waiting,
        escalated=ctx.orch_escalated,
        approval_pending=ctx.approval_pending,
        stale_signals_or_audit=ctx.stale_refresh_needed,
        has_eligible=bool(ctx.deduped),
    )
    ctx.orch_status = orch_status
    ctx.orch_reason = orch_reason


def _stage_evaluate_waiting_inputs(ctx: _OrchestrationEvalContext) -> None:
    root = ctx.root
    product_id = ctx.product_id
    now = ctx.now
    sig = ctx.sig
    temp = ctx.temp
    aud = ctx.aud
    sessions = ctx.sessions
    pending_approvals = ctx.pending_approvals
    impl_elig = ctx.impl_elig
    impl_codes = ctx.impl_codes
    ps_final = ctx.ps_final
    ip_absent = ctx.ip_absent

    waiting_inputs: list[dict[str, Any]] = []
    for s in sessions:
        if refinement_reviews_in_missing(root, s):
            r = s.current_round
            rip = reviews_in_path(root, s.session_id, r)
            waiting_inputs.append(
                {
                    "kind": WAITING_KIND_REFINEMENT_GROUNDED_INPUT,
                    "reason_codes": ["refinement_reviews_in_missing"],
                    "detail": (
                        f"session {s.session_id} is in_review: add reviews_in/round_{r}.json "
                        f"before argus refine run"
                    ),
                    "session_id": s.session_id,
                    "expected_paths": [str(rip)],
                }
            )
        elif refinement_cycle_incomplete(root, s):
            r = s.current_round
            rp = reviews_path(root, s.session_id, r)
            waiting_inputs.append(
                {
                    "kind": WAITING_KIND_REFINEMENT_GROUNDED_INPUT,
                    "reason_codes": ["refinement_cycle_incomplete"],
                    "detail": (
                        f"session {s.session_id}: draft exists for round {r} but reviews/round_{r}.json missing"
                    ),
                    "session_id": s.session_id,
                    "expected_paths": [str(rp)],
                }
            )
    if bool(ps_final) and ip_absent and not impl_elig and impl_codes:
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_IMPL_PLAN_PREREQUISITE,
                "reason_codes": sorted(impl_codes),
                "detail": "implementation_plan_generate blocked until listed prerequisites are satisfied",
            }
        )
    if pending_approvals:
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_EXECUTION_APPROVAL,
                "reason_codes": [RC_APPROVAL_PENDING],
                "detail": f"{len(pending_approvals)} pending approval record(s) for this product",
                "approval_ids": [str(x.get("approval_id", "")) for x in pending_approvals],
            }
        )
    if not sig.present:
        lp = signals_latest_path(root, product_id)
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_OBSERVABILITY_SIGNALS,
                "reason_codes": [RC_SIGNALS_BUNDLE_ABSENT],
                "detail": f"signals bundle missing at {lp}",
                "expected_paths": [str(lp)],
            }
        )
    elif _is_stale_signals(sig, now):
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_OBSERVABILITY_SIGNALS,
                "reason_codes": [RC_SIGNALS_TIME_STALE],
                "detail": f"signals collection older than {SIGNAL_STALE_HOURS}h or unreadable timestamp",
            }
        )
    if _temporal_worst_is_stale(temp):
        w = temp.worst_freshness_status or ""
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_OBSERVABILITY_TEMPORAL,
                "reason_codes": [RC_TEMPORAL_WORST_STALE],
                "detail": f"temporal worst_freshness_status is {w!r} (refresh via signals collect)",
            }
        )
    if _is_stale_audit(aud, now):
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_OBSERVABILITY_AUDIT,
                "reason_codes": [RC_AUDIT_TIME_STALE],
                "detail": f"audit bundle missing or older than {AUDIT_STALE_DAYS}d",
            }
        )
    if _audit_product_gap_incomplete(aud):
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_OBSERVABILITY_AUDIT,
                "reason_codes": [RC_AUDIT_PRODUCT_GAP_INCOMPLETE],
                "detail": "audit product_gap angle missing or stub — run argus audit run",
            }
        )
    if _audit_security_stub(aud):
        waiting_inputs.append(
            {
                "kind": WAITING_KIND_OBSERVABILITY_AUDIT,
                "reason_codes": [RC_AUDIT_SECURITY_STUB],
                "detail": "audit security angle is stub — run argus audit run",
            }
        )
    waiting_inputs.extend(ctx.orchestration_feedback_waiting_inputs)
    waiting_inputs[:0] = ctx.import_waiting_inputs_extra
    waiting_inputs.sort(key=lambda x: (str(x.get("kind", "")), ",".join(x.get("reason_codes") or [])))
    ctx.waiting_inputs = waiting_inputs


def _stage_prioritize_next_action_and_reason_codes(ctx: _OrchestrationEvalContext) -> None:
    from argus.orchestrator.next_action_policy import NextActionPolicyInput, resolve_next_action
    from argus.planning.orchestration_priority import load_planning_snapshot_for_priority

    ctx.eligibility_facts["eligible_actions_order_rule"] = ctx.eligible_actions_order_rule

    waiting = ctx.waiting
    deduped = ctx.deduped
    stuck = ctx.stuck
    human_exhausted = ctx.human_exhausted
    rejected = ctx.rejected

    policy_in = NextActionPolicyInput(
        waiting=waiting,
        deduped_action_ids=tuple(a.action_id for a in deduped),
        stuck=stuck,
        human_exhausted=human_exhausted,
        rejected=rejected,
        import_readiness_tier=ctx.import_readiness_tier,
        readiness_reason=ctx.readiness_reason,
        repo_root=ctx.root,
        product_id=ctx.product_id,
    )
    policy_result = resolve_next_action(policy_in)
    ctx.next_action = policy_result.next_action
    ctx.next_action_policy = policy_result.to_payload_dict()
    pm = policy_result.planning_meta
    ctx.eligibility_facts["planning_mode_considered"] = pm.get("planning_mode_considered")
    ctx.eligibility_facts["planning_priority_adjustment_applied"] = pm.get(
        "planning_priority_adjustment_applied", False
    )
    ctx.eligibility_facts["next_action_planning_note"] = pm.get("next_action_planning_note")
    ctx.eligibility_facts["next_action_rule_applied"] = policy_result.rule_applied
    ctx.eligibility_facts["next_action_tie_break"] = policy_result.tie_break

    # Back-compat: surface planning_mode from snapshot when soft priority did not set it (matches prior
    # ``_merge_planning_explainability`` behavior for waiting / no-eligible branches).
    if ctx.eligibility_facts.get("planning_mode_considered") is None:
        snap = load_planning_snapshot_for_priority(ctx.root, ctx.product_id)
        if isinstance(snap, dict) and snap.get("planning_mode"):
            ctx.eligibility_facts["planning_mode_considered"] = str(snap["planning_mode"])

    reason_codes_flat: list[str] = []
    for wi in ctx.waiting_inputs:
        reason_codes_flat.extend([str(c) for c in (wi.get("reason_codes") or []) if str(c).strip()])
    for t in ctx.escalation_triggers or []:
        if not isinstance(t, dict):
            continue
        c = str(t.get("code") or "").strip()
        if c in (RC_ORCH_FEEDBACK_REPEATED_FAILURES, RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED):
            reason_codes_flat.append(c)
    rr = _retry_reopened_posture(
        feedback_summary=ctx.orchestration_feedback_summary,
        suppressed_action_ids=ctx.orchestration_failed_deprioritize_suppressed_action_ids,
        next_action=ctx.next_action,
        deduped=ctx.deduped,
    )
    if rr.get("active"):
        reason_codes_flat.append(RC_ORCH_RETRY_REOPENED)
    t = ctx.import_readiness_tier
    if t == "failed":
        reason_codes_flat.append(RC_IMPORT_FIRST_PASS_FAILED)
    elif t in ("partial", "skipped"):
        reason_codes_flat.append(RC_IMPORT_FIRST_PASS_PARTIAL)
    ctx.orchestration_status_reason_codes = sorted(set(reason_codes_flat))


def _stage_enrich_headline_with_execution_feedback(ctx: _OrchestrationEvalContext) -> None:
    parts: list[str] = []
    retry = _retry_reopened_headline_suffix(
        feedback_summary=ctx.orchestration_feedback_summary,
        suppressed_action_ids=ctx.orchestration_failed_deprioritize_suppressed_action_ids,
        next_action=ctx.next_action,
        deduped=ctx.deduped,
    )
    if retry:
        parts.append(retry)
    fb = _execution_feedback_headline_suffix(
        feedback_summary=ctx.orchestration_feedback_summary,
        next_action=ctx.next_action,
        deduped_action_ids={a.action_id for a in ctx.deduped},
    )
    if fb:
        parts.append(fb)
    rep = _repeat_threshold_headline_suffix(ctx)
    if rep:
        parts.append(rep)
    if not parts:
        return
    ctx.orch_reason = f"{ctx.orch_reason} — {'; '.join(parts)}"


def _stage_assemble_orchestration_payload(ctx: _OrchestrationEvalContext) -> dict[str, Any]:
    readiness = build_readiness_section(
        product_id=ctx.product_id,
        root=ctx.root,
        import_state=ctx.import_state,
        import_readiness_tier=ctx.import_readiness_tier,
        import_health=ctx.import_health,
        artifacts=ctx.artifacts,
        eligibility_facts=ctx.eligibility_facts,
        waiting_inputs=ctx.waiting_inputs,
        orchestration_status=ctx.orch_status,
        next_action=ctx.next_action,
    )
    return {
        "schema": "argus.orchestration_state.v1",
        "product_id": ctx.product_id,
        "evaluated_at_utc": ctx.now_iso,
        "import_health": ctx.import_health,
        "readiness_reason": ctx.readiness_reason,
        "orchestration_status": ctx.orch_status,
        "orchestration_status_reason": ctx.orch_reason,
        "orchestration_status_reason_codes": ctx.orchestration_status_reason_codes,
        "orchestration_posture": _build_orchestration_posture(ctx),
        "waiting_inputs": ctx.waiting_inputs,
        "overall_status": ctx.overall,
        "signals_stale_hours": SIGNAL_STALE_HOURS,
        "audit_stale_days": AUDIT_STALE_DAYS,
        "artifacts": ctx.artifacts,
        "eligibility_facts": ctx.eligibility_facts,
        "escalation_eligible": ctx.escalation_eligible,
        "escalation_triggers": ctx.escalation_triggers,
        "eligible_actions": [
            {"action_id": x.action_id, "reason": x.reason, "reason_codes": list(x.reason_codes)}
            for x in ctx.deduped
        ],
        "blockers": ctx.blockers,
        "next_action": ctx.next_action,
        "next_action_policy": ctx.next_action_policy,
        "progression": {"implementation_plan": ctx.progression_impl_plan},
        "readiness": readiness,
    }


def evaluate_product_orchestration(repo_root: Path, product_id: str) -> dict[str, Any]:
    ctx = _stage_load_snapshots(repo_root, product_id)
    _stage_build_artifacts_dict(ctx)
    _stage_evaluate_blockers(ctx)
    _stage_evaluate_eligible_actions(ctx)
    _stage_apply_orchestration_execution_feedback(ctx)
    _apply_import_readiness_gates(ctx)
    _stage_determine_overall_and_review_state(ctx)
    _stage_evaluate_escalation_posture(ctx)
    _stage_determine_orchestration_headline_status(ctx)
    _stage_evaluate_waiting_inputs(ctx)
    _stage_prioritize_next_action_and_reason_codes(ctx)
    _stage_enrich_headline_with_execution_feedback(ctx)
    return _stage_assemble_orchestration_payload(ctx)
