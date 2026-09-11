"""Phase 1 enforcement and audit artifacts for orchestration step executor."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.state_models import (
    ACTION_FINDINGS_GENERATE,
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_FAILED,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from argus.project_permissions.audit import PHASE1_PERMISSION_DECISION_SCHEMA
from tests.test_orchestration_step_executor import _minimal_product


class TestPhase1OrchestrationAudit(unittest.TestCase):
    def test_blocked_writes_decision_artifact_and_feedback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_block"
            _minimal_product(root, pid)
            pol = root / "products" / pid / "argus.policy.yaml"
            pol.write_text(
                """
schema: argus.project_permission_policy.v1
change_experiments: 'yes'
commit_local: 'yes'
deploy: 'no'
mutate_nonprod: 'no'
mutate_prod: 'no'
observe_prod_signals: 'yes'
push_remote: 'no'
""".strip()
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_BLOCKED)
            self.assertIn("phase1_decision_audit_path", r.get("execution_detail") or {})

            dec_dir = root / "runs" / "policy" / "phase1_decisions" / pid
            self.assertTrue(dec_dir.is_dir())
            files = list(dec_dir.glob("phase1_*.json"))
            self.assertEqual(len(files), 1)
            raw = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertEqual(raw.get("schema"), PHASE1_PERMISSION_DECISION_SCHEMA)
            self.assertEqual(raw.get("aggregate_decision"), "refused")
            self.assertEqual(raw.get("execution_path"), "orchestration_step_executor")
            self.assertFalse(raw.get("execution_proceeds"))

    def test_allowed_writes_audit_and_merges_execution_detail(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ok"
            _minimal_product(root, pid)
            # Minimal signals + eligibility stubs are heavy; patch orchestration evaluation inside handler.
            from unittest.mock import patch

            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"findings_generate_eligible": False}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)  # eligibility failed inside handler
            ed = r.get("execution_detail") or {}
            self.assertIn("phase1_permission_decision", ed)
            self.assertEqual(
                (ed.get("phase1_permission_decision") or {}).get("aggregate_decision"),
                "allowed",
            )
            dec_dir = root / "runs" / "policy" / "phase1_decisions" / pid
            self.assertTrue(any(dec_dir.glob("phase1_*.json")))

    def test_confirm_policy_blocks_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_conf"
            _minimal_product(root, pid)
            pol = root / "products" / pid / "argus.policy.yaml"
            pol.write_text(
                """
schema: argus.project_permission_policy.v1
change_experiments: 'yes'
commit_local: 'yes'
deploy: 'no'
mutate_nonprod: 'confirm'
mutate_prod: 'no'
observe_prod_signals: 'yes'
push_remote: 'no'
""".strip()
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_BLOCKED)
            raw = json.loads(
                next((root / "runs" / "policy" / "phase1_decisions" / pid).glob("phase1_*.json")).read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(raw.get("aggregate_decision"), "blocked_pending_confirmation")


if __name__ == "__main__":
    unittest.main()
