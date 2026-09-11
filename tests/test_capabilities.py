"""Tests for capability registry and evaluation."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.capabilities.evaluate import evaluate_capabilities
from argus.capabilities.models import Capability, MissingCapability
from argus.capabilities.registry import (
    current_capabilities,
    infer_missing_capabilities,
    snapshot_usage_filenames,
    suggest_next_build,
)


class TestRegistry(unittest.TestCase):
    def test_current_capabilities_nonempty(self) -> None:
        caps = current_capabilities()
        self.assertGreaterEqual(len(caps), 5)
        ids = {c.id for c in caps}
        self.assertIn("cap.ingestion.signals.builtin", ids)
        for c in caps:
            self.assertIn(c.category, ("analysis", "ingestion", "execution", "ui", "planning"))
            self.assertIn(c.maturity, ("stub", "basic", "advanced"))

    def test_orchestrator_loop_is_stub_maturity(self) -> None:
        """Registry marks the local loop as stub (minimal vs production orchestration)."""
        by_id = {c.id: c for c in current_capabilities()}
        orch = by_id["cap.orchestrator.loop"]
        self.assertEqual(orch.maturity, "stub")

    def test_infer_missing_includes_experiment_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            gaps = infer_missing_capabilities(root)
            ids = {g.id for g in gaps}
            self.assertIn("gap.execution.experiment_tracking", ids)

    def test_suggest_next_is_lowest_priority(self) -> None:
        m = [
            MissingCapability(
                id="a",
                name="a",
                description="",
                category="analysis",
                reason="",
                priority=30,
            ),
            MissingCapability(
                id="b",
                name="b",
                description="",
                category="analysis",
                reason="",
                priority=10,
            ),
        ]
        self.assertEqual(suggest_next_build(m), "b")

    def test_snapshot_usage_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(snapshot_usage_filenames(Path(tmp)), set())


class TestEvaluate(unittest.TestCase):
    def test_evaluate_smoke(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ev = evaluate_capabilities(root)
            self.assertTrue(ev.capabilities)
            self.assertIsNotNone(ev.suggested_next)
            self.assertIn("kinds_total", ev.finding_aggregate)

    def test_capability_model_frozen(self) -> None:
        c = Capability(
            id="x",
            name="n",
            description="d",
            category="analysis",
            maturity="stub",
            coverage="c",
        )
        with self.assertRaises(Exception):
            c.id = "y"  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
