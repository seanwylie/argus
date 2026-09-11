"""
Deterministic escalation triggers (no ML).

Each rule returns zero or more :class:`TriggerMatch` records with a stable ``rule_id``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import FindingKind, LifecycleStage, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.decision.intents import DecisionIntent
from argus.escalation.builder_rule_ids import (
    RULE_BUILDER_ARGUS_CORE_BREACH,
    RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED,
    RULE_BUILDER_EXECUTION_OUTCOME_BREACHED,
    RULE_BUILDER_NON_PRODUCT_ROOT_BREACH,
    RULE_BUILDER_SEMANTIC_SCOPE_BREACH,
    RULE_BUILDER_TRUST_DIRTY_TREE,
    RULE_BUILDER_TRUST_MISSING_NO_NEW_PRIVS,
    RULE_BUILDER_TRUST_UNSANDBOXED,
)
from argus.lifecycle.model import LifecycleAssessment


@dataclass(frozen=True)
class TriggerMatch:
    """One fired rule with human-readable detail."""

    rule_id: str
    detail: str


# Stable identifiers (logged in ``EscalationPacket.triggering_rules``)
RULE_COST_OVER_CEILING = "cost_over_monthly_ceiling"
RULE_KILL_OR_DEPRECATE = "kill_or_deprecate_recommendation"
RULE_MISSING_BUSINESS_DATA = "missing_required_business_data"
RULE_LIFECYCLE_CONTRADICTION = "invalid_lifecycle_transition"
RULE_CONFIDENCE_TOO_LOW = "confidence_too_low_for_automation"
RULE_UNSAFE_CONTRACT = "dangerous_action_contract_flagged_unsafe"
RULE_ESCALATION_PRESSURE = "bounded_escalation_pressure"
RULE_LOW_CONFIDENCE_HIGH_RISK = "bounded_low_confidence_high_risk"
RULE_REPEATED_POLICY_BLOCKS = "bounded_repeated_policy_blocks"

_BUILDER_CRITICAL_RULES = frozenset(
    {
        RULE_BUILDER_ARGUS_CORE_BREACH,
        RULE_BUILDER_EXECUTION_OUTCOME_BREACHED,
    }
)
_BUILDER_HIGH_RULES = frozenset(
    {
        RULE_BUILDER_NON_PRODUCT_ROOT_BREACH,
        RULE_BUILDER_SEMANTIC_SCOPE_BREACH,
    }
)
_BUILDER_MEDIUM_RULES = frozenset(
    {
        RULE_BUILDER_EXECUTION_OUTCOME_BLOCKED,
        RULE_BUILDER_TRUST_UNSANDBOXED,
        RULE_BUILDER_TRUST_MISSING_NO_NEW_PRIVS,
        RULE_BUILDER_TRUST_DIRTY_TREE,
    }
)

# Tunable thresholds (deterministic, documented)
_CONFIDENCE_AUTOMATION_FLOOR = 0.55
_KILL_SCORE_LIFECYCLE_CONFLICT = 0.55
_ESCALATION_PRESSURE_THRESHOLD = 0.85
_CONF_WEAK = 0.35
_RISK_HIGH = 0.65
_BLOCK_STREAK_ESCALATION = 5


def _destructive_intent(c: DecisionCandidate) -> bool:
    intent = (c.metadata or {}).get("intent")
    if intent in (DecisionIntent.KILL_PRODUCT.value, DecisionIntent.DEPRECATE_PRODUCT.value):
        return True
    return c.action_type.value in ("deprecate", "archive")


def _rollout_triggers(repo_root: Path | None, product_id: str) -> list[TriggerMatch]:
    """Bounded-autonomy hard triggers (assessment, registry gaps, autonomy streak)."""
    if repo_root is None:
        return []
    root = repo_root.resolve()
    out: list[TriggerMatch] = []
    try:
        from argus.decision_assessment.persistence import load_latest_assessment

        ass = load_latest_assessment(root, product_id)
        if ass is not None:
            if float(ass.escalation_pressure) >= _ESCALATION_PRESSURE_THRESHOLD:
                out.append(
                    TriggerMatch(
                        RULE_ESCALATION_PRESSURE,
                        f"decision_assessment escalation_pressure={ass.escalation_pressure:.2f} "
                        f"(threshold {_ESCALATION_PRESSURE_THRESHOLD})",
                    )
                )
            if float(ass.confidence_score) < _CONF_WEAK and float(ass.risk_score) > _RISK_HIGH:
                out.append(
                    TriggerMatch(
                        RULE_LOW_CONFIDENCE_HIGH_RISK,
                        f"decision_assessment confidence={ass.confidence_score:.2f} "
                        f"and risk={ass.risk_score:.2f} (conf<{_CONF_WEAK}, risk>{_RISK_HIGH})",
                    )
                )
    except (OSError, TypeError, ValueError):
        pass
    try:
        from argus.autonomy.controller import load_state

        st = load_state(root)
        streak = int(float(st.get("block_streak", 0)))
        if streak >= _BLOCK_STREAK_ESCALATION:
            out.append(
                TriggerMatch(
                    RULE_REPEATED_POLICY_BLOCKS,
                    f"autonomy block_streak={streak} (threshold {_BLOCK_STREAK_ESCALATION}) — "
                    "repeated policy blocks; review autonomy tier and approvals.",
                )
            )
    except OSError:
        pass
    return out


def evaluate_triggers(
    product: ProductNode,
    findings: list[Finding],
    assessment: LifecycleAssessment,
    candidates: list[DecisionCandidate],
    *,
    repo_root: Path | None = None,
) -> list[TriggerMatch]:
    """
    Evaluate all rules; returns a de-duplicated list of matches (order: rule priority).
    """
    out: list[TriggerMatch] = []

    # --- Cost over configured ceiling ---
    cap = product.constraints.max_monthly_cost_usd
    if cap is not None:
        m = product.cost.monthly_usd
        if m is not None and float(m) > float(cap):
            out.append(
                TriggerMatch(
                    RULE_COST_OVER_CEILING,
                    f"Declared monthly_usd ({m}) exceeds constraints.max_monthly_cost_usd ({cap}).",
                )
            )
    for f in findings:
        if f.kind == FindingKind.COST_RISK and f.severity in (
            SeverityLevel.HIGH,
            SeverityLevel.CRITICAL,
        ):
            if not any(m.rule_id == RULE_COST_OVER_CEILING for m in out):
                out.append(
                    TriggerMatch(
                        RULE_COST_OVER_CEILING,
                        f"Finding {f.id}: cost risk ({f.severity.value}) — {f.title}",
                    )
                )
            break

    # --- Kill / deprecate recommendation ---
    if assessment.kill_candidate:
        out.append(
            TriggerMatch(
                RULE_KILL_OR_DEPRECATE,
                "Lifecycle assessment marks kill_candidate (high kill posture vs forward momentum).",
            )
        )
    if candidates:
        top = candidates[0]
        intent = (top.metadata or {}).get("intent")
        if intent in (
            DecisionIntent.KILL_PRODUCT.value,
            DecisionIntent.DEPRECATE_PRODUCT.value,
        ):
            out.append(
                TriggerMatch(
                    RULE_KILL_OR_DEPRECATE,
                    f"Top-ranked decision intent is {intent!r}: {top.summary}",
                )
            )

    # --- Missing required business data (high-severity finding without confidence) ---
    for f in findings:
        if f.severity in (SeverityLevel.HIGH, SeverityLevel.CRITICAL) and f.confidence is None:
            out.append(
                TriggerMatch(
                    RULE_MISSING_BUSINESS_DATA,
                    f"Finding {f.id} ({f.kind.value}) is {f.severity.value} but has no confidence score.",
                )
            )
            break

    # --- Invalid / risky lifecycle transition (growth stage + terminal posture) ---
    stage = product.lifecycle.stage
    if stage in (LifecycleStage.GROW, LifecycleStage.VALIDATE) and assessment.kill >= _KILL_SCORE_LIFECYCLE_CONFLICT:
        out.append(
            TriggerMatch(
                RULE_LIFECYCLE_CONTRADICTION,
                f"Product stage is {stage.value} but kill score is high ({assessment.kill:.3f}).",
            )
        )

    # --- Confidence too low for automated move (destructive top candidate) ---
    if candidates:
        top = candidates[0]
        if _destructive_intent(top):
            conf = top.confidence
            if conf is None or conf < _CONFIDENCE_AUTOMATION_FLOOR:
                out.append(
                    TriggerMatch(
                        RULE_CONFIDENCE_TOO_LOW,
                        f"Top candidate is destructive ({(top.metadata or {}).get('intent')!r}) "
                        f"but confidence is {conf!r} (floor {_CONFIDENCE_AUTOMATION_FLOOR}).",
                    )
                )

    # --- Dangerous action contract flagged unsafe ---
    for f in findings:
        ev = f.evidence or {}
        if ev.get("unsafe") is True or ev.get("unsafe_contract") is True:
            out.append(
                TriggerMatch(
                    RULE_UNSAFE_CONTRACT,
                    f"Finding {f.id} carries unsafe / unsafe_contract in evidence.",
                )
            )
    for c in candidates:
        md = c.metadata or {}
        if md.get("unsafe_contract") is True or md.get("unsafe") is True:
            out.append(
                TriggerMatch(
                    RULE_UNSAFE_CONTRACT,
                    f"Decision candidate {c.id} flagged unsafe in metadata.",
                )
            )

    out.extend(_rollout_triggers(repo_root, product.id))

    # De-duplicate by rule_id (keep first detail)
    seen: set[str] = set()
    deduped: list[TriggerMatch] = []
    for m in out:
        if m.rule_id not in seen:
            seen.add(m.rule_id)
            deduped.append(m)
    return deduped


def _base_rule_id(rule_id: str) -> str:
    """Orchestration matches use ``code@session_id``; compare on ``code`` for risk buckets."""
    return rule_id.split("@", 1)[0]


# Aligns with ``argus.orchestrator.eligibility`` escalation_triggers ``code`` values.
_ORCH_CRITICAL_CODES = frozenset({"refinement_session_rejected"})
_ORCH_HIGH_CODES = frozenset(
    {
        "refinement_not_converged_stuck",
        "convergence_failed_blocking_grounded_reviews",
        "refinement_human_review_required",
        "review_input_wait_exceeded",
        "audit_product_gap_stub_or_missing",
        "audit_security_stub",
        "audit_product_gap_partial",
        "compound_critical_artifact_gaps",
    }
)


def max_risk_for_matches(matches: list[TriggerMatch]) -> str:
    """Map rule set to a single RiskLevel string for the packet."""
    critical_rules = {RULE_KILL_OR_DEPRECATE, RULE_UNSAFE_CONTRACT}
    high_rules = {
        RULE_COST_OVER_CEILING,
        RULE_LIFECYCLE_CONTRADICTION,
        RULE_CONFIDENCE_TOO_LOW,
        RULE_ESCALATION_PRESSURE,
        RULE_LOW_CONFIDENCE_HIGH_RISK,
        RULE_REPEATED_POLICY_BLOCKS,
    }
    ids = {m.rule_id for m in matches}
    bases = {_base_rule_id(rid) for rid in ids}
    if bases & _ORCH_CRITICAL_CODES:
        return "critical"
    if ids & critical_rules:
        return "critical"
    if ids & _BUILDER_CRITICAL_RULES:
        return "critical"
    if bases & _ORCH_HIGH_CODES:
        return "high"
    if ids & high_rules or RULE_MISSING_BUSINESS_DATA in ids:
        return "high"
    if ids & _BUILDER_HIGH_RULES:
        return "high"
    if ids & _BUILDER_MEDIUM_RULES:
        return "medium"
    return "medium"
