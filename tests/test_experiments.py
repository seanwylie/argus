"""Tests for experiments storage and lifecycle."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.registry import can_transition
from argus.experiments.store import list_experiments, load_experiment, save_experiment


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
      max_monthly_cost_usd: 100
      min_activity_threshold: 0
    """


class TestTransitions(unittest.TestCase):
    def test_proposed_to_active_and_failed(self) -> None:
        self.assertTrue(can_transition(ExperimentStatus.PROPOSED, ExperimentStatus.ACTIVE))
        self.assertTrue(can_transition(ExperimentStatus.PROPOSED, ExperimentStatus.FAILED))
        self.assertFalse(can_transition(ExperimentStatus.PROPOSED, ExperimentStatus.COMPLETED))

    def test_active_to_terminal(self) -> None:
        self.assertTrue(can_transition(ExperimentStatus.ACTIVE, ExperimentStatus.COMPLETED))
        self.assertTrue(can_transition(ExperimentStatus.ACTIVE, ExperimentStatus.FAILED))
        self.assertFalse(can_transition(ExperimentStatus.ACTIVE, ExperimentStatus.PROPOSED))

    def test_terminal_locked(self) -> None:
        self.assertFalse(can_transition(ExperimentStatus.COMPLETED, ExperimentStatus.ACTIVE))


class TestStore(unittest.TestCase):
    def test_create_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = Experiment(
                id="exp_test_001",
                product_id="p1",
                hypothesis="H",
                type=ExperimentType.GROWTH,
                description="D",
                expected_outcome="O",
                success_metrics=["m1"],
                start_at="2026-01-01T00:00:00+00:00",
                end_at=None,
                status=ExperimentStatus.PROPOSED,
                confidence=0.6,
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            got = load_experiment(root, "exp_test_001")
            self.assertEqual(got.hypothesis, "H")
            self.assertEqual(got.type, ExperimentType.GROWTH)
            self.assertEqual(got.success_metrics, ["m1"])

    def test_list_filters_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_experiment(
                root,
                Experiment(
                    id="exp_a",
                    product_id="alpha",
                    hypothesis="h",
                    type=ExperimentType.CONTENT,
                    description="",
                    expected_outcome="",
                    created_at="t",
                ),
            )
            save_experiment(
                root,
                Experiment(
                    id="exp_b",
                    product_id="beta",
                    hypothesis="h",
                    type=ExperimentType.CONTENT,
                    description="",
                    expected_outcome="",
                    created_at="t",
                ),
            )
            la = list_experiments(root, product_id="alpha")
            self.assertEqual(len(la), 1)
            self.assertEqual(la[0].id, "exp_a")


class TestProductBinding(unittest.TestCase):
    def test_experiment_tied_to_product_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "bindme"
            _write(pr / "product.yaml", _minimal_yaml("bindme"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            exp = Experiment(
                id="exp_bind",
                product_id="bindme",
                hypothesis="Increase engagement",
                type=ExperimentType.ENGAGEMENT,
                description="",
                expected_outcome="",
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            got = load_experiment(root, "exp_bind")
            self.assertEqual(got.product_id, "bindme")


if __name__ == "__main__":
    unittest.main()
