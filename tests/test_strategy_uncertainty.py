"""Strategy mode affects confidence / uncertainty handling deterministically."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import (
    ActionType,
    EffortBucket,
    FindingKind,
    LifecycleStage,
    SeverityLevel,
    SignalType,
)
from argus.core.models.finding import Finding
from argus.core.models.signal import SignalRecord
from argus.decision.freshness import apply_freshness_to_candidates
from argus.decision.intents import DecisionIntent
from argus.decision.priority import compute_priority_score
from argus.lifecycle.model import LifecycleAssessment
from argus.signals.ids import new_signal_id
from argus.signals.persistence import save_collection
from argus.strategy.modes import PROFILE_BY_MODE, StrategyMode


def _assessment() -> LifecycleAssessment:
    return LifecycleAssessment(
        product_id="p1",
        stage=LifecycleStage.VALIDATE,
        move_forward=0.52,
        hold=0.38,
        improve=0.55,
        deprecate=0.12,
        kill=0.06,
    )


def _growth_finding(conf: float = 0.55) -> Finding:
    return Finding(
        id="f1",
        product_id="p1",
        kind=FindingKind.GROWTH_OPPORTUNITY,
        severity=SeverityLevel.MEDIUM,
        effort=EffortBucket.SMALL,
        title="t",
        summary="s",
        recommendation="r",
        source_signals=["s1"],
        evidence={},
        confidence=conf,
        created_at=datetime.now(timezone.utc),
    )


def _experiment_candidate(conf: float = 0.55) -> DecisionCandidate:
    return DecisionCandidate(
        id="d1",
        product_id="p1",
        action_type=ActionType.CUSTOM,
        summary="experiment",
        metadata={"intent": DecisionIntent.LAUNCH_EXPERIMENT.value, "finding_id": "f1"},
        confidence=conf,
        priority_score=None,
    )


class TestLeapOfFaithPriority(unittest.TestCase):
    def test_exploration_ranks_experiment_higher_than_profit_same_context(self) -> None:
        """Same inputs: exploration lift vs profit damp → exploration scores higher."""
        a = _assessment()
        f = _growth_finding(0.55)
        c = _experiment_candidate(0.55)
        ex = PROFILE_BY_MODE[StrategyMode.EXPLORATION]
        pr = PROFILE_BY_MODE[StrategyMode.PROFIT]
        se = compute_priority_score(
            c, finding=f, assessment=a, monthly_spend=5.0, spend_cap=20.0, strategy_profile=ex
        )
        sp = compute_priority_score(
            c, finding=f, assessment=a, monthly_spend=5.0, spend_cap=20.0, strategy_profile=pr
        )
        self.assertGreater(se, sp)

    def test_survival_damps_more_than_profit(self) -> None:
        a = _assessment()
        f = _growth_finding(0.6)
        c = _experiment_candidate(0.6)
        pr = PROFILE_BY_MODE[StrategyMode.PROFIT]
        su = PROFILE_BY_MODE[StrategyMode.SURVIVAL]
        sp = compute_priority_score(
            c, finding=f, assessment=a, monthly_spend=5.0, spend_cap=20.0, strategy_profile=pr
        )
        ss = compute_priority_score(
            c, finding=f, assessment=a, monthly_spend=5.0, spend_cap=20.0, strategy_profile=su
        )
        self.assertGreater(sp, ss)

    def test_exploration_lift_within_guardrails_not_above_ceiling(self) -> None:
        """High base confidence: lift has little room; scores stay finite and ordered."""
        a = _assessment()
        f = _growth_finding(0.95)
        c = _experiment_candidate(0.95)
        ex = PROFILE_BY_MODE[StrategyMode.EXPLORATION]
        pr = PROFILE_BY_MODE[StrategyMode.PROFIT]
        se = compute_priority_score(
            c, finding=f, assessment=a, monthly_spend=5.0, spend_cap=20.0, strategy_profile=ex
        )
        sp = compute_priority_score(
            c, finding=f, assessment=a, monthly_spend=5.0, spend_cap=20.0, strategy_profile=pr
        )
        self.assertLessEqual(se, 100.0)
        self.assertLessEqual(sp, 100.0)
        self.assertGreaterEqual(se, sp)


class TestFreshnessFactorsByStrategy(unittest.TestCase):
    def test_stale_bundle_exploration_retains_higher_confidence_than_survival(self) -> None:
        """Same stale operational signals: exploration uses gentler freshness factors."""
        fixed = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        old = fixed - timedelta(hours=200)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _, _ = save_collection(
                root,
                pid,
                [
                    SignalRecord(
                        id=new_signal_id(),
                        product_id=pid,
                        signal_type=SignalType.METRICS,
                        source="t",
                        observed_at=old,
                        payload={"n": 1},
                        confidence=0.9,
                    )
                ],
            )
            f = _growth_finding(0.75)
            c = _experiment_candidate(0.75)
            assessment = _assessment()

            ex = PROFILE_BY_MODE[StrategyMode.EXPLORATION]
            su = PROFILE_BY_MODE[StrategyMode.SURVIVAL]

            with patch("argus.decision.freshness._utc_now", return_value=fixed):
                out_ex = apply_freshness_to_candidates(
                    root,
                    pid,
                    [f],
                    [c],
                    assessment,
                    monthly_spend=5.0,
                    spend_cap=20.0,
                    strategy_profile=ex,
                    now=fixed,
                )
            c2 = _experiment_candidate(0.75)
            with patch("argus.decision.freshness._utc_now", return_value=fixed):
                out_su = apply_freshness_to_candidates(
                    root,
                    pid,
                    [f],
                    [c2],
                    assessment,
                    monthly_spend=5.0,
                    spend_cap=20.0,
                    strategy_profile=su,
                    now=fixed,
                )

            self.assertTrue(out_ex[0].metadata.get("stale_data_affected_confidence"))
            self.assertGreater(out_ex[0].confidence or 0, out_su[0].confidence or 0)
            self.assertEqual(
                out_ex[0].metadata.get("freshness_confidence_factors", {}).get("stale"),
                ex.freshness_stale_confidence_factor,
            )
            self.assertEqual(
                out_su[0].metadata.get("freshness_confidence_factors", {}).get("stale"),
                su.freshness_stale_confidence_factor,
            )


if __name__ == "__main__":
    unittest.main()
