"""Lightweight cross-subsystem smoke: proposals, prioritization, allocation, simulation."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.prioritize import prioritize_experiments
from argus.experiments.propose import propose_experiments
from argus.portfolio.allocate import run_allocation
from argus.products.inventory import build_inventory
from argus.simulation.simulate import run_simulation


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
      monthly_usd: 20
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: 150
      min_activity_threshold: 0
    """


class TestPipelineIntegrity(unittest.TestCase):
    def test_propose_prioritize_allocate_simulate_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "pipe_demo"
            _write(pr / "product.yaml", _minimal_yaml("pipe_demo"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            inv = build_inventory(root)
            self.assertIn("pipe_demo", inv.valid)

            prun = propose_experiments(root, product_id="pipe_demo", inventory=inv)
            self.assertIsNotNone(prun.proposals)

            prio = prioritize_experiments(root, product_id="pipe_demo", inventory=inv)
            self.assertEqual(
                sorted(prio.by_product.keys()),
                ["pipe_demo"],
            )

            alloc, _g = run_allocation(root)
            self.assertGreaterEqual(len(alloc.products), 1)
            self.assertTrue(any(p.product_id == "pipe_demo" for p in alloc.products))

            sim = run_simulation(root, inv.valid["pipe_demo"].node, experiment=None)
            self.assertEqual(sim.product_id, "pipe_demo")
            self.assertEqual(len(sim.scenarios), 3)


if __name__ == "__main__":
    unittest.main()
