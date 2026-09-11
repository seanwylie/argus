"""Tests for experiment prioritization."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.prioritize import format_prioritization_text, prioritize_experiments


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_yaml(pid: str, *, stage: str = "grow") -> str:
    return f"""
    id: {pid}
    name: {pid}
    owner:
      team: test
    lifecycle:
      stage: {stage}
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: 10
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: 100
      min_activity_threshold: 0
    """


class TestPrioritizeExperiments(unittest.TestCase):
    def _scaffold(self, root: Path, pid: str) -> None:
        pr = root / "products" / pid
        _write(pr / "product.yaml", _minimal_yaml(pid))
        (pr / "scripts").mkdir(parents=True)
        (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

    def test_sorted_within_product_and_top_three_global(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root, "alpha")
            self._scaffold(root, "beta")
            run = prioritize_experiments(root)
            for pid in run.by_product:
                scores = [r.score for r in run.by_product[pid]]
                self.assertEqual(scores, sorted(scores, reverse=True))
            self.assertLessEqual(len(run.top_recommendations), 3)

    def test_deterministic_repeat(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root, "solo")
            a = prioritize_experiments(root, product_id="solo")
            b = prioritize_experiments(root, product_id="solo")
            self.assertEqual(
                [r.proposal.proposal_id for r in a.by_product.get("solo", [])],
                [r.proposal.proposal_id for r in b.by_product.get("solo", [])],
            )
            self.assertEqual(
                [r.score for r in a.by_product.get("solo", [])],
                [r.score for r in b.by_product.get("solo", [])],
            )

    def test_format_non_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self._scaffold(root, "fmt")
            run = prioritize_experiments(root, product_id="fmt")
            text = format_prioritization_text(run)
            self.assertIn("Top recommendations", text)
            self.assertIn("## fmt", text)


if __name__ == "__main__":
    unittest.main()
