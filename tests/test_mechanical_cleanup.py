"""Tests for mechanical idea deduplication, caps, and diversity buckets."""

from __future__ import annotations

import unittest

from argus.idea_generation.mechanical_cleanup import (
    mechanical_cleanup,
    mechanical_rank_score,
    normalize_idea_text,
)
from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.idea_generation.synthesis_grounding import build_grounding


def _idea(
    *,
    iid: str,
    title: str,
    desc: str,
    source: IdeaSource = IdeaSource.SYNTHESIS,
    ev: float = 0.5,
    conf: float = 0.5,
    nov: float = 0.3,
    signal_hygiene: dict | None = None,
    signal_provenance: dict | None = None,
) -> Idea:
    return Idea(
        idea_id=iid,
        title=title,
        description=desc,
        type=IdeaType.EXPLORE,
        source=source,
        novelty_score=nov,
        adjacency_score=0.4,
        expected_value_score=ev,
        confidence_score=conf,
        cost_estimate="small",
        channel_type="web",
        monetization_type="hybrid",
        rationale="test",
        signal_hygiene=signal_hygiene,
        signal_provenance=signal_provenance,
    )


class TestNormalize(unittest.TestCase):
    def test_strips_punct(self) -> None:
        self.assertEqual(normalize_idea_text("Hello, World!!"), "hello world")


class TestDedupe(unittest.TestCase):
    def test_exact_duplicate_removed(self) -> None:
        a = _idea(iid="1", title="Same", desc="Body one")
        b = _idea(iid="2", title="Same", desc="Body one")
        out, meta = mechanical_cleanup([a, b], max_ideas=10, max_per_bucket=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(meta["rejection_counts"]["rejected_exact_duplicate"], 1)

    def test_near_duplicate_removed(self) -> None:
        # Punctuation normalizes away; these collapse to the same string → exact dedup.
        a = _idea(
            iid="1",
            title="Alpha",
            desc="The quick brown fox jumps over the lazy dog repeatedly today",
        )
        b = _idea(
            iid="2",
            title="Alpha",
            desc="The quick brown fox jumps over the lazy dog repeatedly today!",
        )
        out, meta = mechanical_cleanup([a, b], max_ideas=10, max_per_bucket=5)
        self.assertEqual(len(out), 1)
        self.assertEqual(meta["rejection_counts"]["rejected_exact_duplicate"], 1)

    def test_near_duplicate_jaccard(self) -> None:
        body = "grow revenue improve retention ship features faster " * 8
        a = _idea(iid="1", title="Same title", desc=body + " alpha")
        b = _idea(iid="2", title="Same title", desc=body + " beta")
        out, meta = mechanical_cleanup([a, b], max_ideas=10, max_per_bucket=5)
        self.assertEqual(len(out), 1)
        self.assertGreaterEqual(meta["rejection_counts"]["rejected_near_duplicate"], 1)

    def test_cap(self) -> None:
        ideas = [_idea(iid=str(i), title=f"T{i}", desc=f"Unique body {i}") for i in range(20)]
        out, meta = mechanical_cleanup(ideas, max_ideas=5, max_per_bucket=99)
        self.assertEqual(len(out), 5)
        self.assertEqual(meta["ideas_output"], 5)
        self.assertEqual(meta["rejection_counts"]["rejected_ranked_out"], 15)


class TestHygieneDownrank(unittest.TestCase):
    def test_manifest_ranks_below_clean_signal(self) -> None:
        weak = _idea(
            iid="w",
            title="Weak",
            desc="x",
            source=IdeaSource.SIGNALS,
            ev=0.9,
            conf=0.9,
            signal_hygiene={
                "is_manifest_declaration": True,
                "is_placeholder": False,
                "is_sentinel_timestamp": False,
                "signal_quality_score": "low",
            },
            signal_provenance={"signal_type": "metrics", "adapter_source": "manifest_declaration"},
        )
        strong = _idea(
            iid="s",
            title="Strong",
            desc="y",
            source=IdeaSource.SIGNALS,
            ev=0.5,
            conf=0.5,
            signal_hygiene={
                "is_manifest_declaration": False,
                "is_placeholder": False,
                "is_sentinel_timestamp": False,
                "signal_quality_score": "high",
            },
            signal_provenance={"signal_type": "metrics", "adapter_source": "filesystem"},
        )
        self.assertGreater(mechanical_rank_score(strong), mechanical_rank_score(weak))


class TestQualityThreshold(unittest.TestCase):
    def test_stops_below_max_when_tail_is_weak_synthesis(self) -> None:
        hi = _idea(
            iid="strong",
            title="Finding grounded",
            desc="unique alpha body for quality test",
            source=IdeaSource.SYNTHESIS,
            ev=0.9,
            conf=0.9,
            nov=0.3,
        )
        hi.grounding = build_grounding(
            grounding_kind="finding",
            grounding_strength="high",
            grounding_sources=[
                {"type": "finding", "id": "f1", "kind": "gap"},
                {"type": "artifact", "path": "runs/findings/latest/p.json"},
            ],
        )
        fillers = []
        for i in range(6):
            w = _idea(
                iid=f"w{i}",
                title=f"Weak pad {i}",
                desc=f"distinct weak body {i} xyz",
                source=IdeaSource.SYNTHESIS,
                ev=0.9,
                conf=0.9,
                nov=0.3,
            )
            w.grounding = build_grounding(
                grounding_kind="weak",
                grounding_strength="low",
                grounding_sources=[{"type": "combinatorial", "pattern": "domain_fuse"}],
            )
            fillers.append(w)

        out, meta = mechanical_cleanup([hi] + fillers, max_ideas=12, max_per_bucket=99)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].idea_id, "strong")
        self.assertGreater(meta["rejection_counts"]["rejected_below_quality_threshold"], 0)
        self.assertTrue(meta["quality_threshold"]["stopped_early_for_quality"])
        self.assertIn("relative=0.55", meta["quality_threshold"]["quality_threshold_rule"])

    def test_keeps_multiple_strong_rows(self) -> None:
        ideas = []
        bodies = (
            "alpha zebra revenue cohort retention dashboard export pipeline",
            "beta quartz onboarding funnel experiment clip highlight transcript",
            "gamma lithium migration backup restore integrity checksum nightly",
        )
        for i in range(3):
            x = _idea(
                iid=f"h{i}",
                title=f"Strong {i}",
                desc=bodies[i],
                source=IdeaSource.SYNTHESIS,
                ev=0.85,
                conf=0.85,
                nov=0.3,
            )
            x.grounding = build_grounding(
                grounding_kind="finding",
                grounding_strength="high",
                grounding_sources=[
                    {"type": "finding", "id": f"f{i}", "kind": "gap"},
                    {"type": "artifact", "path": "runs/findings/latest/p.json"},
                ],
            )
            ideas.append(x)
        out, meta = mechanical_cleanup(ideas, max_ideas=12, max_per_bucket=99)
        self.assertEqual(len(out), 3)
        self.assertEqual(meta["rejection_counts"]["rejected_below_quality_threshold"], 0)


class TestDiversityBucket(unittest.TestCase):
    def test_per_bucket_cap(self) -> None:
        ideas = [
            _idea(
                iid=str(i),
                title=f"S{i}",
                desc=f"distinct {i}",
                source=IdeaSource.SIGNALS,
                signal_provenance={"signal_type": "metrics", "adapter_source": "filesystem"},
            )
            for i in range(8)
        ]
        out, meta = mechanical_cleanup(ideas, max_ideas=10, max_per_bucket=2)
        self.assertEqual(len(out), 2)
        self.assertEqual(meta["rejection_counts"]["rejected_bucket_cap"], 6)


if __name__ == "__main__":
    unittest.main()
