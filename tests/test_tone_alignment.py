"""Tests for decision/findings tone alignment on idea ranking (no rewrites)."""

from __future__ import annotations

import unittest

from argus.idea_generation.mechanical_cleanup import mechanical_rank_score
from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.idea_generation.tone_alignment import (
    compute_tone_alignment_for_idea,
    derive_decision_tone,
    max_finding_severity,
)


def _idea(title: str, desc: str, *, source: IdeaSource = IdeaSource.SYNTHESIS) -> Idea:
    return Idea(
        idea_id="i1",
        title=title,
        description=desc,
        type=IdeaType.EXPLORE,
        source=source,
        novelty_score=0.4,
        adjacency_score=0.4,
        expected_value_score=0.7,
        confidence_score=0.7,
        cost_estimate="small",
        channel_type="web",
        monetization_type="hybrid",
        rationale="rationale text",
    )


class TestDeriveDecisionTone(unittest.TestCase):
    def test_cautious_low_bucket(self) -> None:
        dc = {
            "confidence_bucket": "low",
            "uncertainty_score": 0.6,
            "risk_score": 0.55,
            "escalation_recommendation": "gather_data",
            "escalation_pressure": 0.6,
            "exploratory_action_recommended": True,
            "confidence_score": 0.2,
        }
        self.assertEqual(derive_decision_tone(dc), "cautious")

    def test_assertive(self) -> None:
        dc = {
            "confidence_bucket": "high",
            "uncertainty_score": 0.2,
            "risk_score": 0.3,
            "escalation_recommendation": "proceed",
            "escalation_pressure": 0.2,
            "exploratory_action_recommended": False,
            "confidence_score": 0.8,
        }
        self.assertEqual(derive_decision_tone(dc), "assertive")


class TestComputeToneAlignment(unittest.TestCase):
    def test_cautious_downranks_rollout_language(self) -> None:
        idea = _idea(
            "Scale",
            "Full roll-out to production at scale this quarter.",
        )
        out = compute_tone_alignment_for_idea(
            idea,
            decision_tone="cautious",
            top_candidate_confidence=0.4,
            max_finding_severity_label="medium",
            max_finding_severity_ordinal=2,
            has_findings=True,
        )
        self.assertEqual(out["tag"], "overstated")
        self.assertLess(out["rank_multiplier"], 0.95)
        self.assertTrue(any("cautious_decision" in r for r in out.get("reasons", [])))

    def test_low_severity_downranks_urgency(self) -> None:
        idea = _idea("X", "This is urgent — ship now and pivot the whole stack.")
        out = compute_tone_alignment_for_idea(
            idea,
            decision_tone="balanced",
            top_candidate_confidence=None,
            max_finding_severity_label="low",
            max_finding_severity_ordinal=1,
            has_findings=True,
        )
        self.assertEqual(out["tag"], "overstated")
        self.assertLess(out["rank_multiplier"], 1.0)

    def test_aligned_when_no_trigger(self) -> None:
        idea = _idea("Y", "Try a small experiment; measure one metric.")
        out = compute_tone_alignment_for_idea(
            idea,
            decision_tone="cautious",
            top_candidate_confidence=0.5,
            max_finding_severity_label="low",
            max_finding_severity_ordinal=1,
            has_findings=True,
        )
        self.assertEqual(out["tag"], "aligned")
        self.assertEqual(out["rank_multiplier"], 1.0)


class TestMechanicalRankUsesTone(unittest.TestCase):
    def test_multiplier_lowers_rank(self) -> None:
        a = _idea("T", "neutral description without hype")
        b = _idea("T", "neutral description without hype")
        a.tone_alignment = {"rank_multiplier": 1.0, "tag": "aligned"}
        b.tone_alignment = {"rank_multiplier": 0.4, "tag": "overstated"}
        self.assertGreater(mechanical_rank_score(a), mechanical_rank_score(b))


class TestMaxFindingSeverity(unittest.TestCase):
    def test_empty(self) -> None:
        self.assertEqual(max_finding_severity([]), (None, -1))


if __name__ == "__main__":
    unittest.main()
