"""Tests for auto-approval rule evaluation."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.actions.models import ActionContract, LifecycleTransitionSpec
from argus.approval.rules import evaluate_auto_approval


def _product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    pr.joinpath("product.yaml").write_text(
        textwrap.dedent(
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
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")


class TestAutoApprovalRules(unittest.TestCase):
    def test_investigate_read_only_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "r1b"
            _product(root, pid)
            c = ActionContract(
                action_id="a1b",
                product_id=pid,
                action_type="investigate",
                command="echo read_only",
                working_directory=f"products/{pid}",
            )
            ev = evaluate_auto_approval(c, repo_root=root)
            self.assertTrue(ev.auto_approve)

    def test_analyze_read_only_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "r1"
            _product(root, pid)
            c = ActionContract(
                action_id="a1",
                product_id=pid,
                action_type="analyze",
                command="echo read_only",
                working_directory=f"products/{pid}",
            )
            ev = evaluate_auto_approval(c, repo_root=root)
            self.assertTrue(ev.auto_approve)
            self.assertTrue(any("analyze" in x.lower() for x in ev.reasons))

    def test_non_analyze_not_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "r2"
            _product(root, pid)
            c = ActionContract(
                action_id="a1",
                product_id=pid,
                action_type="stop",
                command="true",
                working_directory=f"products/{pid}",
            )
            ev = evaluate_auto_approval(c, repo_root=root)
            self.assertFalse(ev.auto_approve)

    def test_lifecycle_transition_not_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "r3"
            _product(root, pid)
            c = ActionContract(
                action_id="a1",
                product_id=pid,
                action_type="analyze",
                command="true",
                working_directory=f"products/{pid}",
                lifecycle_transition=LifecycleTransitionSpec(from_stage="grow", to_stage="validate"),
            )
            ev = evaluate_auto_approval(c, repo_root=root)
            self.assertFalse(ev.auto_approve)
            self.assertTrue(any("lifecycle" in x.lower() for x in ev.reasons))

    def test_redirect_not_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "r4"
            _product(root, pid)
            c = ActionContract(
                action_id="a1",
                product_id=pid,
                action_type="analyze",
                command="echo x > /tmp/out.txt",
                working_directory=f"products/{pid}",
            )
            ev = evaluate_auto_approval(c, repo_root=root)
            self.assertFalse(ev.auto_approve)

    def test_git_not_auto(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "r5"
            _product(root, pid)
            c = ActionContract(
                action_id="a1",
                product_id=pid,
                action_type="analyze",
                command="git status",
                working_directory=f"products/{pid}",
            )
            ev = evaluate_auto_approval(c, repo_root=root)
            self.assertFalse(ev.auto_approve)


if __name__ == "__main__":
    unittest.main()
