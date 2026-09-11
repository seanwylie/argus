"""Score and rank self-improvement proposals."""

from __future__ import annotations

from pathlib import Path

from argus.self_improvement.models import (
    ProposalScores,
    RankedProposal,
    SelfImprovementProposal,
)
from argus.strategy.apply import load_strategy_mode
from argus.strategy.modes import StrategyMode


def _strategy_alignment_boost(mode: StrategyMode | None, proposal_kind: str) -> float:
    """Bump strategy_alignment dimension when proposal fits mode emphasis."""
    if mode is None:
        return 0.0
    k = proposal_kind
    if mode == StrategyMode.GROWTH and k in ("add_new_adapter", "improve_experiment_evaluation"):
        return 0.12
    if mode == StrategyMode.SURVIVAL and k in (
        "strengthen_artifact_validation",
        "improve_approval_flow",
        "reduce_escalation_noise",
    ):
        return 0.12
    if mode == StrategyMode.EXPLORATION and k in (
        "improve_experiment_evaluation",
        "add_loop_regression_harness",
    ):
        return 0.14
    if mode == StrategyMode.PROFIT and k in ("strengthen_artifact_validation", "improve_approval_flow"):
        return 0.1
    return 0.0


def score_proposal(
    p: SelfImprovementProposal,
    *,
    strategy_mode: StrategyMode | None = None,
) -> float:
    """
    Aggregate score from rubric. Higher is better.

    implementation_effort is a cost: higher effort lowers the score.
    """
    s: ProposalScores = p.scores
    align = min(1.0, s.strategy_alignment + _strategy_alignment_boost(strategy_mode, p.kind.value))
    # Weighted sum — weights sum to 1.0 on positive terms; effort inverted.
    return (
        0.26 * s.ecosystem_leverage
        + 0.22 * s.safety_impact
        + 0.18 * (1.0 - s.implementation_effort)
        + 0.18 * s.recurrence_of_pain
        + 0.16 * align
    )


def rank_proposals(
    proposals: list[SelfImprovementProposal],
    *,
    strategy_mode: StrategyMode | None = None,
) -> list[RankedProposal]:
    ranked = [
        RankedProposal(proposal=p, total_score=score_proposal(p, strategy_mode=strategy_mode))
        for p in proposals
    ]
    ranked.sort(key=lambda r: (-r.total_score, r.proposal.id))
    return ranked


def resolve_strategy_mode(repo_root: Path) -> StrategyMode | None:
    return load_strategy_mode(repo_root)
