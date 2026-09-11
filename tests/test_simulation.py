"""Deterministic scenario simulation."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import save_experiment
from argus.products.inventory import build_inventory
from argus.simulation.models import ScenarioKind
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
      stage: validate
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: 100
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions: {{}}
    constraints:
      max_monthly_cost_usd: 500
      min_activity_threshold: 0
    """


class TestSimulation(unittest.TestCase):
    def test_generic_produces_three_scenarios(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "products" / "sim_p" / "product.yaml", _minimal_yaml("sim_p"))
            inv = build_inventory(root)
            self.assertIn("sim_p", inv.valid)
            r = run_simulation(root, inv.valid["sim_p"].node, experiment=None)
            self.assertEqual(r.product_id, "sim_p")
            self.assertIsNone(r.experiment_id)
            self.assertEqual(len(r.scenarios), 3)
            kinds = {s.kind for s in r.scenarios}
            self.assertEqual(
                kinds,
                {ScenarioKind.BEST, ScenarioKind.EXPECTED, ScenarioKind.WORST},
            )

    def test_same_inputs_deterministic_metrics(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "products" / "sim_p" / "product.yaml", _minimal_yaml("sim_p"))
            inv = build_inventory(root)
            node = inv.valid["sim_p"].node
            a = run_simulation(root, node, experiment=None)
            b = run_simulation(root, node, experiment=None)
            self.assertEqual(
                [s.metric_changes for s in a.scenarios],
                [s.metric_changes for s in b.scenarios],
            )

    def test_experiment_changes_shape_vs_generic(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(root / "products" / "sim_p" / "product.yaml", _minimal_yaml("sim_p"))
            inv = build_inventory(root)
            node = inv.valid["sim_p"].node
            g = run_simulation(root, node, experiment=None)
            exp = Experiment(
                id="exp_sim_test",
                product_id="sim_p",
                hypothesis="Increase conversion",
                type=ExperimentType.GROWTH,
                description="",
                expected_outcome="more",
                success_metrics=["m1"],
                start_at="2026-01-01T00:00:00+00:00",
                end_at=None,
                status=ExperimentStatus.PROPOSED,
                confidence=0.7,
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            e = run_simulation(root, node, experiment=exp)
            self.assertEqual(e.experiment_id, "exp_sim_test")
            g_keys = set(g.scenarios[0].metric_changes)
            e_keys = set(e.scenarios[0].metric_changes)
            self.assertNotEqual(g_keys, e_keys)


if __name__ == "__main__":
    unittest.main()
