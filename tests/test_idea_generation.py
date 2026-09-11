"""Tests for structured idea generation."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.advisors.idea_expansion import (
    expand_idea_with_advisors,
    uncertainty_from_advisor_consensus,
)
from argus.advisors.models import ConsensusResult
from argus.idea_generation.classify import classify_type
from argus.idea_generation.models import Idea, IdeaSource, IdeaType, new_idea_id
from argus.idea_generation.mutation import mutate_ideas
from argus.idea_generation.pipeline import run_pipeline
from argus.idea_generation.score import apply_scores, rank_key


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_valid_yaml(product_id: str = "p1") -> str:
    return f"""
    id: {product_id}
    name: Test
    owner:
      team: test
    lifecycle:
      stage: idea
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: 0
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: 1
      min_activity_threshold: 0
    """


class TestClassify(unittest.TestCase):
    def test_invent_keywords(self) -> None:
        t = classify_type("Net new platform", "Greenfield hybrid combining X and Y", source=IdeaSource.SYNTHESIS)
        self.assertEqual(t, IdeaType.INVENT)

    def test_exploit_keywords(self) -> None:
        t = classify_type("Scale conversion", "Optimize funnel and improve retention", source=IdeaSource.SIGNALS)
        self.assertEqual(t, IdeaType.EXPLOIT)


class TestPipeline(unittest.TestCase):
    def test_generate_writes_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "products" / "p1" / "product.yaml", _minimal_valid_yaml("p1"))
            (root / "products" / "p1" / "scripts").mkdir(parents=True)
            (root / "products" / "p1" / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n")

            sig = root / "runs" / "signals" / "latest" / "p1.json"
            sig.parent.mkdir(parents=True, exist_ok=True)
            sig.write_text(
                json.dumps(
                    {
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "records": [
                            {
                                "id": "s1",
                                "product_id": "p1",
                                "signal_type": "metrics",
                                "source": "test",
                                "observed_at": "2026-01-01T12:00:00+00:00",
                                "payload": {"summary": "up"},
                                "tags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            path, bundle = run_pipeline(root, "p1", seed="test", include_mutation=True)
            self.assertTrue(path.is_file())
            self.assertGreaterEqual(len(bundle.ideas), 1)
            latest = root / "runs" / "ideas" / "latest.json"
            self.assertTrue(latest.is_file())
            exp = bundle.meta.get("advisor_idea_expansion")
            self.assertIsInstance(exp, dict)
            per = exp.get("per_idea") if exp else None
            self.assertIsInstance(per, dict)
            self.assertGreater(len(per), 0)


class TestMutationEngine(unittest.TestCase):
    def _base_idea(self) -> Idea:
        return Idea(
            idea_id="parent_stable",
            title="Ship TikTok tips for indie devs",
            description="Short clips; monetize with ads.",
            type=IdeaType.EXPLORE,
            source=IdeaSource.SIGNALS,
            novelty_score=0.0,
            adjacency_score=0.0,
            expected_value_score=0.0,
            confidence_score=0.0,
            cost_estimate="small",
            channel_type="tiktok",
            monetization_type="ads",
            rationale="seed",
            product_id="p9",
        )

    def test_major_dimension_changes(self) -> None:
        base = self._base_idea()
        out = mutate_ideas([base], count=6, seed="major-test", product_id="p9", weird_ratio=0.0)
        self.assertEqual(len(out), 6)
        for idea in out:
            self.assertEqual(idea.source, IdeaSource.MUTATION)
            major_shift = (
                idea.channel_type != base.channel_type
                or idea.monetization_type != base.monetization_type
                or "audience:->" in idea.rationale
            )
            self.assertTrue(major_shift, msg=idea.rationale)

    def test_variety_across_mutations(self) -> None:
        base = self._base_idea()
        out = mutate_ideas([base], count=10, seed="variety", product_id="p9", weird_ratio=0.2)
        keys = {(x.channel_type, x.monetization_type, x.title) for x in out}
        self.assertGreaterEqual(len(keys), 3, msg="expected distinct mutations")

    def test_weird_pool_sometimes_used(self) -> None:
        base = self._base_idea()
        out = mutate_ideas([base], count=40, seed="weirdness", product_id="p9", weird_ratio=0.9)
        unconventional = any("unconventional pivot" in m.description for m in out)
        self.assertTrue(unconventional)

    def test_backward_compatible_empty_base(self) -> None:
        out = mutate_ideas([], count=2, seed="empty", product_id="px", weird_ratio=0.3)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(i.source == IdeaSource.MUTATION for i in out))


class TestAdvisorIdeaExpansion(unittest.TestCase):
    def test_expansion_does_not_replace_idea(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "products" / "p1" / "product.yaml", _minimal_valid_yaml("p1"))
            idea = Idea(
                idea_id="idea_fixed_1",
                title="Canonical title",
                description="Canonical description body.",
                type=IdeaType.INVENT,
                source=IdeaSource.SYNTHESIS,
                novelty_score=0.5,
                adjacency_score=0.5,
                expected_value_score=0.5,
                confidence_score=0.5,
                cost_estimate="small",
                channel_type="web",
                monetization_type="hybrid",
                rationale="synth",
                product_id="p1",
            )
            before = (idea.idea_id, idea.title, idea.description, idea.rationale, idea.source)
            rec = expand_idea_with_advisors(root, "p1", idea, use_llm=False)
            after = (idea.idea_id, idea.title, idea.description, idea.rationale, idea.source)
            self.assertEqual(before, after)
            self.assertGreater(len(rec.improved_description_suggestions), 0)
            self.assertGreaterEqual(rec.uncertainty_from_disagreement, 0.0)

    def test_disagreement_increases_uncertainty(self) -> None:
        low = ConsensusResult(
            product_id="p",
            repo_root="/r",
            generated_at_utc="t",
            consensus_decision="favor_measured_progress",
            disagreement_signals=[],
            confidence_score=0.92,
        )
        high = ConsensusResult(
            product_id="p",
            repo_root="/r",
            generated_at_utc="t",
            consensus_decision="favor_measured_progress",
            disagreement_signals=["a", "b", "c", "d"],
            confidence_score=0.35,
        )
        self.assertGreater(
            uncertainty_from_advisor_consensus(high),
            uncertainty_from_advisor_consensus(low),
        )


class TestScores(unittest.TestCase):
    def test_apply_scores_clipped(self) -> None:
        idea = Idea(
            idea_id=new_idea_id("idea"),
            title="T",
            description="New hybrid approach",
            type=IdeaType.INVENT,
            source=IdeaSource.SYNTHESIS,
            novelty_score=0.0,
            adjacency_score=0.0,
            expected_value_score=0.0,
            confidence_score=0.0,
            cost_estimate="small",
            channel_type="web",
            monetization_type="hybrid",
            rationale="r",
        )
        apply_scores(idea, seed="s")
        self.assertGreater(idea.novelty_score, 0.0)
        self.assertLessEqual(idea.confidence_score, 1.0)

    def test_rank_key(self) -> None:
        a = Idea(
            idea_id="a",
            title="",
            description="",
            type=IdeaType.EXPLOIT,
            source=IdeaSource.SIGNALS,
            novelty_score=0.5,
            adjacency_score=0.5,
            expected_value_score=0.9,
            confidence_score=0.9,
            cost_estimate="s",
            channel_type="w",
            monetization_type="h",
            rationale="",
        )
        self.assertGreater(rank_key(a), 0.0)


if __name__ == "__main__":
    unittest.main()
