"""Tests for operator autonomy mode, policy, and CLI."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.autonomy.models import AutonomyMode
from argus.autonomy.operator_policy import (
    autonomy_config_path,
    effective_policy,
    save_autonomy_config,
)
from argus.autonomy.tiers import AutonomyTier
from argus.execution.models import ExecutionStatus
from argus.execution.runner import run_action_file


def _containment_staging_grant(root: Path) -> None:
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
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
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
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
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


class TestOperatorPolicy(unittest.TestCase):
    def test_default_active_without_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            mode, pol, tier = effective_policy(root)
            self.assertEqual(mode, AutonomyMode.ACTIVE)
            self.assertEqual(tier, AutonomyTier.BOUNDED_EXECUTION)
            self.assertGreater(pol.max_actions_per_run, 0)

    def test_set_off_blocks_execution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "ax"
            _product_layout(root, pid)
            save_autonomy_config(root, AutonomyMode.OFF)
            af = root / "a.yaml"
            af.write_text(
                f"""
action_id: test_{pid}
product_id: {pid}
action_type: analyze
working_directory: products/{pid}
command: "echo x"
requires_approval: false
safe_to_auto_execute: true
""",
                encoding="utf-8",
            )
            run = run_action_file(root, af, enable_execution=True)
            self.assertEqual(run.status, ExecutionStatus.BLOCKED)
            self.assertIn("autonomy", (run.error_log or "").lower())
            self.assertTrue(run.execution_detail.get("autonomy_blocked"))
            self.assertTrue((root / "runs" / "execution" / run.run_id / "run.json").is_file())


class TestAutonomyConfigPath(unittest.TestCase):
    def test_config_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = save_autonomy_config(root, AutonomyMode.LIMITED)
            self.assertEqual(p, autonomy_config_path(root))
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data.get("mode"), "limited")


if __name__ == "__main__":
    unittest.main()
