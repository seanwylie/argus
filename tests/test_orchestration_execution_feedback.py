"""Orchestration step outcomes as durable execution feedback for signal observation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.core.models.enums import SignalType
from argus.orchestrator.execution_feedback import (
    is_orchestration_feedback_filename,
    write_orchestration_execution_feedback,
)
from argus.orchestrator.state_models import (
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from argus.products.inventory import build_inventory
from argus.signals.adapters import default_builtin_adapters
from argus.signals.adapters.execution import ExecutionAdapter
from argus.signals.contract import ProductSignalContext
from argus.signals.runner import product_root_path


def _product_with_execution(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: ExecFeedbackTest
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths:
    - metrics/
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
  - type: execution
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
    for name, body in (
        ("start.sh", "#!/bin/sh\necho start\n"),
        ("stop.sh", "#!/bin/sh\necho stop\n"),
        ("analyze.sh", "#!/bin/sh\necho analyze\n"),
    ):
        (pr / "scripts" / name).write_text(body, encoding="utf-8")
    (pr / "metrics").mkdir(exist_ok=True)
    (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")
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


class TestOrchestrationExecutionFeedback(unittest.TestCase):
    def test_writes_success_feedback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fb"
            _product_with_execution(root, pid)
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                from datetime import datetime, timezone

                dt.now.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                r = execute_orchestration_action(root, pid, ACTION_ORCHESTRATION_STATE_REFRESH)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            files = list((root / "runs" / "execution" / pid).glob("orchestration_feedback_*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA)
            self.assertEqual(data["product_id"], pid)
            self.assertEqual(data["action_id"], ACTION_ORCHESTRATION_STATE_REFRESH)
            self.assertEqual(data["orchestration_action_status"], ACTION_STATUS_EXECUTED)
            self.assertTrue(data["success"])
            self.assertTrue(data["provenance"]["source_ref"].endswith(files[0].name))
            self.assertIn("orchestration_state_path", data["execution_detail"])

    def test_writes_failed_feedback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "missing_product"
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                from datetime import datetime, timezone

                dt.now.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                r = execute_orchestration_action(root, pid, ACTION_SIGNALS_COLLECT)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            files = list((root / "runs" / "execution" / "missing_product").glob("orchestration_feedback_*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["orchestration_action_status"], ACTION_STATUS_FAILED)
            self.assertFalse(data["success"])
            self.assertIsNotNone(data.get("execution_error"))

    def test_writes_queued_unhandled_feedback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fb"
            _product_with_execution(root, pid)
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                from datetime import datetime, timezone

                dt.now.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                r = execute_orchestration_action(root, pid, "not_a_real_action_id")
            self.assertEqual(r["action_status"], ACTION_STATUS_QUEUED_UNHANDLED)
            files = list((root / "runs" / "execution" / pid).glob("orchestration_feedback_*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(data["orchestration_action_status"], ACTION_STATUS_QUEUED_UNHANDLED)
            self.assertFalse(data["success"])

    def test_execution_adapter_visible_on_later_collect(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fb"
            _product_with_execution(root, pid)
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                from datetime import datetime, timezone

                dt.now.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                execute_orchestration_action(root, pid, ACTION_ORCHESTRATION_STATE_REFRESH)

            inv = build_inventory(root)
            node = inv.valid[pid].node
            ctx = ProductSignalContext(
                repo_root=root,
                product=node,
                product_root=product_root_path(root, node),
            )
            adapter = ExecutionAdapter()
            recs = adapter.collect(ctx)
            self.assertTrue(len(recs) >= 1)
            match = [r for r in recs if r.payload.get("orchestration_action_status") == ACTION_STATUS_EXECUTED]
            self.assertEqual(len(match), 1)
            self.assertEqual(match[0].signal_type, SignalType.EXECUTION)
            self.assertEqual(match[0].payload.get("schema"), ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA)
            self.assertIn("provenance", match[0].payload)

    def test_collect_for_product_includes_execution_feedback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fb"
            _product_with_execution(root, pid)
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                from datetime import datetime, timezone

                dt.now.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                execute_orchestration_action(root, pid, ACTION_ORCHESTRATION_STATE_REFRESH)

            inv = build_inventory(root)
            node = inv.valid[pid].node
            from argus.signals.registry import AdapterRegistry
            from argus.signals.runner import collect_for_product as cfp

            reg = AdapterRegistry(default_builtin_adapters())
            rows = cfp(root, node, reg)
            orch = [r for r in rows if r.signal_type == SignalType.EXECUTION and r.payload.get("schema") == ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA]
            self.assertEqual(len(orch), 1)

    def test_is_orchestration_feedback_filename(self) -> None:
        self.assertTrue(is_orchestration_feedback_filename("orchestration_feedback_x_20260412T120000Z.json"))
        self.assertFalse(is_orchestration_feedback_filename("run.json"))

    def test_write_manual_feedback_completed_at(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = write_orchestration_execution_feedback(
                root,
                "p1",
                "audit_run",
                {
                    "action_status": ACTION_STATUS_EXECUTED,
                    "execution_detail": {"k": 1},
                    "execution_error": None,
                },
                completed_at_utc="2026-01-01T00:00:00+00:00",
            )
            d = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(d["finished_at_utc"], "2026-01-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
