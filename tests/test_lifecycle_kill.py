"""Tests for kill criteria scoring."""

from __future__ import annotations

import unittest

from argus.lifecycle.kill import (
    KillRecommendation,
    combine_dimension_scores,
    recommendation_for_score,
)


class TestCombineDimensions(unittest.TestCase):
    def test_high_cost_no_usage_high_kill_score(self) -> None:
        """High spend with no value and silence should land in deprecate/kill band."""
        ks = combine_dimension_scores(
            inactivity=0.95,
            findings_trend=0.55,
            cost_vs_value=0.95,
            experiment_failure=0.45,
            decision_churn=0.35,
            escalation=0.3,
        )
        self.assertGreaterEqual(ks, 55)
        self.assertIn(recommendation_for_score(ks), (KillRecommendation.DEPRECATE, KillRecommendation.KILL))

    def test_active_growth_low_kill_score(self) -> None:
        """Strong value vs cost and fresh activity should score low."""
        ks = combine_dimension_scores(
            inactivity=0.05,
            findings_trend=0.1,
            cost_vs_value=0.08,
            experiment_failure=0.05,
            decision_churn=0.1,
            escalation=0.0,
        )
        self.assertLessEqual(ks, 30)
        self.assertEqual(recommendation_for_score(ks), KillRecommendation.CONTINUE)

    def test_recommendation_thresholds(self) -> None:
        self.assertEqual(recommendation_for_score(0), KillRecommendation.CONTINUE)
        self.assertEqual(recommendation_for_score(30), KillRecommendation.HOLD)
        self.assertEqual(recommendation_for_score(60), KillRecommendation.DEPRECATE)
        self.assertEqual(recommendation_for_score(90), KillRecommendation.KILL)
