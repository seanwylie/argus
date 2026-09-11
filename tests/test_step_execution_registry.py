"""Mechanical tests for ``STEP_EXECUTION_REGISTRY`` and dispatch wiring."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.state_models import (
    ACTION_DECISIONS_GENERATE,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_QUEUED_UNHANDLED,
)
from argus.orchestrator.step_executor import (
    STEP_EXECUTION_REGISTRY,
    execute_orchestration_action,
)
from tests.test_orchestration_phase1_contract import PHASE1_STEP_EXECUTOR_ACTION_IDS
from tests.test_orchestration_step_executor import _minimal_product


class TestStepExecutionRegistry(unittest.TestCase):
    def test_registry_keys_match_phase1_documented_action_ids(self) -> None:
        self.assertEqual(
            frozenset(STEP_EXECUTION_REGISTRY.keys()),
            PHASE1_STEP_EXECUTOR_ACTION_IDS,
        )

    def test_unsupported_action_queued_unhandled_payload_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = execute_orchestration_action(root, "p1", "not_a_real_action_id")
            self.assertEqual(r["action_status"], ACTION_STATUS_QUEUED_UNHANDLED)
            ed = r.get("execution_detail") or {}
            self.assertEqual(ed.get("reason"), "no in-process executor for this action_id")
            self.assertEqual(ed.get("action_id"), "not_a_real_action_id")
            self.assertFalse(ed.get("phase1_evaluated"))
            self.assertIsNone(ed.get("phase1_permission_decision"))
            self.assertIn("STEP_EXECUTION_REGISTRY", ed.get("phase1_not_evaluated_reason") or "")
            self.assertIsNone(r.get("execution_error"))

    def test_dispatch_invokes_registered_signals_collect(self) -> None:
        sentinel = {
            "action_status": ACTION_STATUS_EXECUTED,
            "execution_detail": {"test": "sentinel"},
            "execution_error": None,
        }
        calls: list[tuple[Path, str]] = []

        def fake(root: Path, product_id: str) -> dict[str, object]:
            calls.append((root, product_id))
            return sentinel

        orig = STEP_EXECUTION_REGISTRY[ACTION_SIGNALS_COLLECT]
        STEP_EXECUTION_REGISTRY[ACTION_SIGNALS_COLLECT] = fake  # type: ignore[assignment]
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                _minimal_product(root, "p1")
                out = execute_orchestration_action(root, "p1", ACTION_SIGNALS_COLLECT)
            self.assertEqual(calls, [(root.resolve(), "p1")])
            self.assertEqual(out["action_status"], ACTION_STATUS_EXECUTED)
            self.assertEqual(out["execution_detail"].get("test"), "sentinel")
            self.assertIn("phase1_permission_decision", out["execution_detail"])
        finally:
            STEP_EXECUTION_REGISTRY[ACTION_SIGNALS_COLLECT] = orig

    def test_dispatch_invokes_registered_decisions_generate(self) -> None:
        done = {
            "action_status": ACTION_STATUS_EXECUTED,
            "execution_detail": {},
            "execution_error": None,
        }
        calls: list[tuple[Path, str]] = []

        def fake(root: Path, product_id: str) -> dict[str, object]:
            calls.append((root, product_id))
            return done

        orig = STEP_EXECUTION_REGISTRY[ACTION_DECISIONS_GENERATE]
        STEP_EXECUTION_REGISTRY[ACTION_DECISIONS_GENERATE] = fake  # type: ignore[assignment]
        try:
            with TemporaryDirectory() as tmp:
                root = Path(tmp)
                _minimal_product(root, "p2")
                out = execute_orchestration_action(root, "p2", ACTION_DECISIONS_GENERATE)
            self.assertEqual(calls, [(root.resolve(), "p2")])
            self.assertEqual(out["action_status"], ACTION_STATUS_EXECUTED)
            self.assertIn("phase1_permission_decision", out["execution_detail"])
        finally:
            STEP_EXECUTION_REGISTRY[ACTION_DECISIONS_GENERATE] = orig


if __name__ == "__main__":
    unittest.main()
