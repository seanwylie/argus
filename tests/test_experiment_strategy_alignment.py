"""Experiment prioritization reflects persisted strategy mode (cohesion with decision weights)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.prioritize import prioritize_experiments
from argus.strategy.apply import save_strategy_mode
from argus.strategy.modes import StrategyMode


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_yaml(pid: str) -> str:
    return f"""
    id: {pid}
    name: {pid}
    owner:
      team: test
    lifecycle:
      stage: grow
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: 50
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: 200
      min_activity_threshold: 0
    """


class TestExperimentPrioritizeStrategy(unittest.TestCase):
    def test_scores_differ_growth_vs_survival(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "strat_exp"
            _write(pr / "product.yaml", _minimal_yaml("strat_exp"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            save_strategy_mode(root, StrategyMode.GROWTH)
            g = prioritize_experiments(root, product_id="strat_exp")
            by_g = {
                p.proposal.proposal_id: p.score
                for plist in g.by_product.values()
                for p in plist
            }
            self.assertTrue(by_g, "expected at least one proposal for grow-stage product")

            save_strategy_mode(root, StrategyMode.SURVIVAL)
            s = prioritize_experiments(root, product_id="strat_exp")
            by_s = {
                p.proposal.proposal_id: p.score
                for plist in s.by_product.values()
                for p in plist
            }
            self.assertEqual(set(by_g.keys()), set(by_s.keys()))
            self.assertTrue(
                any(by_g[k] != by_s[k] for k in by_g),
                "strategy profile should change at least one prioritization score",
            )


if __name__ == "__main__":
    unittest.main()
