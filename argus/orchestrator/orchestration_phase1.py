"""
Map orchestration ``action_id`` strings to Phase 1 permission keys.

In-process orchestration mutates ``runs/`` and local product artifacts; it does not
invoke ``git push`` or cloud deploy from this layer. Keys are chosen to match intent:

- ``observe_prod_signals``: read/collect observability (signals, audit, temporal).
- ``change_experiments``: experiment lifecycle and apply-from-execution.
- ``mutate_nonprod``: other local pipeline writes (findings, decisions, refinement, …).

``signals_collect`` requires both observation and (bundled) experiment outcome apply — both keys must pass.
"""

from __future__ import annotations

from typing import Any, Iterable

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
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_TEMPORAL_REFRESH,
)
from argus.project_permissions.schema import PHASE1_KEYS as _PHASE1_SCHEMA_KEYS

# Canonical location for governance mapping (Track D — do not bypass Phase 1).
ORCHESTRATION_PHASE1_MAPPING_REF = (
    "argus.orchestrator.orchestration_phase1.ORCHESTRATION_ACTION_PHASE1_KEYS"
)


class OrchestrationPhase1MappingError(RuntimeError):
    """Registry and ``ORCHESTRATION_ACTION_PHASE1_KEYS`` are out of sync (fail-fast)."""


# action_id -> ordered keys (all must allow for execution)
ORCHESTRATION_ACTION_PHASE1_KEYS: dict[str, tuple[str, ...]] = {
    ACTION_SIGNALS_COLLECT: ("observe_prod_signals", "change_experiments"),
    ACTION_AUDIT_RUN: ("observe_prod_signals",),
    ACTION_TEMPORAL_REFRESH: ("observe_prod_signals",),
    ACTION_EXECUTION_OUTCOMES_APPLY: ("change_experiments",),
    ACTION_EXPERIMENTS_PROPOSE: ("change_experiments",),
    ACTION_EXPERIMENTS_PRIORITIZE: ("change_experiments",),
    ACTION_EXPERIMENTS_CREATE: ("change_experiments",),
    ACTION_EXPERIMENTS_ACTIVATE: ("change_experiments",),
    ACTION_EXPERIMENTS_EVALUATE: ("change_experiments",),
    ACTION_EXPERIMENTS_CLOSE_STALE: ("change_experiments",),
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: ("change_experiments",),
    # Local pipeline / refinement / strategy — all under non-prod mutation of repo artifacts
    ACTION_ORCHESTRATION_STATE_REFRESH: ("mutate_nonprod",),
    ACTION_REFINEMENT_START_PRODUCT_SPEC: ("mutate_nonprod",),
    ACTION_REFINEMENT_START_IDEA: ("mutate_nonprod",),
    ACTION_IMPLEMENTATION_PLAN_GENERATE: ("mutate_nonprod",),
    ACTION_REFINEMENT_RUN: ("mutate_nonprod",),
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN: ("mutate_nonprod",),
    ACTION_ESCALATION_CONSIDER: ("mutate_nonprod",),
    ACTION_ESCALATION_PACKET_GENERATE: ("mutate_nonprod",),
    ACTION_FINDINGS_GENERATE: ("mutate_nonprod",),
    ACTION_DECISIONS_GENERATE: ("mutate_nonprod",),
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: ("mutate_nonprod",),
    ACTION_IDEAS_GENERATE: ("mutate_nonprod",),
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: ("mutate_nonprod",),
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION: ("mutate_nonprod",),
    ACTION_PLANNING_REFRESH_FROM_STRATEGY: ("mutate_nonprod",),
}


ORCHESTRATION_ACTION_DESCRIPTIONS: dict[str, str] = {
    ACTION_SIGNALS_COLLECT: "collect signals bundle and apply execution outcomes to experiments",
    ACTION_AUDIT_RUN: "run audit bundle for product",
    ACTION_TEMPORAL_REFRESH: "refresh temporal grounding from latest signals",
    ACTION_EXECUTION_OUTCOMES_APPLY: "apply pending execution outcomes to experiments",
    ACTION_EXPERIMENTS_PROPOSE: "propose experiments",
    ACTION_EXPERIMENTS_PRIORITIZE: "prioritize experiments",
    ACTION_EXPERIMENTS_CREATE: "create experiment records",
    ACTION_EXPERIMENTS_ACTIVATE: "activate experiments",
    ACTION_EXPERIMENTS_EVALUATE: "evaluate experiments",
    ACTION_EXPERIMENTS_CLOSE_STALE: "close stale experiments",
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: "surface findings into experiments",
    ACTION_ORCHESTRATION_STATE_REFRESH: "refresh orchestration state snapshot",
    ACTION_REFINEMENT_START_PRODUCT_SPEC: "start product spec refinement session",
    ACTION_REFINEMENT_START_IDEA: "start idea refinement session",
    ACTION_IMPLEMENTATION_PLAN_GENERATE: "generate implementation plan",
    ACTION_REFINEMENT_RUN: "run refinement cycle",
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN: "submit refinement reviews_in",
    ACTION_ESCALATION_CONSIDER: "consider escalation",
    ACTION_ESCALATION_PACKET_GENERATE: "generate escalation packet",
    ACTION_FINDINGS_GENERATE: "generate findings from signals",
    ACTION_DECISIONS_GENERATE: "generate decisions",
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: "refresh decisions from surfaced findings",
    ACTION_IDEAS_GENERATE: "generate ideas",
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: "refresh ideas from surfaced findings",
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION: "refresh strategy from decision evolution",
    ACTION_PLANNING_REFRESH_FROM_STRATEGY: "refresh planning from strategy",
}

_VALID_PHASE1 = frozenset(_PHASE1_SCHEMA_KEYS)


def validate_orchestration_registry_phase1_mapping(registry_action_ids: Iterable[str]) -> None:
    """
    Ensure every in-process orchestration handler has exactly one Phase 1 mapping entry
    and that each mapped key is a valid Phase 1 permission field.

    Call at module load with ``STEP_EXECUTION_REGISTRY.keys()`` and in CI/tests.
    Raises :class:`OrchestrationPhase1MappingError` on any mismatch (no fallback).
    """
    reg = {str(x).strip() for x in registry_action_ids if str(x).strip()}
    mkeys = set(ORCHESTRATION_ACTION_PHASE1_KEYS.keys())
    missing = sorted(reg - mkeys)
    extra = sorted(mkeys - reg)
    bad_tuples: list[str] = []
    for aid, tup in ORCHESTRATION_ACTION_PHASE1_KEYS.items():
        for k in tup:
            if k not in _VALID_PHASE1:
                bad_tuples.append(f"{aid!r} references unknown Phase 1 key {k!r}")
    parts: list[str] = []
    if missing:
        parts.append(
            "Unmapped orchestration action(s) (in STEP_EXECUTION_REGISTRY but missing from "
            f"{ORCHESTRATION_PHASE1_MAPPING_REF}): {missing!r}. "
            "Add a tuple of Phase 1 keys for each action_id.",
        )
    if extra:
        parts.append(
            "Extra mapping entry(ies) (in ORCHESTRATION_ACTION_PHASE1_KEYS but not in "
            f"STEP_EXECUTION_REGISTRY): {extra!r}. Remove stale keys or register the handler.",
        )
    if bad_tuples:
        parts.append("Invalid Phase 1 key in mapping: " + "; ".join(bad_tuples))
    if parts:
        raise OrchestrationPhase1MappingError("\n".join(parts))


def orchestration_phase1_mapping_report() -> dict[str, Any]:
    """Deterministic action_id → Phase 1 keys for operators / CI (read-only)."""
    rows = [
        {
            "action_id": aid,
            "phase1_keys": list(ORCHESTRATION_ACTION_PHASE1_KEYS[aid]),
        }
        for aid in sorted(ORCHESTRATION_ACTION_PHASE1_KEYS.keys())
    ]
    return {
        "schema": "argus.orchestration_phase1_mapping_report.v1",
        "mapping_table": ORCHESTRATION_PHASE1_MAPPING_REF,
        "action_count": len(rows),
        "actions": rows,
    }


def phase1_keys_for_orchestration_action(action_id: str) -> tuple[str, ...] | None:
    """Return Phase 1 keys for this orchestration action, or None if not governed here."""
    aid = str(action_id).strip()
    if not aid:
        return None
    return ORCHESTRATION_ACTION_PHASE1_KEYS.get(aid)


def action_description_for_orchestration(action_id: str) -> str:
    aid = str(action_id).strip()
    return ORCHESTRATION_ACTION_DESCRIPTIONS.get(aid, f"orchestration action {aid!r}")


__all__ = [
    "ORCHESTRATION_ACTION_DESCRIPTIONS",
    "ORCHESTRATION_ACTION_PHASE1_KEYS",
    "ORCHESTRATION_PHASE1_MAPPING_REF",
    "OrchestrationPhase1MappingError",
    "action_description_for_orchestration",
    "orchestration_phase1_mapping_report",
    "phase1_keys_for_orchestration_action",
    "validate_orchestration_registry_phase1_mapping",
]
