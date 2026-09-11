"""Orchestration eligibility reads orchestration_execution_feedback artifacts."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.audit.bundle import bundle_audit_path
from argus.core.serialize import dumps_json
from argus.orchestrator.eligibility import (
    RC_ORCH_FAILED_ACTION_DEPRIORITIZED,
    RC_ORCH_FEEDBACK_FAILED,
    RC_ORCH_FEEDBACK_QUEUED_UNHANDLED,
    RC_ORCH_FEEDBACK_REPEATED_FAILURES,
    RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
    RC_ORCH_RETRY_REOPENED,
    RC_ORCH_SAME_ACTION_AFTER_SUCCESS,
    RC_ORCH_UNHANDLED_ACTION_DEPRIORITIZED,
    EligibleAction,
    _execution_feedback_headline_suffix,
    _retry_reopened_headline_suffix,
    evaluate_product_orchestration,
)
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA,
    ORCHESTRATION_POSTURE_SCHEMA,
)


def _minimal_product(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: EligFb
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


def _write_feedback(
    root: Path,
    product_id: str,
    *,
    action_id: str,
    status: str,
    finished_at_utc: str,
    execution_error: str | None = None,
) -> Path:
    body = {
        "schema": ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA,
        "product_id": product_id,
        "action_id": action_id,
        "orchestration_action_status": status,
        "success": status == ACTION_STATUS_EXECUTED,
        "finished_at_utc": finished_at_utc,
        "observed_at_utc": finished_at_utc,
        "execution_detail": {},
        "execution_error": execution_error,
        "provenance": {"source": "orchestration_step_executor", "kind": "in_process", "source_ref": "x"},
    }
    d = root / "runs" / "execution" / product_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"orchestration_feedback_{action_id}_test.json"
    p.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return p


def _write_feedback_named(
    root: Path,
    product_id: str,
    *,
    action_id: str,
    status: str,
    finished_at_utc: str,
    filename: str,
    execution_error: str | None = None,
) -> Path:
    body = {
        "schema": ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA,
        "product_id": product_id,
        "action_id": action_id,
        "orchestration_action_status": status,
        "success": status == ACTION_STATUS_EXECUTED,
        "finished_at_utc": finished_at_utc,
        "observed_at_utc": finished_at_utc,
        "execution_detail": {},
        "execution_error": execution_error,
        "provenance": {"source": "orchestration_step_executor", "kind": "in_process", "source_ref": filename},
    }
    d = root / "runs" / "execution" / product_id
    d.mkdir(parents=True, exist_ok=True)
    p = d / filename
    p.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return p


def _write_temporal_latest(
    root: Path,
    product_id: str,
    *,
    collected_at_utc: str,
    worst_freshness_status: str = "stale",
) -> Path:
    d = root / "runs" / "temporal" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{product_id}.json"
    body = {
        "schema": "argus.temporal_bundle.v1",
        "worst_freshness_status": worst_freshness_status,
        "collected_at_utc": collected_at_utc,
    }
    p.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return p


def _write_audit_bundle_stub(root: Path, product_id: str, *, generated_at_utc: str) -> Path:
    p = bundle_audit_path(root, product_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "schema": "argus.audit_bundle.v1",
        "product_id": product_id,
        "generated_at_utc": generated_at_utc,
        "angles": {"product_gap": {"angle_status": "stub"}},
    }
    p.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return p


class TestEligibilityOrchestrationFeedback(unittest.TestCase):
    def test_retry_reopened_suffix_none_without_suppression_or_intersection(self) -> None:
        self.assertIsNone(
            _retry_reopened_headline_suffix(
                feedback_summary={"failed_action_ids": [ACTION_SIGNALS_COLLECT]},
                suppressed_action_ids=[],
                next_action=ACTION_SIGNALS_COLLECT,
                deduped=[EligibleAction(ACTION_SIGNALS_COLLECT, "r", ())],
            )
        )
        self.assertIsNone(
            _retry_reopened_headline_suffix(
                feedback_summary={"failed_action_ids": []},
                suppressed_action_ids=[ACTION_SIGNALS_COLLECT],
                next_action=ACTION_SIGNALS_COLLECT,
                deduped=[EligibleAction(ACTION_SIGNALS_COLLECT, "r", ())],
            )
        )

    def test_retry_reopened_suffix_when_suppressed_failed_and_relevant(self) -> None:
        s = _retry_reopened_headline_suffix(
            feedback_summary={"failed_action_ids": [ACTION_SIGNALS_COLLECT]},
            suppressed_action_ids=[ACTION_SIGNALS_COLLECT],
            next_action=ACTION_IMPLEMENTATION_PLAN_GENERATE,
            deduped=[
                EligibleAction(ACTION_SIGNALS_COLLECT, "r", ()),
                EligibleAction(ACTION_IMPLEMENTATION_PLAN_GENERATE, "r2", ()),
            ],
        )
        self.assertIsNotNone(s)
        assert s is not None
        self.assertIn("retry posture reopened", s)
        self.assertIn(ACTION_SIGNALS_COLLECT, s)
        self.assertIn("historical failure retained", s)

    def test_headline_suffix_next_action_failed(self) -> None:
        s = _execution_feedback_headline_suffix(
            feedback_summary={
                "failed_action_ids": [ACTION_SIGNALS_COLLECT],
                "queued_unhandled_action_ids": [],
            },
            next_action=ACTION_SIGNALS_COLLECT,
            deduped_action_ids={ACTION_SIGNALS_COLLECT},
        )
        self.assertIsNotNone(s)
        assert s is not None
        self.assertIn("next_action", s)
        self.assertIn(ACTION_SIGNALS_COLLECT, s)
        self.assertIn("failed", s)

    def test_headline_suffix_next_action_queued_unhandled(self) -> None:
        s = _execution_feedback_headline_suffix(
            feedback_summary={
                "failed_action_ids": [],
                "queued_unhandled_action_ids": [ACTION_SIGNALS_COLLECT],
            },
            next_action=ACTION_SIGNALS_COLLECT,
            deduped_action_ids={ACTION_SIGNALS_COLLECT},
        )
        self.assertIsNotNone(s)
        assert s is not None
        self.assertIn("queued_unhandled", s)

    def test_headline_suffix_unrelated_feedback_no_material_suffix(self) -> None:
        s = _execution_feedback_headline_suffix(
            feedback_summary={
                "failed_action_ids": ["only_in_feedback_not_eligible"],
                "queued_unhandled_action_ids": [],
            },
            next_action=ACTION_SIGNALS_COLLECT,
            deduped_action_ids={ACTION_SIGNALS_COLLECT},
        )
        self.assertIsNone(s)

    def test_failed_feedback_adds_blocker_and_escalation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "pfail"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                execution_error="collect failed",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            self.assertTrue(state["eligibility_facts"].get("orchestration_feedback_recent_failed"))
            kinds = [b.get("kind") for b in state["blockers"]]
            self.assertIn("orchestration_execution_failed", kinds)
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertIn(RC_ORCH_FEEDBACK_FAILED, codes)
            op = state.get("orchestration_posture") or {}
            self.assertEqual(op.get("schema"), ORCHESTRATION_POSTURE_SCHEMA)
            xw = op.get("execution_feedback_crosswalk") or {}
            na = state.get("next_action")
            if na == ACTION_SIGNALS_COLLECT:
                self.assertTrue(xw.get("next_action_matches_recent_failed_execution"))
                self.assertIn(
                    ACTION_SIGNALS_COLLECT,
                    xw.get("eligible_action_ids_with_recent_failed_execution") or [],
                )

    def test_queued_unhandled_surfaces_in_waiting_and_escalation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "punh"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id="unknown_action",
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            self.assertTrue(state["eligibility_facts"].get("orchestration_feedback_recent_queued_unhandled"))
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertIn(RC_ORCH_FEEDBACK_QUEUED_UNHANDLED, codes)
            wi_kinds = [w.get("kind") for w in state["waiting_inputs"]]
            self.assertIn("orchestration_execution_feedback", wi_kinds)
            orch_wi = next(w for w in state["waiting_inputs"] if w.get("kind") == "orchestration_execution_feedback")
            self.assertEqual(orch_wi.get("action_ids"), ["unknown_action"])

    def test_executed_success_deprioritizes_repeat_same_first_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "prep"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_EXECUTED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in state["eligible_actions"]]
            self.assertGreaterEqual(len(ids), 2)
            self.assertNotEqual(ids[0], ACTION_SIGNALS_COLLECT)
            sc = next(a for a in state["eligible_actions"] if a["action_id"] == ACTION_SIGNALS_COLLECT)
            self.assertIn(RC_ORCH_SAME_ACTION_AFTER_SUCCESS, sc.get("reason_codes") or [])

    def test_latest_outcome_per_action_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_multi"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_EXECUTED,
                finished_at_utc="2026-04-12T09:00:00+00:00",
            )
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T11:00:00+00:00",
                execution_error="second",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            by_aid = state["eligibility_facts"].get("orchestration_feedback_by_action_id") or {}
            self.assertEqual(
                by_aid[ACTION_SIGNALS_COLLECT]["orchestration_action_status"],
                ACTION_STATUS_FAILED,
            )

    def test_failed_first_eligible_deprioritized_not_blind_repeat(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fd"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                execution_error="x",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in state["eligible_actions"]]
            self.assertGreaterEqual(len(ids), 2)
            self.assertNotEqual(ids[0], ACTION_SIGNALS_COLLECT)
            sc = next(a for a in state["eligible_actions"] if a["action_id"] == ACTION_SIGNALS_COLLECT)
            self.assertIn(RC_ORCH_FAILED_ACTION_DEPRIORITIZED, sc.get("reason_codes") or [])
            self.assertEqual(
                state["eligibility_facts"].get("orchestration_feedback_failed_deprioritize_suppressed_action_ids"),
                [],
            )
            reason = state["orchestration_status_reason"]
            self.assertIn(
                f"recent failure also recorded for eligible action_id(s): {ACTION_SIGNALS_COLLECT}",
                reason,
            )
            self.assertNotIn("retry posture reopened", reason)
            self.assertNotIn(RC_ORCH_RETRY_REOPENED, state["orchestration_status_reason_codes"])

    def test_failed_deprioritize_suppressed_when_newer_observability_evidence(self) -> None:
        """Temporal/signals-class evidence newer than failure time — do not deprioritize failed action."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_retry"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                execution_error="x",
            )
            _write_temporal_latest(root, pid, collected_at_utc="2026-04-12T11:00:00+00:00")
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            self.assertEqual(
                state["eligibility_facts"].get("orchestration_feedback_failed_deprioritize_suppressed_action_ids"),
                [ACTION_SIGNALS_COLLECT],
            )
            ids = [a["action_id"] for a in state["eligible_actions"]]
            self.assertGreaterEqual(len(ids), 2)
            self.assertEqual(ids[0], ACTION_SIGNALS_COLLECT)
            sc = next(a for a in state["eligible_actions"] if a["action_id"] == ACTION_SIGNALS_COLLECT)
            self.assertNotIn(RC_ORCH_FAILED_ACTION_DEPRIORITIZED, sc.get("reason_codes") or [])
            reason = state["orchestration_status_reason"]
            self.assertIn("retry posture reopened", reason)
            self.assertIn(ACTION_SIGNALS_COLLECT, reason)
            posture = state.get("orchestration_posture") or {}
            rr = posture.get("retry_reopened") or {}
            self.assertTrue(rr.get("active"))
            self.assertEqual(rr.get("action_ids"), [ACTION_SIGNALS_COLLECT])
            self.assertIn(RC_ORCH_RETRY_REOPENED, state["orchestration_status_reason_codes"])

    def test_unrelated_newer_audit_does_not_suppress_signals_failure_deprioritize(self) -> None:
        """Audit-only freshness does not clear signals_collect failure deprioritization."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_audit_only"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                execution_error="x",
            )
            _write_audit_bundle_stub(root, pid, generated_at_utc="2026-04-12T11:00:00+00:00")
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            self.assertEqual(
                state["eligibility_facts"].get("orchestration_feedback_failed_deprioritize_suppressed_action_ids"),
                [],
            )
            ids = [a["action_id"] for a in state["eligible_actions"]]
            self.assertGreaterEqual(len(ids), 2)
            self.assertNotEqual(ids[0], ACTION_SIGNALS_COLLECT)
            sc = next(a for a in state["eligible_actions"] if a["action_id"] == ACTION_SIGNALS_COLLECT)
            self.assertIn(RC_ORCH_FAILED_ACTION_DEPRIORITIZED, sc.get("reason_codes") or [])
            self.assertNotIn("retry posture reopened", state["orchestration_status_reason"])
            self.assertNotIn(RC_ORCH_RETRY_REOPENED, state["orchestration_status_reason_codes"])

    def test_unhandled_first_eligible_deprioritized(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_uh"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in state["eligible_actions"]]
            self.assertGreaterEqual(len(ids), 2)
            self.assertNotEqual(ids[0], ACTION_SIGNALS_COLLECT)
            sc = next(a for a in state["eligible_actions"] if a["action_id"] == ACTION_SIGNALS_COLLECT)
            self.assertIn(RC_ORCH_UNHANDLED_ACTION_DEPRIORITIZED, sc.get("reason_codes") or [])
            reason = state["orchestration_status_reason"]
            self.assertIn(
                f"queued_unhandled also recorded for eligible action_id(s): {ACTION_SIGNALS_COLLECT}",
                reason,
            )

    def test_unrelated_failed_feedback_does_not_reorder_unrelated_first(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_unrel"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id="only_in_feedback_not_eligible",
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            first = state["eligible_actions"][0]["action_id"]
            self.assertEqual(first, ACTION_SIGNALS_COLLECT)
            codes = [a.get("reason_codes") or [] for a in state["eligible_actions"] if a["action_id"] == ACTION_SIGNALS_COLLECT][0]
            self.assertNotIn(RC_ORCH_FAILED_ACTION_DEPRIORITIZED, codes)
            reason = state["orchestration_status_reason"]
            self.assertNotIn("recent failure also recorded for eligible action_id(s)", reason)
            self.assertNotIn("next_action ", reason)

    def test_per_action_recent_fail_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fcnt"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_signals_collect_a.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:30:00+00:00",
                filename="orchestration_feedback_signals_collect_b.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            fc = state["eligibility_facts"].get("orchestration_feedback_recent_fail_count_by_action_id") or {}
            self.assertEqual(fc.get(ACTION_SIGNALS_COLLECT), 2)

    def test_per_action_recent_queued_unhandled_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_uhcnt"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_signals_collect_uh1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:15:00+00:00",
                filename="orchestration_feedback_signals_collect_uh2.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            uc = (
                state["eligibility_facts"].get("orchestration_feedback_recent_queued_unhandled_count_by_action_id")
                or {}
            )
            self.assertEqual(uc.get(ACTION_SIGNALS_COLLECT), 2)

    def test_last_success_finished_at_most_recent_executed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ls"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_EXECUTED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_signals_collect_ex1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_EXECUTED,
                finished_at_utc="2026-04-12T11:00:00+00:00",
                filename="orchestration_feedback_signals_collect_ex2.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            ls = (
                state["eligibility_facts"].get("orchestration_feedback_last_success_finished_at_utc_by_action_id")
                or {}
            )
            self.assertEqual(ls.get(ACTION_SIGNALS_COLLECT), "2026-04-12T11:00:00+00:00")

    def test_per_action_outcome_facts_isolated_between_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_iso"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_sc_f1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:05:00+00:00",
                filename="orchestration_feedback_sc_f2.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_AUDIT_RUN,
                status=ACTION_STATUS_EXECUTED,
                finished_at_utc="2026-04-12T10:20:00+00:00",
                filename="orchestration_feedback_audit_ex.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            facts = state["eligibility_facts"]
            fc = facts.get("orchestration_feedback_recent_fail_count_by_action_id") or {}
            ls = facts.get("orchestration_feedback_last_success_finished_at_utc_by_action_id") or {}
            self.assertEqual(fc.get(ACTION_SIGNALS_COLLECT), 2)
            self.assertIsNone(fc.get(ACTION_AUDIT_RUN))
            self.assertEqual(ls.get(ACTION_AUDIT_RUN), "2026-04-12T10:20:00+00:00")
            self.assertIsNone(ls.get(ACTION_SIGNALS_COLLECT))

    def test_repeated_failures_escalation_trigger_and_headline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rep_f"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_sc_rf1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:30:00+00:00",
                filename="orchestration_feedback_sc_rf2.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertIn(RC_ORCH_FEEDBACK_REPEATED_FAILURES, codes)
            rep = [t for t in state["escalation_triggers"] if t.get("code") == RC_ORCH_FEEDBACK_REPEATED_FAILURES]
            self.assertEqual(len(rep), 1)
            self.assertEqual(rep[0].get("action_id"), ACTION_SIGNALS_COLLECT)
            self.assertIn("repeated failed orchestration feedback", state["orchestration_status_reason"])
            posture = state.get("orchestration_posture") or {}
            self.assertEqual(posture.get("schema"), ORCHESTRATION_POSTURE_SCHEMA)
            rpt = posture.get("repeat_execution_feedback_thresholds") or {}
            rf = rpt.get("failures") or []
            self.assertEqual(len(rf), 1)
            self.assertEqual(rf[0].get("action_id"), ACTION_SIGNALS_COLLECT)
            self.assertEqual(rf[0].get("recent_fail_count"), 2)
            self.assertEqual(rpt.get("queued_unhandled"), [])
            self.assertIn(
                RC_ORCH_FEEDBACK_REPEATED_FAILURES,
                state["orchestration_status_reason_codes"],
            )

    def test_repeated_queued_unhandled_escalation_trigger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rep_uh"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_sc_uh1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:20:00+00:00",
                filename="orchestration_feedback_sc_uh2.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertIn(RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED, codes)
            self.assertIn("repeated queued_unhandled orchestration feedback", state["orchestration_status_reason"])
            posture = state.get("orchestration_posture") or {}
            rpt = posture.get("repeat_execution_feedback_thresholds") or {}
            uh = rpt.get("queued_unhandled") or []
            self.assertEqual(len(uh), 1)
            self.assertEqual(uh[0].get("action_id"), ACTION_SIGNALS_COLLECT)
            self.assertEqual(uh[0].get("recent_queued_unhandled_count"), 2)
            self.assertEqual(rpt.get("failures"), [])
            self.assertIn(
                RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
                state["orchestration_status_reason_codes"],
            )

    def test_single_failure_no_repeat_failure_trigger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_one_f"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertNotIn(RC_ORCH_FEEDBACK_REPEATED_FAILURES, codes)
            self.assertIn(RC_ORCH_FEEDBACK_FAILED, codes)
            self.assertNotIn(
                RC_ORCH_FEEDBACK_REPEATED_FAILURES,
                state["orchestration_status_reason_codes"],
            )
            posture = state.get("orchestration_posture") or {}
            rpt = posture.get("repeat_execution_feedback_thresholds") or {}
            self.assertEqual(rpt.get("failures"), [])
            self.assertEqual(rpt.get("queued_unhandled"), [])

    def test_single_queued_unhandled_no_repeat_trigger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_one_uh"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_QUEUED_UNHANDLED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertNotIn(RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED, codes)
            self.assertIn(RC_ORCH_FEEDBACK_QUEUED_UNHANDLED, codes)
            self.assertNotIn(
                RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
                state["orchestration_status_reason_codes"],
            )

    def test_repeat_failure_isolated_per_action_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_iso_rep"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_sc_iso1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:05:00+00:00",
                filename="orchestration_feedback_sc_iso2.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_AUDIT_RUN,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_audit_one.json",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            rep = [t for t in state["escalation_triggers"] if t.get("code") == RC_ORCH_FEEDBACK_REPEATED_FAILURES]
            self.assertEqual(len(rep), 1)
            self.assertEqual(rep[0].get("action_id"), ACTION_SIGNALS_COLLECT)

    def test_retry_suppressed_skips_repeated_failure_escalation_trigger(self) -> None:
        """Newer evidence suppresses deprioritize — do not add repeated-failure escalation for that action."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sup_rep"
            _minimal_product(root, pid)
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
                filename="orchestration_feedback_sc_s1.json",
            )
            _write_feedback_named(
                root,
                pid,
                action_id=ACTION_SIGNALS_COLLECT,
                status=ACTION_STATUS_FAILED,
                finished_at_utc="2026-04-12T10:15:00+00:00",
                filename="orchestration_feedback_sc_s2.json",
            )
            _write_temporal_latest(root, pid, collected_at_utc="2026-04-12T11:00:00+00:00")
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            self.assertEqual(
                state["eligibility_facts"].get("orchestration_feedback_failed_deprioritize_suppressed_action_ids"),
                [ACTION_SIGNALS_COLLECT],
            )
            codes = [t.get("code") for t in state["escalation_triggers"]]
            self.assertNotIn(RC_ORCH_FEEDBACK_REPEATED_FAILURES, codes)
            self.assertNotIn(
                RC_ORCH_FEEDBACK_REPEATED_FAILURES,
                state["orchestration_status_reason_codes"],
            )

    def test_artifacts_execution_includes_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "part"
            _minimal_product(root, pid)
            _write_feedback(
                root,
                pid,
                action_id=ACTION_ORCHESTRATION_STATE_REFRESH,
                status=ACTION_STATUS_EXECUTED,
                finished_at_utc="2026-04-12T10:00:00+00:00",
            )
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            ex = state["artifacts"]["execution"]
            self.assertIn("orchestration_feedback", ex)
            self.assertEqual(ex["orchestration_feedback"]["latest"]["action_id"], ACTION_ORCHESTRATION_STATE_REFRESH)

    def test_orchestration_posture_minimal_when_no_execution_feedback_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_posture_clean"
            _minimal_product(root, pid)
            with patch("argus.orchestrator.eligibility.utc_now") as ut:
                from datetime import datetime, timezone

                ut.return_value = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
                state = evaluate_product_orchestration(root, pid)
            op = state.get("orchestration_posture") or {}
            self.assertEqual(op.get("schema"), ORCHESTRATION_POSTURE_SCHEMA)
            self.assertFalse((op.get("retry_reopened") or {}).get("active"))
            self.assertEqual((op.get("retry_reopened") or {}).get("action_ids"), [])
            xw = op.get("execution_feedback_crosswalk") or {}
            self.assertFalse(xw.get("next_action_matches_recent_failed_execution"))
            self.assertFalse(xw.get("next_action_matches_recent_queued_unhandled"))
            self.assertEqual(xw.get("eligible_action_ids_with_recent_failed_execution"), [])
            self.assertEqual(xw.get("eligible_action_ids_with_recent_queued_unhandled"), [])
            rpt = op.get("repeat_execution_feedback_thresholds") or {}
            self.assertEqual(rpt.get("failures"), [])
            self.assertEqual(rpt.get("queued_unhandled"), [])


if __name__ == "__main__":
    unittest.main()
