"""Tests for deterministic experiment evaluation."""

from __future__ import annotations

import unittest

from argus.experiments.evaluate import (
    _trend_adjustment,
    _type_score,
    _verdict_from_score,
    evaluate_experiment,
)
from argus.experiments.models import (
    EvaluationVerdict,
    Experiment,
    ExperimentType,
)
from argus.history.models import ProductSnapshot
from argus.trends.models import TrendFlag


def _snap(
    *,
    findings: int = 0,
    cost: float | None = 100.0,
    priority: float | None = 50.0,
    esc: int = 0,
) -> ProductSnapshot:
    return ProductSnapshot(
        snapshot_id="s",
        product_id="p",
        observed_at_utc="2026-01-01T00:00:00+00:00",
        state="",
        status="",
        lifecycle_stage="grow",
        monthly_cost_usd=cost,
        last_signal_at=None,
        active_findings_count=findings,
        findings_by_severity={},
        top_recommended_action="",
        priority_score=priority,
        top_confidence=None,
        escalation_count=esc,
        lifecycle_scores={},
        kill_candidate=False,
        source_paths={},
    )


class TestTypeScore(unittest.TestCase):
    def test_growth_success_signals(self) -> None:
        exp = Experiment(
            id="e1",
            product_id="p",
            hypothesis="h",
            type=ExperimentType.GROWTH,
            description="",
            expected_outcome="",
        )
        before = _snap(findings=10, priority=40.0)
        after = _snap(findings=5, priority=55.0)
        score, _ = _type_score(exp, before, after)
        self.assertGreater(score, 0.15)

    def test_growth_failure_signals(self) -> None:
        exp = Experiment(
            id="e2",
            product_id="p",
            hypothesis="h",
            type=ExperimentType.GROWTH,
            description="",
            expected_outcome="",
        )
        before = _snap(findings=3, priority=60.0)
        after = _snap(findings=12, priority=35.0)
        score, _ = _type_score(exp, before, after)
        self.assertLess(score, -0.15)

    def test_cost_reduction_success(self) -> None:
        exp = Experiment(
            id="e3",
            product_id="p",
            hypothesis="h",
            type=ExperimentType.COST_REDUCTION,
            description="",
            expected_outcome="",
        )
        before = _snap(cost=200.0)
        after = _snap(cost=150.0)
        score, _ = _type_score(exp, before, after)
        self.assertGreater(score, 0.1)


class TestVerdict(unittest.TestCase):
    def test_inconclusive_no_snapshots(self) -> None:
        self.assertEqual(
            _verdict_from_score(0.5, False, False),
            EvaluationVerdict.INCONCLUSIVE,
        )

    def test_success_with_pair(self) -> None:
        self.assertEqual(
            _verdict_from_score(0.3, True, False),
            EvaluationVerdict.SUCCESS,
        )

    def test_failed_with_pair(self) -> None:
        self.assertEqual(
            _verdict_from_score(-0.4, True, False),
            EvaluationVerdict.FAILED,
        )

    def test_partial_band(self) -> None:
        self.assertEqual(
            _verdict_from_score(0.05, True, False),
            EvaluationVerdict.PARTIAL_SUCCESS,
        )

    def test_inconclusive_weak_with_trend_only(self) -> None:
        self.assertEqual(
            _verdict_from_score(0.05, True, True),
            EvaluationVerdict.INCONCLUSIVE,
        )


class TestTrendAdjustment(unittest.TestCase):
    def test_improving_bumps(self) -> None:
        adj, _ = _trend_adjustment([TrendFlag.IMPROVING.value])
        self.assertGreater(adj, 0)


class TestEvaluateExperimentNoHistory(unittest.TestCase):
    def test_inconclusive_without_timeline(self) -> None:
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = Experiment(
                id="ex",
                product_id="nohist",
                hypothesis="h",
                type=ExperimentType.GROWTH,
                description="",
                expected_outcome="",
                start_at="2026-06-01T12:00:00+00:00",
            )
            ev = evaluate_experiment(root, exp)
            self.assertEqual(ev.verdict, EvaluationVerdict.INCONCLUSIVE)


if __name__ == "__main__":
    unittest.main()
