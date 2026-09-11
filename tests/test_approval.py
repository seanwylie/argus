"""Tests for approval store and execution gates."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.actions.executor import execute_action
from argus.actions.gate import (
    ExecutionNotApprovedError,
    is_execution_approved,
    require_execution_approval,
)
from argus.actions.models import ActionContract
from argus.approval.store import approve, create_pending, has_approved_for_action
from argus.products.inventory import build_inventory


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    _write(
        pr / "product.yaml",
        f"""
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
        """,
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")


class TestApprovalStore(unittest.TestCase):
    def test_pending_then_approve(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rec = create_pending(root, action_id="act_a", product_id="p_a")
            self.assertFalse(has_approved_for_action(root, "act_a", "p_a"))
            approve(root, rec.approval_id)
            self.assertTrue(has_approved_for_action(root, "act_a", "p_a"))

    def test_require_execution_approval_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_denied"
            _product(root, pid)
            create_pending(root, action_id="x", product_id=pid)
            c = ActionContract(
                action_id="x",
                product_id=pid,
                action_type="stop",
                command="true",
                working_directory=f"products/{pid}",
            )
            with self.assertRaises(ExecutionNotApprovedError):
                require_execution_approval(root, c)


class TestExecuteActionGate(unittest.TestCase):
    def test_execute_blocked_without_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "ap_gate"
            _product(root, pid)
            c = ActionContract(
                action_id="gate_act",
                product_id=pid,
                action_type="stop",
                command="echo hi",
                working_directory=f"products/{pid}",
                requires_approval=True,
            )
            inv = build_inventory(root)
            r = execute_action(c, repo_root=root, inventory=inv)
            self.assertIsNone(r.returncode)
            self.assertIn("Autonomy policy blocked execution", r.stderr)

    def test_execute_auto_analyze_without_manual_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "ap_auto"
            _product(root, pid)
            c = ActionContract(
                action_id="auto_act",
                product_id=pid,
                action_type="analyze",
                command="echo hi",
                working_directory=f"products/{pid}",
                requires_approval=True,
            )
            self.assertTrue(is_execution_approved(root, c))
            inv = build_inventory(root)
            r = execute_action(c, repo_root=root, inventory=inv)
            self.assertEqual(r.returncode, 0)
            self.assertIn("hi", r.stdout)

    def test_execute_runs_after_approval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "ap_ok"
            _product(root, pid)
            c = ActionContract(
                action_id="ok_act",
                product_id=pid,
                action_type="analyze",
                command="echo hi",
                working_directory=f"products/{pid}",
                requires_approval=True,
            )
            rec = create_pending(root, action_id="ok_act", product_id=pid)
            approve(root, rec.approval_id)
            inv = build_inventory(root)
            r = execute_action(c, repo_root=root, inventory=inv)
            self.assertEqual(r.returncode, 0)
            self.assertIn("hi", r.stdout)


if __name__ == "__main__":
    unittest.main()
