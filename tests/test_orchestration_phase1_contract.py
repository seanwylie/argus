"""Orchestration dispatch contract proofs — full in-process ``action_id`` table (Phase-1 core + Phase-2 analysis/governance).

Align with ``docs/model-contracts.md`` (Phase-1 and Phase-2 sections). Phase-2 subset: ``tests/test_orchestration_phase2_contract.py``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.orchestrator.eligibility import (
    RC_ORCH_FEEDBACK_REPEATED_FAILURES,
    RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
    RC_ORCH_RETRY_REOPENED,
)
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_CONSIDER,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXECUTION_OUTCOMES_APPLY,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_TEMPORAL_REFRESH,
    ORCHESTRATION_POSTURE_SCHEMA,
)
from argus.orchestrator.state_pass import (
    build_cross_product_prioritization,
    orchestration_batch_advancement_path,
    orchestration_fleet_governance_rollup_path,
    orchestration_index_path,
    orchestration_latest_path,
    orchestration_operator_summary_path,
    orchestration_priority_tuple,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from tests.test_orchestration_step_executor import _minimal_product

# Full in-process dispatch table: ``step_executor.STEP_EXECUTION_REGISTRY`` keys (see ``_execute_orchestration_action_impl``).
PHASE1_STEP_EXECUTOR_ACTION_IDS: frozenset[str] = frozenset(
    {
        ACTION_SIGNALS_COLLECT,
        ACTION_AUDIT_RUN,
        ACTION_ORCHESTRATION_STATE_REFRESH,
        ACTION_REFINEMENT_START_PRODUCT_SPEC,
        ACTION_REFINEMENT_START_IDEA,
        ACTION_IMPLEMENTATION_PLAN_GENERATE,
        ACTION_REFINEMENT_RUN,
        ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
        ACTION_ESCALATION_CONSIDER,
        ACTION_ESCALATION_PACKET_GENERATE,
        ACTION_EXECUTION_OUTCOMES_APPLY,
        ACTION_TEMPORAL_REFRESH,
        ACTION_FINDINGS_GENERATE,
        ACTION_DECISIONS_GENERATE,
        ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_IDEAS_GENERATE,
        ACTION_EXPERIMENTS_PROPOSE,
        ACTION_EXPERIMENTS_PRIORITIZE,
        ACTION_EXPERIMENTS_CREATE,
        ACTION_EXPERIMENTS_ACTIVATE,
        ACTION_EXPERIMENTS_EVALUATE,
        ACTION_EXPERIMENTS_CLOSE_STALE,
        ACTION_EXPERIMENTS_SURFACE_FINDINGS,
        ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_PLANNING_REFRESH_FROM_STRATEGY,
        ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    }
)


class TestOrchestrationPhase1Contract(unittest.TestCase):
    def test_step_executor_covers_documented_action_ids(self) -> None:
        self.assertEqual(len(PHASE1_STEP_EXECUTOR_ACTION_IDS), 26)
        for aid in PHASE1_STEP_EXECUTOR_ACTION_IDS:
            with self.subTest(action_id=aid):
                with TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    pid = "p_contract"
                    _minimal_product(root, pid)
                    out = execute_orchestration_action(root, pid, aid)
                    self.assertNotEqual(
                        (out.get("action_status") or ""),
                        "",
                        msg=f"executor must return action_status for {aid!r}",
                    )
                    if out.get("action_status") == "queued_unhandled":
                        self.fail(
                            f"documented action_id {aid!r} unexpectedly queued_unhandled — "
                            "update Phase-1 contract or step_executor dispatch"
                        )

    def test_execution_feedback_parity_reason_code_strings(self) -> None:
        self.assertEqual(RC_ORCH_FEEDBACK_REPEATED_FAILURES, "orchestration_execution_feedback_repeated_failures")
        self.assertEqual(
            RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
            "orchestration_execution_feedback_repeated_queued_unhandled",
        )
        self.assertEqual(RC_ORCH_RETRY_REOPENED, "orchestration_retry_reopened")

    def test_orchestration_posture_top_level_keys_stable(self) -> None:
        from argus.orchestrator.eligibility import evaluate_product_orchestration

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_posture_keys"
            _minimal_product(root, pid)
            with patch("argus.orchestrator.artifact_snapshot.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
        op = state.get("orchestration_posture") or {}
        self.assertEqual(op.get("schema"), ORCHESTRATION_POSTURE_SCHEMA)
        self.assertEqual(
            set(op.keys()),
            {"schema", "execution_feedback_crosswalk", "retry_reopened", "repeat_execution_feedback_thresholds"},
        )

    def test_latest_artifact_paths_repo_relative(self) -> None:
        root = Path("/repo")
        self.assertEqual(
            orchestration_index_path(root).relative_to(root),
            Path("runs/orchestration/latest/index.json"),
        )
        self.assertEqual(
            orchestration_batch_advancement_path(root).relative_to(root),
            Path("runs/orchestration/latest/batch_advancement.json"),
        )
        self.assertEqual(
            orchestration_operator_summary_path(root).relative_to(root),
            Path("runs/orchestration/latest/operator_summary.json"),
        )
        self.assertEqual(
            orchestration_fleet_governance_rollup_path(root).relative_to(root),
            Path("runs/orchestration/latest/fleet_governance_rollup.json"),
        )
        self.assertEqual(
            orchestration_latest_path(root, "x").relative_to(root),
            Path("runs/orchestration/latest/x.json"),
        )

    def test_cross_product_prioritization_shape(self) -> None:
        minimal = {
            "a": {
                "eligible_actions": [{"action_id": "signals_collect"}],
                "escalation_triggers": [],
                "eligibility_facts": {},
            },
            "b": {
                "eligible_actions": [],
                "escalation_triggers": [],
                "eligibility_facts": {},
            },
        }
        out = build_cross_product_prioritization(minimal)
        self.assertIn("ranked_product_ids", out)
        self.assertIn("entries", out)
        self.assertIsInstance(out["entries"], list)
        ent0 = out["entries"][0]
        self.assertIn("priority_tuple", ent0)
        self.assertIn("priority_labels", ent0)
        self.assertEqual(len(ent0["priority_tuple"]), 5)

    def test_orchestration_priority_tuple_width_matches_labels(self) -> None:
        state = {
            "eligible_actions": [],
            "escalation_triggers": [],
            "eligibility_facts": {},
        }
        t = orchestration_priority_tuple(state)
        self.assertEqual(len(t), 5)
        self.assertEqual(
            set((build_cross_product_prioritization({"x": state})["entries"][0]["priority_labels"] or {}).keys()),
            {
                "repeated_failure_threshold",
                "repeated_queued_unhandled_threshold",
                "has_escalation_triggers",
                "has_eligible_actions",
                "retry_reopened_suppression",
            },
        )
