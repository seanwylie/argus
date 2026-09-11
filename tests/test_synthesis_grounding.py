"""Tests for synthesis grounding metadata and mechanical rank factors."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import to_jsonable
from argus.idea_generation.mechanical_cleanup import mechanical_rank_score
from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.idea_generation.pipeline import run_pipeline
from argus.idea_generation.score import rank_key
from argus.idea_generation.synthesis import synthesize_ideas
from argus.idea_generation.synthesis_grounding import (
    build_grounding,
    synthesis_grounding_rank_multiplier,
)


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _product_yaml(pid: str) -> str:
    return f"""
    id: {pid}
    name: Product {pid}
    owner:
      team: test
    lifecycle:
      stage: validate
    type: micro_saas
    status: active
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


def _base_synthesis_idea(**kwargs: object) -> Idea:
    defaults = dict(
        idea_id="idea_test",
        title="T",
        description="D",
        type=IdeaType.INVENT,
        source=IdeaSource.SYNTHESIS,
        novelty_score=0.5,
        adjacency_score=0.4,
        expected_value_score=0.6,
        confidence_score=0.7,
        cost_estimate="medium",
        channel_type="web",
        monetization_type="ads",
        rationale="r",
        product_id="p1",
        grounding=None,
    )
    defaults.update(kwargs)
    return Idea(**defaults)  # type: ignore[arg-type]


class TestGroundingMultiplier(unittest.TestCase):
    def test_high_beats_low(self) -> None:
        hi = _base_synthesis_idea(
            grounding=build_grounding(
                grounding_kind="finding",
                grounding_strength="high",
                grounding_sources=[{"type": "finding", "id": "find-x"}],
            ),
        )
        lo = _base_synthesis_idea(
            idea_id="idea_lo",
            grounding=build_grounding(
                grounding_kind="weak",
                grounding_strength="low",
                grounding_sources=[{"type": "combinatorial"}],
            ),
        )
        self.assertGreater(synthesis_grounding_rank_multiplier(hi), synthesis_grounding_rank_multiplier(lo))
        self.assertGreater(mechanical_rank_score(hi), mechanical_rank_score(lo))

    def test_non_synthesis_unchanged(self) -> None:
        sig = _base_synthesis_idea(source=IdeaSource.SIGNALS, type=IdeaType.EXPLOIT)
        self.assertEqual(synthesis_grounding_rank_multiplier(sig), 1.0)

    def test_missing_grounding_downranked(self) -> None:
        bare = _base_synthesis_idea(grounding=None)
        self.assertLess(synthesis_grounding_rank_multiplier(bare), 1.0)

    def test_signal_type_lattice_extra_downrank_vs_record(self) -> None:
        lattice = _base_synthesis_idea(
            idea_id="idea_lat",
            grounding=build_grounding(
                grounding_kind="signal_cluster",
                grounding_strength="low",
                grounding_sources=[{"type": "signal_type_only", "value": "metrics"}],
            ),
        )
        record = _base_synthesis_idea(
            idea_id="idea_rec",
            grounding=build_grounding(
                grounding_kind="signal_cluster",
                grounding_strength="medium",
                grounding_sources=[
                    {"type": "signal_record", "id": "sig-1", "signal_type": "metrics", "adapter_source": "t"},
                    {"type": "artifact", "path": "runs/signals/latest/p1.json"},
                ],
            ),
        )
        self.assertLess(
            synthesis_grounding_rank_multiplier(lattice),
            synthesis_grounding_rank_multiplier(record),
        )

    def test_finding_plus_artifact_boosts_multiplier(self) -> None:
        finding_only = _base_synthesis_idea(
            grounding=build_grounding(
                grounding_kind="finding",
                grounding_strength="high",
                grounding_sources=[{"type": "finding", "id": "f1", "kind": "gap"}],
            ),
        )
        finding_repo = _base_synthesis_idea(
            idea_id="idea_fr",
            grounding=build_grounding(
                grounding_kind="mixed",
                grounding_strength="high",
                grounding_sources=[
                    {"type": "finding", "id": "f1", "kind": "gap"},
                    {"type": "artifact", "path": "runs/findings/latest/p1.json"},
                    {"type": "repo_path", "path": "products/p1"},
                ],
            ),
        )
        self.assertGreater(
            synthesis_grounding_rank_multiplier(finding_repo),
            synthesis_grounding_rank_multiplier(finding_only),
        )

    def test_rank_key_orders_grounded_synthesis_above_weak_same_scores(self) -> None:
        base_kw = dict(
            type=IdeaType.INVENT,
            source=IdeaSource.SYNTHESIS,
            novelty_score=0.5,
            adjacency_score=0.5,
            expected_value_score=0.6,
            confidence_score=0.7,
            diversity_impact_score=0.4,
        )
        hi = Idea(
            idea_id="rk_hi",
            title="H",
            description="D",
            rationale="r",
            cost_estimate="medium",
            channel_type="web",
            monetization_type="ads",
            **base_kw,
            grounding=build_grounding(
                grounding_kind="finding",
                grounding_strength="high",
                grounding_sources=[
                    {"type": "finding", "id": "fx"},
                    {"type": "artifact", "path": "runs/findings/latest/p.json"},
                    {"type": "repo_path", "path": "products/x"},
                ],
            ),
        )
        lo = Idea(
            idea_id="rk_lo",
            title="L",
            description="D",
            rationale="r",
            cost_estimate="medium",
            channel_type="web",
            monetization_type="ads",
            **base_kw,
            grounding=build_grounding(
                grounding_kind="weak",
                grounding_strength="low",
                grounding_sources=[{"type": "combinatorial", "pattern": "domain_fuse"}],
            ),
        )
        self.assertGreater(rank_key(hi), rank_key(lo))


class TestGroundingJson(unittest.TestCase):
    def test_to_jsonable_roundtrip_shape(self) -> None:
        idea = _base_synthesis_idea(
            grounding=build_grounding(
                grounding_kind="mixed",
                grounding_strength="medium",
                grounding_sources=[{"type": "artifact", "path": "runs/signals/latest/p.json"}],
            ),
        )
        j = to_jsonable(idea)
        self.assertIsInstance(j, dict)
        g = j.get("grounding")
        self.assertIsInstance(g, dict)
        self.assertEqual(g.get("grounding_strength"), "medium")
        self.assertEqual(g.get("grounding_kind"), "mixed")


class TestSynthesisSingleProductGrounding(unittest.TestCase):
    def test_finding_id_appears_in_grounded_idea(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _write(root / "products" / pid / "product.yaml", _product_yaml(pid))
            (root / "products" / pid / "scripts").mkdir(parents=True)
            (root / "products" / pid / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            sig = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sig.parent.mkdir(parents=True, exist_ok=True)
            sig.write_text(
                json.dumps(
                    {
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "product_id": pid,
                        "record_count": 1,
                        "records": [
                            {
                                "id": "sig-metrics-1",
                                "product_id": pid,
                                "signal_type": "metrics",
                                "source": "metrics_file",
                                "observed_at": "2026-01-01T12:00:00+00:00",
                                "payload": {"summary": "up"},
                                "tags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            find_path = root / "runs" / "findings" / "latest" / f"{pid}.json"
            find_path.parent.mkdir(parents=True, exist_ok=True)
            find_path.write_text(
                json.dumps(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2026-01-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "finding_count": 1,
                        "findings": [
                            {
                                "id": "find-grounded-abc",
                                "product_id": pid,
                                "kind": "launch_candidate",
                                "severity": "low",
                                "effort": "medium",
                                "title": "Readiness check",
                                "summary": "Metrics present.",
                                "recommendation": "Validate gate.",
                                "source_signals": ["sig-metrics-1"],
                                "evidence": {},
                                "confidence": 0.5,
                                "created_at": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            ideas = synthesize_ideas(root, pid, seed="g-test", max_ideas=30)
            hi = [i for i in ideas if i.grounding and i.grounding.get("grounding_strength") == "high"]
            self.assertTrue(hi)
            self.assertTrue(any("find-grounded-abc" in i.description for i in hi))
            self.assertTrue(any("finding_grounded" in i.rationale or "finding_twist_local" in i.rationale for i in hi))

    def test_grounding_persisted_in_saved_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _write(root / "products" / pid / "product.yaml", _product_yaml(pid))
            (root / "products" / pid / "scripts").mkdir(parents=True)
            (root / "products" / pid / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")

            sig = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sig.parent.mkdir(parents=True, exist_ok=True)
            sig.write_text(
                json.dumps(
                    {
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "product_id": pid,
                        "record_count": 1,
                        "records": [
                            {
                                "id": "sig-metrics-1",
                                "product_id": pid,
                                "signal_type": "metrics",
                                "source": "metrics_file",
                                "observed_at": "2026-01-01T12:00:00+00:00",
                                "payload": {"summary": "up"},
                                "tags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            find_path = root / "runs" / "findings" / "latest" / f"{pid}.json"
            find_path.parent.mkdir(parents=True, exist_ok=True)
            find_path.write_text(
                json.dumps(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2026-01-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "finding_count": 1,
                        "findings": [
                            {
                                "id": "find-bundle-test",
                                "product_id": pid,
                                "kind": "launch_candidate",
                                "severity": "low",
                                "effort": "medium",
                                "title": "Readiness check",
                                "summary": "Metrics present.",
                                "recommendation": "Validate gate.",
                                "source_signals": ["sig-metrics-1"],
                                "evidence": {},
                                "confidence": 0.5,
                                "created_at": "2026-01-01T00:00:00+00:00",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            _, bundle = run_pipeline(root, pid, seed="persist-g", include_mutation=False, max_ideas=15)
            synth = [i for i in bundle.ideas if i.source == IdeaSource.SYNTHESIS]
            self.assertTrue(synth)
            for i in synth:
                self.assertIsInstance(i.grounding, dict)
                self.assertIn("grounding_kind", i.grounding)
                self.assertIn("grounding_strength", i.grounding)
                self.assertIsInstance(i.grounding.get("grounding_sources"), list)


if __name__ == "__main__":
    unittest.main()
