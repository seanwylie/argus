"""Schemas and vocabulary for state-driven orchestration (inspectable JSON)."""

from __future__ import annotations

ORCHESTRATION_STATE_SCHEMA = "argus.orchestration_state.v1"
ORCHESTRATION_POSTURE_SCHEMA = "argus.orchestration_posture.v1"

# --- Artifact phase strings (machine-readable, stable) ---
PHASE_ABSENT = "absent"
PHASE_PRESENT = "present"
PHASE_FINALIZED = "finalized"
PHASE_IN_PROGRESS = "in_progress"
PHASE_TERMINAL_REJECTED = "terminal_rejected"
PHASE_STALE = "stale"
PHASE_FRESH = "fresh"
PHASE_NONE = "none"

# Aggregate refinement/review posture for the product (across sessions).
REFINEMENT_REVIEW_ABSENT = "absent"
REFINEMENT_REVIEW_IN_PROGRESS = "in_progress"
REFINEMENT_REVIEW_AWAITING_REVIEW_INPUT = "awaiting_review_input"
REFINEMENT_REVIEW_FINALIZED = "finalized"
REFINEMENT_REVIEW_HUMAN_REVIEW_REQUIRED = "human_review_required"
REFINEMENT_REVIEW_REJECTED = "rejected"
REFINEMENT_REVIEW_NOT_CONVERGED_STUCK = "not_converged_stuck"

# Overall orchestration status for operators and automation (legacy; see orchestration_status).
STATUS_ELIGIBLE = "eligible"
STATUS_BLOCKED_WAITING_INPUT = "blocked_waiting_input"
STATUS_BLOCKED_EXHAUSTED = "blocked_exhausted"
STATUS_NO_ACTION = "no_action"

# Canonical headline status on orchestration artifacts (mutually exclusive).
ORCH_STATUS_ELIGIBLE = "eligible"
ORCH_STATUS_BLOCKED_WAITING_INPUT = "blocked_waiting_input"
ORCH_STATUS_BLOCKED_WAITING_APPROVAL = "blocked_waiting_approval"
ORCH_STATUS_STALE_REFRESH_NEEDED = "stale_refresh_needed"
ORCH_STATUS_COMPLETE = "complete"
ORCH_STATUS_ESCALATED = "escalated"

# ``waiting_inputs[].kind`` (deterministic strings for operators / automation)
WAITING_KIND_REFINEMENT_GROUNDED_INPUT = "refinement_awaiting_grounded_input"
WAITING_KIND_IMPL_PLAN_PREREQUISITE = "implementation_plan_prerequisites_unmet"
WAITING_KIND_EXECUTION_APPROVAL = "execution_awaiting_approval"
WAITING_KIND_OBSERVABILITY_SIGNALS = "observability_signals"
WAITING_KIND_OBSERVABILITY_TEMPORAL = "observability_temporal"
WAITING_KIND_OBSERVABILITY_AUDIT = "observability_audit"
WAITING_KIND_IMPORT_FIRST_PASS = "import_first_pass"

ORCHESTRATION_INDEX_SCHEMA = "argus.orchestration_index.v1"
ORCHESTRATION_FLEET_GOVERNANCE_ROLLUP_SCHEMA = "argus.orchestration_fleet_governance_rollup.v1"
ORCHESTRATION_OPERATOR_SUMMARY_SCHEMA = "argus.orchestration_operator_summary.v1"
ORCHESTRATION_TASK_SCHEMA = "argus.orchestration_task.v1"
ORCHESTRATION_ADVANCEMENT_SCHEMA = "argus.orchestration_advancement.v1"
ORCHESTRATION_PROGRESSION_RUN_SCHEMA = "argus.orchestration_progression_run.v1"
ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA = "argus.orchestration_progression_run_artifact.v1"
ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA = "argus.orchestration_execution_feedback.v1"
ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA = "argus.orchestration_escalation_consider.v1"
ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA = "argus.orchestration_batch_advancement.v1"

# Advancement record (durable intent; does not imply subprocess execution).
ACTION_STATUS_QUEUED = "queued"
ACTION_STATUS_DISPATCHED = "dispatched"
ACTION_STATUS_BLOCKED = "blocked"
ACTION_STATUS_SKIPPED = "skipped"
ACTION_STATUS_EXECUTED = "executed"
ACTION_STATUS_FAILED = "failed"
ACTION_STATUS_QUEUED_UNHANDLED = "queued_unhandled"

# Action ids (deterministic; map to CLI in orchestrator README).
ACTION_SIGNALS_COLLECT = "signals_collect"
ACTION_AUDIT_RUN = "audit_run"
ACTION_ORCHESTRATION_STATE_REFRESH = "orchestration_state_refresh"
ACTION_REFINEMENT_START_PRODUCT_SPEC = "refinement_start_product_spec"
ACTION_REFINEMENT_START_IDEA = "refinement_start_idea"
ACTION_IMPLEMENTATION_PLAN_GENERATE = "implementation_plan_generate"
ACTION_REFINEMENT_RUN = "refinement_run"
ACTION_REFINEMENT_SUBMIT_REVIEWS_IN = "refinement_submit_reviews_in"
ACTION_ESCALATION_CONSIDER = "escalation_consider"
ACTION_EXECUTION_OUTCOMES_APPLY = "execution_outcomes_apply"
ACTION_TEMPORAL_REFRESH = "temporal_refresh"
ACTION_FINDINGS_GENERATE = "findings_generate"
ACTION_DECISIONS_GENERATE = "decisions_generate"
ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS = "decisions_refresh_from_surfaced_findings"
ACTION_IDEAS_GENERATE = "ideas_generate"
ACTION_EXPERIMENTS_PROPOSE = "experiments_propose"
ACTION_EXPERIMENTS_PRIORITIZE = "experiments_prioritize"
ACTION_EXPERIMENTS_CREATE = "experiments_create"
ACTION_EXPERIMENTS_ACTIVATE = "experiments_activate"
ACTION_EXPERIMENTS_EVALUATE = "experiments_evaluate"
ACTION_EXPERIMENTS_CLOSE_STALE = "experiments_close_stale"
ACTION_EXPERIMENTS_SURFACE_FINDINGS = "experiments_surface_findings"
ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS = "ideas_refresh_from_surfaced_findings"
ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION = "strategy_refresh_from_decision_evolution"
ACTION_PLANNING_REFRESH_FROM_STRATEGY = "planning_refresh_from_strategy"
ACTION_ESCALATION_PACKET_GENERATE = "escalation_packet_generate"

# Back-compat alias (same string).
ACTION_REFINEMENT_START_IMPLEMENTATION_PLAN = ACTION_IMPLEMENTATION_PLAN_GENERATE
