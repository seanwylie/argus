"""Execution feedback must embed Phase 1 truth (or explicit absence) on all orchestration paths."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.phase1_execution_detail import PHASE1_EMBED_SCHEMA_VERSION
from argus.orchestrator.state_models import (
    ACTION_FINDINGS_GENERATE,
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
)
from argus.orchestrator.step_executor import STEP_EXECUTION_REGISTRY, execute_orchestration_action
from tests.test_orchestration_step_executor import _minimal_product


def _assert_embed_keys(ed: dict, *, evaluated: bool) -> None:
    assert ed.get("phase1_embed_schema_version") == PHASE1_EMBED_SCHEMA_VERSION
    assert ed.get("phase1_evaluated") is evaluated
    if evaluated:
        assert ed.get("phase1_permission_decision") is not None
        assert ed.get("phase1_decision_audit_path")
        assert isinstance(ed["phase1_permission_decision"], dict)
        assert ed.get("phase1_not_evaluated_reason") is None
    else:
        assert ed.get("phase1_permission_decision") is None
        assert ed.get("phase1_decision_audit_path") is None
        assert isinstance(ed.get("phase1_not_evaluated_reason"), str)


class TestOrchestrationFeedbackPhase1Embed(unittest.TestCase):
    def test_allowed_embeds_phase1(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_yes"
            _minimal_product(root, pid)
            (root / "products" / pid / "argus.policy.yaml").write_text(
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
            from unittest.mock import patch

            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"findings_generate_eligible": False}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            ed = r.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=True)
            self.assertEqual(
                (ed.get("phase1_permission_decision") or {}).get("aggregate_decision"),
                "allowed",
            )

    def test_blocked_confirm_embeds_phase1(self) -> None:
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
            ed = r.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=True)
            self.assertEqual(
                (ed.get("phase1_permission_decision") or {}).get("aggregate_decision"),
                "blocked_pending_confirmation",
            )

    def test_refused_embeds_phase1(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_no"
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
            ed = r.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=True)
            self.assertEqual(
                (ed.get("phase1_permission_decision") or {}).get("aggregate_decision"),
                "refused",
            )

    def test_queued_unhandled_explicit_not_evaluated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, "not_a_registered_action")
            self.assertEqual(r["action_status"], ACTION_STATUS_QUEUED_UNHANDLED)
            ed = r.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=False)
            self.assertIn("STEP_EXECUTION_REGISTRY", ed.get("phase1_not_evaluated_reason") or "")

    def test_invalid_product_explicit_not_evaluated(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = execute_orchestration_action(root, "nope", ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            ed = r.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=False)
            self.assertIn("inventory", (ed.get("phase1_not_evaluated_reason") or "").lower())

    def test_feedback_file_on_disk_contains_embed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_disk"
            _minimal_product(root, pid)
            (root / "products" / pid / "argus.policy.yaml").write_text(
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
            execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            files = list((root / "runs" / "execution" / pid).glob("orchestration_feedback_*.json"))
            self.assertEqual(len(files), 1)
            data = json.loads(files[0].read_text(encoding="utf-8"))
            ed = data.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=True)

    def test_unmapped_action_explicit_not_evaluated(self) -> None:
        """Registry has fn but no Phase1 mapping — should not happen in production; must not omit Phase1 fields."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            fake_id = "__phase1_mapping_probe__"
            orig = STEP_EXECUTION_REGISTRY.get(ACTION_FINDINGS_GENERATE)
            STEP_EXECUTION_REGISTRY[fake_id] = orig  # type: ignore[assignment]
            try:
                r = execute_orchestration_action(root, pid, fake_id)
            finally:
                del STEP_EXECUTION_REGISTRY[fake_id]
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            ed = r.get("execution_detail") or {}
            _assert_embed_keys(ed, evaluated=False)
            self.assertIn("mapping", (ed.get("phase1_not_evaluated_reason") or "").lower())

