"""Strategy mode changes decision priority scores deterministically."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import (
    ActionType,
    EffortBucket,
    FindingKind,
    LifecycleStage,
    SeverityLevel,
)
from argus.core.models.finding import Finding
from argus.decision.intents import DecisionIntent
from argus.decision.priority import compute_priority_score
from argus.lifecycle.model import LifecycleAssessment
from argus.strategy.modes import PROFILE_BY_MODE, STRATEGY_EMPHASIS, StrategyMode


def _fixed_assessment() -> LifecycleAssessment:
    """Fixed posture so scores differ only by strategy profile."""
    return LifecycleAssessment(
        product_id="p1",
        stage=LifecycleStage.VALIDATE,
        move_forward=0.52,
        hold=0.38,
        improve=0.55,
        deprecate=0.12,
        kill=0.06,
    )


def _cost_risk_finding() -> Finding:
    return Finding(
        id="f-cost",
        product_id="p1",
        kind=FindingKind.COST_RISK,
        severity=SeverityLevel.HIGH,
        effort=EffortBucket.SMALL,
        title="t",
        summary="s",
        recommendation="r",
        source_signals=["sig-1"],
        evidence={},
        confidence=0.72,
        created_at=datetime.now(timezone.utc),
    )


def _growth_opportunity_finding() -> Finding:
    return Finding(
        id="f-grow",
        product_id="p1",
        kind=FindingKind.GROWTH_OPPORTUNITY,
        severity=SeverityLevel.MEDIUM,
        effort=EffortBucket.MEDIUM,
        title="t",
        summary="s",
        recommendation="r",
        source_signals=["sig-1"],
        evidence={},
        confidence=0.68,
        created_at=datetime.now(timezone.utc),
    )


class TestStrategyPriorityDifferentiation(unittest.TestCase):
    def test_same_cost_risk_input_scores_differ_growth_vs_survival(self) -> None:
        """Survival penalizes spend harder than growth for identical COST_RISK + reduce_cost."""
        a = _fixed_assessment()
        f = _cost_risk_finding()
        c = DecisionCandidate(
            id="d1",
            product_id="p1",
            action_type=ActionType.INVESTIGATE,
            summary="reduce",
            metadata={"intent": DecisionIntent.REDUCE_COST.value},
            confidence=0.72,
            priority_score=None,
        )
        g = PROFILE_BY_MODE[StrategyMode.GROWTH]
        s = PROFILE_BY_MODE[StrategyMode.SURVIVAL]
        sg = compute_priority_score(
            c,
            finding=f,
            assessment=a,
            monthly_spend=14.0,
            spend_cap=20.0,
            strategy_profile=g,
        )
        ss = compute_priority_score(
            c,
            finding=f,
            assessment=a,
            monthly_spend=14.0,
            spend_cap=20.0,
            strategy_profile=s,
        )
        self.assertNotEqual(sg, ss)
        self.assertGreater(sg, ss, "growth should rank cost mitigation higher than survival here")

    def test_same_experiment_input_scores_differ_exploration_vs_profit(self) -> None:
        """Exploration boosts launch_experiment vs profit on identical opportunity finding."""
        a = _fixed_assessment()
        f = _growth_opportunity_finding()
        c = DecisionCandidate(
            id="d2",
            product_id="p1",
            action_type=ActionType.CUSTOM,
            summary="run experiment",
            metadata={"intent": DecisionIntent.LAUNCH_EXPERIMENT.value},
            confidence=0.68,
            priority_score=None,
        )
        ex = PROFILE_BY_MODE[StrategyMode.EXPLORATION]
        pr = PROFILE_BY_MODE[StrategyMode.PROFIT]
        se = compute_priority_score(
            c,
            finding=f,
            assessment=a,
            monthly_spend=5.0,
            spend_cap=20.0,
            strategy_profile=ex,
        )
        sp = compute_priority_score(
            c,
            finding=f,
            assessment=a,
            monthly_spend=5.0,
            spend_cap=20.0,
            strategy_profile=pr,
        )
        self.assertNotEqual(se, sp)
        self.assertGreater(se, sp, "exploration should rank experiments above profit here")

    def test_strategy_emphasis_mapping_is_stable(self) -> None:
        self.assertEqual(STRATEGY_EMPHASIS[StrategyMode.GROWTH], "opportunity")
        self.assertEqual(STRATEGY_EMPHASIS[StrategyMode.PROFIT], "roi")
        self.assertEqual(STRATEGY_EMPHASIS[StrategyMode.EXPLORATION], "experimentation")
        self.assertEqual(STRATEGY_EMPHASIS[StrategyMode.SURVIVAL], "cost_reduction")


if __name__ == "__main__":
    unittest.main()
