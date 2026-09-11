"""Tests for combinatorial idea synthesis."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.idea_generation.models import IdeaSource, IdeaType
from argus.idea_generation.synthesis import synthesize_ideas


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _yaml(pid: str) -> str:
    return f"""
    id: {pid}
    name: Product {pid}
    owner:
      team: test
    lifecycle:
      stage: idea
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


class TestSynthesisEngine(unittest.TestCase):
    def test_non_duplicate_titles(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("alpha", "beta", "gamma"):
                _write(root / "products" / pid / "product.yaml", _yaml(pid))
                (root / "products" / pid / "scripts").mkdir(parents=True)
                (root / "products" / pid / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                sig = root / "runs" / "signals" / "latest" / f"{pid}.json"
                sig.parent.mkdir(parents=True, exist_ok=True)
                sig.write_text(
                    json.dumps(
                        {
                            "schema": "argus.signal_collection.v1",
                            "product_id": pid,
                            "collected_at_utc": "2026-01-01T00:00:00+00:00",
                            "repo_root": str(root),
                            "record_count": 1,
                            "records": [
                                {
                                    "id": f"s_{pid}",
                                    "product_id": pid,
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

            ideas = synthesize_ideas(root, None, seed="test-seed", max_ideas=40)
            titles = [i.title.lower() for i in ideas]
            self.assertEqual(len(titles), len(set(titles)))
            for idea in ideas:
                self.assertEqual(idea.type, IdeaType.INVENT)
                self.assertEqual(idea.source, IdeaSource.SYNTHESIS)

    def test_novelty_high_for_invent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("a", "b"):
                _write(root / "products" / pid / "product.yaml", _yaml(pid))
                (root / "products" / pid / "scripts").mkdir(parents=True)
                (root / "products" / pid / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            ideas = synthesize_ideas(root, None, seed="novelty", max_ideas=20)
            self.assertTrue(ideas)
            for idea in ideas:
                self.assertGreaterEqual(idea.novelty_score, 0.44)


if __name__ == "__main__":
    unittest.main()
