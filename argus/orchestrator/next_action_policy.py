"""
Declarative policy for orchestration ``next_action`` selection.

Eligibility (what is allowed) is computed elsewhere; this module decides which single
eligible action is recommended as ``next_action``, in a fixed precedence order.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.orchestrator.state_models import (
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
)
from argus.planning.orchestration_priority import pick_next_action_soft_planning

NEXT_ACTION_POLICY_SCHEMA = "argus.next_action_policy.v1"

# --- Action families (for inspectability; does not affect selection) ---
_OBSERVABILITY_ACTION_IDS: frozenset[str] = frozenset(
    {
        "signals_collect",
        "temporal_refresh",
        "audit_run",
        "orchestration_state_refresh",
    }
)
_GENERATION_CHAIN_ACTION_IDS: frozenset[str] = frozenset(
    {
        "findings_generate",
        "decisions_generate",
        "ideas_generate",
        "decisions_refresh_from_surfaced_findings",
        "ideas_refresh_from_surfaced_findings",
        "strategy_refresh_from_decision_evolution",
        "planning_refresh_from_strategy",
        "escalation_packet_generate",
    }
)
_EXPERIMENTS_ACTION_IDS: frozenset[str] = frozenset(
    {
        "experiments_propose",
        "experiments_prioritize",
        "experiments_create",
        "experiments_activate",
        "experiments_evaluate",
        "experiments_close_stale",
        "experiments_surface_findings",
    }
)
_REFINEMENT_ACTION_IDS: frozenset[str] = frozenset(
    {
        "refinement_start_product_spec",
        "refinement_start_idea",
        "refinement_run",
        "refinement_submit_reviews_in",
    }
)
_GOVERNANCE_ACTION_IDS: frozenset[str] = frozenset(
    {
        "escalation_consider",
        "execution_outcomes_apply",
    }
)


def action_family(action_id: str) -> str:
    """Coarse bucket for ``next_action_policy.action_family`` (display / analytics)."""
    if action_id in _OBSERVABILITY_ACTION_IDS:
        return "observability"
    if action_id in _GENERATION_CHAIN_ACTION_IDS:
        return "generation"
    if action_id in _EXPERIMENTS_ACTION_IDS:
        return "experiments"
    if action_id in _REFINEMENT_ACTION_IDS:
        return "refinement"
    if action_id in _GOVERNANCE_ACTION_IDS:
        return "governance"
    if action_id == ACTION_IMPLEMENTATION_PLAN_GENERATE:
        return "progression"
    return "other"


@dataclass(frozen=True)
class NextActionPolicyInput:
    """Inputs from orchestration evaluation (post-import-gate ``deduped`` order)."""

    waiting: bool
    deduped_action_ids: tuple[str, ...]
    stuck: bool
    human_exhausted: bool
    rejected: bool
    import_readiness_tier: str | None
    readiness_reason: str
    repo_root: Path
    product_id: str


@dataclass
class NextActionPolicyResult:
    """Outcome of :func:`resolve_next_action` (merged into orchestration state)."""

    next_action: str
    rule_applied: str
    ordering_basis: str
    tie_break: str | None
    influences: dict[str, Any]
    planning_meta: dict[str, Any] = field(default_factory=dict)
    action_family: str = "other"

    def to_payload_dict(self) -> dict[str, Any]:
        return {
            "schema": NEXT_ACTION_POLICY_SCHEMA,
            "rule_applied": self.rule_applied,
            "ordering_basis": self.ordering_basis,
            "tie_break": self.tie_break,
            "influences": dict(self.influences),
            "planning": dict(self.planning_meta),
            "action_family": self.action_family,
        }


def _planning_meta_waiting_skipped() -> dict[str, Any]:
    return {
        "planning_mode_considered": None,
        "planning_priority_adjustment_applied": False,
        "next_action_planning_note": "waiting_input: planning soft priority not applied to next_action",
    }


def _planning_meta_impl_precedence() -> dict[str, Any]:
    return {
        "planning_mode_considered": None,
        "planning_priority_adjustment_applied": False,
        "next_action_planning_note": (
            "implementation_plan_generate progression gate takes precedence over planning soft priority"
        ),
    }


def _influences_base(inp: NextActionPolicyInput) -> dict[str, Any]:
    tier = inp.import_readiness_tier
    out: dict[str, Any] = {
        "import_readiness_tier": tier,
        "readiness_gating": tier is not None and tier not in ("success", "unknown"),
    }
    rr = (inp.readiness_reason or "").strip()
    if rr:
        out["readiness_reason_excerpt"] = rr[:280] + ("..." if len(rr) > 280 else "")
    if tier in ("failed", "partial", "skipped"):
        out["import_tier_influence"] = (
            "eligible_actions already filtered by import tier; next_action chosen from that set only"
        )
    return out


def resolve_next_action(inp: NextActionPolicyInput) -> NextActionPolicyResult:
    """
    Select ``next_action`` using fixed policy precedence:

    1. **waiting** — refinement cycle waiting for grounded input: prefer
       ``refinement_submit_reviews_in`` if eligible, else ``none``; no planning soft priority.
    2. **progression** — if ``implementation_plan_generate`` is eligible and refinement is not
       blocked (not stuck / human_exhausted / rejected), take it over planning nudges.
    3. **soft planning** — otherwise use :func:`pick_next_action_soft_planning` on the ordered
       eligible id list (canonical order = first id is default head; planning may reorder).
    4. **none** — no eligible actions.

    Tie-break: planning uses score ``boost - index`` (see ``planning/orchestration_priority.py``);
    otherwise first matching rule wins.
    """
    influences = _influences_base(inp)
    ids = list(inp.deduped_action_ids)
    ordering_basis = "eligible_actions_order_after_state_hygiene_and_execution_feedback"

    # 1) Refinement waiting — observability vs generation is deferred until input lands.
    if inp.waiting:
        na = (
            ACTION_REFINEMENT_SUBMIT_REVIEWS_IN
            if ACTION_REFINEMENT_SUBMIT_REVIEWS_IN in ids
            else "none"
        )
        fam = action_family(na) if na != "none" else "none"
        return NextActionPolicyResult(
            next_action=na,
            rule_applied="waiting_refinement_submit_or_none",
            ordering_basis=ordering_basis,
            tie_break="prefer refinement_submit_reviews_in when present, else none",
            influences=influences,
            planning_meta=_planning_meta_waiting_skipped(),
            action_family=fam,
        )

    if not ids:
        return NextActionPolicyResult(
            next_action="none",
            rule_applied="no_eligible_actions",
            ordering_basis=ordering_basis,
            tie_break=None,
            influences=influences,
            planning_meta={
                "planning_mode_considered": None,
                "planning_priority_adjustment_applied": False,
                "next_action_planning_note": None,
            },
            action_family="none",
        )

    # 2) Product-spec → implementation plan progression beats soft planning.
    prefer_impl = (
        ACTION_IMPLEMENTATION_PLAN_GENERATE in ids
        and not inp.stuck
        and not inp.human_exhausted
        and not inp.rejected
    )
    if prefer_impl:
        influences["progression_preference"] = "implementation_plan_generate over planning soft priority"
        return NextActionPolicyResult(
            next_action=ACTION_IMPLEMENTATION_PLAN_GENERATE,
            rule_applied="progression_implementation_plan_generate",
            ordering_basis=ordering_basis,
            tie_break="product_spec→implementation_plan gate when refinement posture allows",
            influences=influences,
            planning_meta=_planning_meta_impl_precedence(),
            action_family=action_family(ACTION_IMPLEMENTATION_PLAN_GENERATE),
        )

    # 3) Soft planning nudges among already-ordered eligible ids (canonical head = ids[0]).
    na, pmeta = pick_next_action_soft_planning(ids, repo_root=inp.repo_root, product_id=inp.product_id)
    head = ids[0]
    if na == head:
        tb = "canonical head (first eligible after ordering); planning did not promote another id"
    else:
        tb = f"planning soft priority promoted {na!r} over canonical head {head!r}"
    influences["soft_planning"] = {
        "canonical_head": head,
        "selected": na,
        "planning_priority_adjustment_applied": bool(pmeta.get("planning_priority_adjustment_applied")),
    }
    return NextActionPolicyResult(
        next_action=na,
        rule_applied="soft_planning_pick_next_action",
        ordering_basis=ordering_basis,
        tie_break=tb,
        influences=influences,
        planning_meta={
            "planning_mode_considered": pmeta.get("planning_mode_considered"),
            "planning_priority_adjustment_applied": pmeta.get("planning_priority_adjustment_applied", False),
            "next_action_planning_note": pmeta.get("next_action_planning_note"),
        },
        action_family=action_family(na) if na != "none" else "none",
    )
