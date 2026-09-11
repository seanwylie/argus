"""Map self-findings to improvement proposals (recommendations only)."""

from __future__ import annotations

from argus.self_improvement.models import (
    ProposalScores,
    SelfChangeRisk,
    SelfFindingKind,
    SelfImprovementFinding,
    SelfImprovementProposal,
    SelfProposalKind,
    new_proposal_id,
)


def _base_scores_for_kind(kind: SelfProposalKind) -> ProposalScores:
    """Default rubric hints per proposal class."""
    if kind == SelfProposalKind.ADD_ADAPTER:
        return ProposalScores(
            ecosystem_leverage=0.85,
            safety_impact=0.55,
            implementation_effort=0.55,
            recurrence_of_pain=0.5,
            strategy_alignment=0.6,
        )
    if kind == SelfProposalKind.STRENGTHEN_ARTIFACT_VALIDATION:
        return ProposalScores(
            ecosystem_leverage=0.75,
            safety_impact=0.9,
            implementation_effort=0.35,
            recurrence_of_pain=0.45,
            strategy_alignment=0.55,
        )
    if kind == SelfProposalKind.IMPROVE_APPROVAL_FLOW:
        return ProposalScores(
            ecosystem_leverage=0.7,
            safety_impact=0.65,
            implementation_effort=0.4,
            recurrence_of_pain=0.75,
            strategy_alignment=0.5,
        )
    if kind == SelfProposalKind.IMPROVE_EXPERIMENT_EVALUATION:
        return ProposalScores(
            ecosystem_leverage=0.72,
            safety_impact=0.5,
            implementation_effort=0.45,
            recurrence_of_pain=0.55,
            strategy_alignment=0.58,
        )
    if kind == SelfProposalKind.ADD_DOCS:
        return ProposalScores(
            ecosystem_leverage=0.55,
            safety_impact=0.35,
            implementation_effort=0.25,
            recurrence_of_pain=0.4,
            strategy_alignment=0.45,
        )
    if kind == SelfProposalKind.IMPROVE_RESUME_SEMANTICS:
        return ProposalScores(
            ecosystem_leverage=0.65,
            safety_impact=0.45,
            implementation_effort=0.5,
            recurrence_of_pain=0.5,
            strategy_alignment=0.52,
        )
    if kind == SelfProposalKind.ADD_LOOP_REGRESSION_HARNESS:
        return ProposalScores(
            ecosystem_leverage=0.8,
            safety_impact=0.75,
            implementation_effort=0.5,
            recurrence_of_pain=0.48,
            strategy_alignment=0.55,
        )
    if kind == SelfProposalKind.REDUCE_ESCALATION_NOISE:
        return ProposalScores(
            ecosystem_leverage=0.68,
            safety_impact=0.6,
            implementation_effort=0.42,
            recurrence_of_pain=0.85,
            strategy_alignment=0.5,
        )
    return ProposalScores(
        ecosystem_leverage=0.5,
        safety_impact=0.5,
        implementation_effort=0.5,
        recurrence_of_pain=0.5,
        strategy_alignment=0.5,
    )


def _risk_for_proposal(kind: SelfProposalKind) -> tuple[SelfChangeRisk, bool]:
    """(risk tier, requires explicit approval for self-change)."""
    if kind in (
        SelfProposalKind.STRENGTHEN_ARTIFACT_VALIDATION,
        SelfProposalKind.IMPROVE_APPROVAL_FLOW,
        SelfProposalKind.IMPROVE_RESUME_SEMANTICS,
    ):
        return SelfChangeRisk.HIGH, True
    if kind in (
        SelfProposalKind.ADD_ADAPTER,
        SelfProposalKind.ADD_LOOP_REGRESSION_HARNESS,
    ):
        return SelfChangeRisk.MEDIUM, False
    return SelfChangeRisk.LOW, False


def proposals_from_findings(findings: list[SelfImprovementFinding]) -> list[SelfImprovementProposal]:
    """One proposal per finding (deterministic mapping)."""
    out: list[SelfImprovementProposal] = []
    for f in findings:
        kind: SelfProposalKind
        title: str
        summary: str

        if f.kind == SelfFindingKind.MISSING_CAPABILITY:
            kind = SelfProposalKind.ADD_ADAPTER
            title = "Add or extend capability adapter"
            summary = f"Address: {f.title}. {f.summary}"
        elif f.kind == SelfFindingKind.WEAK_TEST_COVERAGE:
            kind = SelfProposalKind.ADD_LOOP_REGRESSION_HARNESS
            title = "Expand tests / loop regression coverage"
            summary = f.summary
        elif f.kind == SelfFindingKind.REPEATED_ESCALATION_PATTERN:
            kind = SelfProposalKind.REDUCE_ESCALATION_NOISE
            title = "Tune escalation generation and resolution paths"
            summary = f.summary
        elif f.kind == SelfFindingKind.DECISION_CHURN_ARGUS:
            kind = SelfProposalKind.IMPROVE_RESUME_SEMANTICS
            title = "Stabilize decision memory and churn heuristics"
            summary = f.summary
        elif f.kind == SelfFindingKind.MISSING_SCHEMA_VALIDATION:
            kind = SelfProposalKind.STRENGTHEN_ARTIFACT_VALIDATION
            title = "Strengthen manifest and artifact validation"
            summary = f.summary
        elif f.kind == SelfFindingKind.MISSING_INTEGRATION_PATH:
            kind = SelfProposalKind.ADD_ADAPTER
            title = "Add missing integration path (signals / ingestion)"
            summary = f.summary
        elif f.kind == SelfFindingKind.POOR_EXECUTION_FEEDBACK:
            kind = SelfProposalKind.IMPROVE_EXPERIMENT_EVALUATION
            title = "Improve execution and experiment feedback loops"
            summary = f.summary
        elif f.kind == SelfFindingKind.OPERATOR_FRICTION:
            kind = SelfProposalKind.IMPROVE_APPROVAL_FLOW
            title = "Reduce approval friction (clarity + automation boundaries)"
            summary = f.summary
        else:
            kind = SelfProposalKind.PLATFORM_OTHER
            title = "Platform improvement (review)"
            summary = f.summary

        scores = _base_scores_for_kind(kind)
        risk, appr = _risk_for_proposal(kind)
        if f.suggested_severity == "high":
            scores.recurrence_of_pain = min(1.0, scores.recurrence_of_pain + 0.12)
            scores.safety_impact = min(1.0, scores.safety_impact + 0.08)

        out.append(
            SelfImprovementProposal(
                id=new_proposal_id(),
                kind=kind,
                title=title,
                summary=summary,
                source_finding_ids=[f.id],
                scores=scores,
                risk=risk,
                requires_operator_approval=appr,
                rationale=f"Derived from finding {f.id} ({f.kind.value}).",
            )
        )
    return out
