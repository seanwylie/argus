"""Tests for execution engine (opt-in subprocess)."""

from __future__ import annotations

import os
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.approval.store import approve, create_pending
from argus.autonomy.safe_execution import ENV_AUTONOMOUS_SAFE_EXECUTION
from argus.execution.engine import ExecutionBlocked, load_run, run_subprocess
from argus.execution.models import ExecutionStatus
from argus.execution.runner import run_action_file


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _containment_staging_grant(root: Path) -> None:
    """So Phase 1 env check for ``mutate_nonprod`` sees staging write as available."""
    cfg = root / "config"
    cfg.mkdir(parents=True)
    (cfg / "argus_containment.yaml").write_text(
        """
schema: argus.credential_containment_policy.v0
grants:
  aws_write_staging: true
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _product_layout(root: Path, pid: str) -> None:
    _containment_staging_grant(root)
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
    (pr / "argus.policy.yaml").write_text(
        """
schema: argus.project_permission_policy.v1
change_experiments: 'yes'
commit_local: 'yes'
deploy: 'no'
mutate_nonprod: 'yes'
mutate_prod: 'no'
observe_prod_signals: 'yes'
push_remote: 'no'
""".strip()
        + "\n",
        encoding="utf-8",
    )


def _approve_pair(root: Path, action_id: str, product_id: str) -> None:
    rec = create_pending(root, action_id=action_id, product_id=product_id)
    approve(root, rec.approval_id)


def _action_yaml(pid: str) -> str:
    return f"""
    action_id: test_exec_{pid}
    product_id: {pid}
    action_type: analyze
    working_directory: products/{pid}
    command: "echo execution_test_ok"
    requires_approval: false
    safe_to_auto_execute: true
    """


class TestExecutionPolicy(unittest.TestCase):
    def test_disabled_without_enable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "exec_pol"
            _product_layout(root, pid)
            af = root / "a.yaml"
            _write(af, _action_yaml(pid))
            os.environ.pop("ARGUS_EXECUTION_ENABLED", None)
            with self.assertRaises(ExecutionBlocked) as ctx:
                run_action_file(root, af, enable_execution=False)
            self.assertTrue(any("disabled" in x.lower() for x in ctx.exception.reasons))

    def test_runs_via_autonomous_env_without_enable_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "exec_auto"
            _product_layout(root, pid)
            af = root / "a.yaml"
            _write(af, _action_yaml(pid))
            _approve_pair(root, f"test_exec_{pid}", pid)
            os.environ.pop("ARGUS_EXECUTION_ENABLED", None)
            os.environ[ENV_AUTONOMOUS_SAFE_EXECUTION] = "1"
            try:
                run = run_action_file(root, af, enable_execution=False)
            finally:
                os.environ.pop(ENV_AUTONOMOUS_SAFE_EXECUTION, None)
            self.assertEqual(run.exit_code, 0)
            self.assertIn("execution_test_ok", run.output_log)

    def test_runs_echo_when_enabled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "exec_run"
            _product_layout(root, pid)
            af = root / "a.yaml"
            _write(af, _action_yaml(pid))
            _approve_pair(root, f"test_exec_{pid}", pid)
            run = run_action_file(root, af, enable_execution=True)
            self.assertEqual(run.status, ExecutionStatus.SUCCESS)
            self.assertEqual(run.exit_code, 0)
            self.assertIn("execution_test_ok", run.output_log)
            loaded = load_run(root, run.run_id)
            self.assertEqual(loaded.run_id, run.run_id)

    def test_requires_approval_record(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "exec_appr"
            _product_layout(root, pid)
            af = root / "a.yaml"
            # lifecycle_transition blocks auto-approval; still analyze + safe command for dry-run.
            _write(
                af,
                f"""
                action_id: test_exec_{pid}
                product_id: {pid}
                action_type: analyze
                working_directory: products/{pid}
                command: "true"
                requires_approval: true
                safe_to_auto_execute: false
                lifecycle_transition:
                  from: validate
                  to: build
                """,
            )
            run_blocked = run_action_file(root, af, enable_execution=True)
            self.assertEqual(run_blocked.status, ExecutionStatus.BLOCKED)
            self.assertTrue((root / "runs" / "execution" / run_blocked.run_id / "run.json").is_file())
            loaded_b = load_run(root, run_blocked.run_id)
            self.assertTrue(loaded_b.execution_detail.get("execution_approval_blocked"))
            _approve_pair(root, f"test_exec_{pid}", pid)
            run = run_action_file(root, af, enable_execution=True)
            self.assertEqual(run.exit_code, 0)


class TestSubprocessDirect(unittest.TestCase):
    def test_run_subprocess_smoke(self) -> None:
        """Engine-level run after validation is caller's responsibility."""
        from argus.actions.models import ActionContract

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "sub_sm"
            _product_layout(root, pid)
            c = ActionContract(
                action_id="a1",
                product_id=pid,
                action_type="analyze",
                command="echo hi",
                working_directory=f"products/{pid}",
                requires_approval=False,
            )
            run = run_subprocess(c, repo_root=root, run_id="exec_test_manual")
            self.assertEqual(run.status, ExecutionStatus.SUCCESS)
            self.assertIn("hi", run.output_log)


if __name__ == "__main__":
    unittest.main()
