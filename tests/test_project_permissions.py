"""Tests for Phase 1 project permission policy (argus.policy.yaml)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.actions.executor import dry_run
from argus.actions.models import ActionContract
from argus.execution.engine import ExecutionBlocked, ensure_execution_allowed
from argus.products.inventory import build_inventory
from argus.project_permissions.defaults import DEFAULT_PHASE1
from argus.project_permissions.gate import (
    infer_phase1_key,
    phase1_summary_for_product,
    require_phase1_for_execution,
)
from argus.project_permissions.load import load_project_permission_policy, write_default_policy_file
from argus.project_permissions.schema import PHASE1_KEYS


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
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
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )


class TestLoadAndDefaults(unittest.TestCase):
    def test_defaults_cover_all_keys(self) -> None:
        self.assertEqual(set(PHASE1_KEYS), set(DEFAULT_PHASE1.keys()))

    def test_missing_file_uses_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            pol = load_project_permission_policy(root, "p1")
            self.assertTrue(pol.load_warnings)
            for k in PHASE1_KEYS:
                self.assertEqual(pol.get(k), DEFAULT_PHASE1[k])

    def test_write_and_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            pr.mkdir(parents=True)
            write_default_policy_file(pr)
            pol = load_project_permission_policy(root, "x")
            self.assertFalse(pol.load_warnings)
            self.assertEqual(pol.get("deploy"), DEFAULT_PHASE1["deploy"])


class TestGate(unittest.TestCase):
    def test_infer_git_push(self) -> None:
        c = ActionContract(
            action_id="a",
            product_id="p",
            action_type="custom",
            command="git push origin main",
            working_directory="products/p",
        )
        self.assertEqual(infer_phase1_key(c), "push_remote")

    def test_explicit_override(self) -> None:
        c = ActionContract(
            action_id="a",
            product_id="p",
            action_type="custom",
            command="echo hi",
            working_directory="products/p",
            project_permission_key="commit_local",
        )
        self.assertEqual(infer_phase1_key(c), "commit_local")

    def test_confirm_blocks_execution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            pr = root / "products" / "p1" / "argus.policy.yaml"
            pr.write_text(
                textwrap.dedent(
                    """
                    schema: argus.project_permission_policy.v1
                    observe_prod_signals: "yes"
                    mutate_nonprod: "confirm"
                    mutate_prod: "no"
                    commit_local: "yes"
                    push_remote: "no"
                    deploy: "no"
                    change_experiments: "no"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="echo safe",
                working_directory="products/p1",
            )
            r = require_phase1_for_execution(root, c)
            self.assertTrue(any("confirmation" in x.lower() for x in r))

    def test_no_blocks(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            pr = root / "products" / "p1" / "argus.policy.yaml"
            pr.write_text(
                textwrap.dedent(
                    """
                    schema: argus.project_permission_policy.v1
                    observe_prod_signals: "yes"
                    mutate_nonprod: "no"
                    mutate_prod: "no"
                    commit_local: "yes"
                    push_remote: "no"
                    deploy: "no"
                    change_experiments: "no"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="echo safe",
                working_directory="products/p1",
            )
            r = require_phase1_for_execution(root, c)
            self.assertTrue(any("denies" in x for x in r))

    def test_phase1_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            write_default_policy_file(root / "products" / "p1")
            s = phase1_summary_for_product(root, "p1")
            self.assertIn("policy", s)
            self.assertIn("policy_environment_mismatches", s)


class TestExecutionIntegration(unittest.TestCase):
    def test_ensure_execution_respects_confirm(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            (root / "products" / "p1" / "argus.policy.yaml").write_text(
                textwrap.dedent(
                    """
                    schema: argus.project_permission_policy.v1
                    observe_prod_signals: "yes"
                    mutate_nonprod: "confirm"
                    mutate_prod: "no"
                    commit_local: "yes"
                    push_remote: "no"
                    deploy: "no"
                    change_experiments: "no"
                    """
                ).strip()
                + "\n",
                encoding="utf-8",
            )
            c = ActionContract(
                action_id="a",
                product_id="p1",
                action_type="analyze",
                command="echo x",
                working_directory="products/p1",
            )
            inv = build_inventory(root)
            dr = dry_run(c, repo_root=root, inventory=inv)
            with self.assertRaises(ExecutionBlocked):
                ensure_execution_allowed(c, root, dr)


if __name__ == "__main__":
    unittest.main()
