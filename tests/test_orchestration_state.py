"""State-driven orchestration eligibility (durable artifacts only)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import dumps_json
from argus.orchestrator.advancement import advance_orchestration, orchestration_advancement_payload
from argus.orchestrator.eligibility import (
    ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR,
    ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS,
    RC_APPROVAL_PENDING,
    RC_AUDIT_PRODUCT_GAP_INCOMPLETE,
    RC_COMPOUND_CRITICAL_ARTIFACTS,
    RC_CONVERGENCE_BLOCKING_GROUNDED,
    RC_DECISIONS_GENERATE_FINDINGS_READY,
    RC_ESCALATION_PACKET_GENERATE_POSTURE,
    RC_FINDINGS_GENERATE_SIGNALS_READY,
    RC_IMPL_PLAN_BLOCKED_REFINEMENT_WAITING,
    RC_IMPL_PLAN_BLOCKED_SIGNALS_STALE,
    RC_PRODUCT_SPEC_FINALIZED_NO_IMPL_PLAN,
    RC_REVIEW_INPUT_WAIT_EXCEEDED,
    RC_SIGNALS_BUNDLE_ABSENT,
    RC_TEMPORAL_LATEST_ABSENT,
    RC_TEMPORAL_WORST_STALE,
    evaluate_product_orchestration,
)
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_ESCALATION_CONSIDER,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_FINDINGS_GENERATE,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_QUEUED,
    ACTION_STATUS_SKIPPED,
    ACTION_TEMPORAL_REFRESH,
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_COMPLETE,
    ORCH_STATUS_ELIGIBLE,
    ORCH_STATUS_ESCALATED,
    ORCH_STATUS_STALE_REFRESH_NEEDED,
    ORCHESTRATION_ADVANCEMENT_SCHEMA,
    ORCHESTRATION_FLEET_GOVERNANCE_ROLLUP_SCHEMA,
    ORCHESTRATION_INDEX_SCHEMA,
    ORCHESTRATION_STATE_SCHEMA,
    ORCHESTRATION_TASK_SCHEMA,
    REFINEMENT_REVIEW_ABSENT,
    REFINEMENT_REVIEW_AWAITING_REVIEW_INPUT,
    REFINEMENT_REVIEW_NOT_CONVERGED_STUCK,
    REFINEMENT_REVIEW_REJECTED,
    STATUS_BLOCKED_EXHAUSTED,
    STATUS_BLOCKED_WAITING_INPUT,
    STATUS_ELIGIBLE,
    WAITING_KIND_EXECUTION_APPROVAL,
    WAITING_KIND_OBSERVABILITY_SIGNALS,
    WAITING_KIND_REFINEMENT_GROUNDED_INPUT,
)
from argus.orchestrator.state_pass import (
    build_fleet_governance_rollup_payload,
    build_operator_summary_payload,
    orchestration_advancement_path,
    orchestration_fleet_governance_rollup_path,
    orchestration_index_path,
    orchestration_latest_path,
    write_orchestration_state,
    write_orchestration_state_payload,
    write_orchestration_states_batch,
)
from argus.orchestrator.task_artifact import orchestration_task_path, task_type_for_action
from tests.test_orchestration_step_executor import _minimal_product

# Shared empty dict for payloads copied by SUT (safe: advancement copies eligibility_facts).
_EMPTY_ELIGIBILITY_FACTS: dict[str, object] = {}


def _write(p: Path, obj: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(obj) + "\n", encoding="utf-8")


class TestOrchestrationState(unittest.TestCase):
    def test_empty_artifacts_suggests_collect_audit_and_spec_start(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            out = evaluate_product_orchestration(root, pid)
            self.assertEqual(out["schema"], ORCHESTRATION_STATE_SCHEMA)
            self.assertEqual(out["overall_status"], STATUS_ELIGIBLE)
            self.assertEqual(out["orchestration_status"], ORCH_STATUS_STALE_REFRESH_NEEDED)
            self.assertEqual(out["artifacts"]["refinement"]["review_state"], REFINEMENT_REVIEW_ABSENT)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_SIGNALS_COLLECT, ids)
            self.assertIn(ACTION_AUDIT_RUN, ids)
            self.assertIn(ACTION_REFINEMENT_START_PRODUCT_SPEC, ids)
            self.assertIn("eligibility_facts", out)
            self.assertTrue(out["eligibility_facts"]["audit_product_gap_incomplete"])
            self.assertTrue(out["escalation_eligible"])
            self.assertTrue(any(t.get("code") == RC_COMPOUND_CRITICAL_ARTIFACTS for t in out["escalation_triggers"]))

    def test_product_spec_finalized_impl_plan_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": "ref_20260101T000000Z_abcd1234",
                            "artifact_type": "product_spec",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "approved",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / "ref_20260101T000000Z_abcd1234"
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": "ref_20260101T000000Z_abcd1234",
                    "artifact_type": "product_spec",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "approved",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {
                        "product_gap": {"angle_status": "active", "summary_lines": ["ok"]},
                        "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                    },
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_IMPLEMENTATION_PLAN_GENERATE, ids)
            impl = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertIn(RC_PRODUCT_SPEC_FINALIZED_NO_IMPL_PLAN, impl["reason_codes"])
            self.assertNotIn(ACTION_REFINEMENT_START_PRODUCT_SPEC, ids)
            self.assertEqual(out["orchestration_status"], ORCH_STATUS_ELIGIBLE)
            self.assertEqual(out["next_action"], ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertTrue(out["eligibility_facts"]["implementation_plan_generate_eligible"])
            prog = out["progression"]["implementation_plan"]
            self.assertTrue(prog["generate_eligible"])
            self.assertEqual(prog["blocked_reason_codes"], [])
            self.assertFalse(prog["implementation_plan_stale_vs_product_spec"])
            self.assertEqual(out["artifacts"]["refinement"]["review_state"], "in_progress")
            self.assertFalse(out["eligibility_facts"]["audit_product_gap_incomplete"])

    def test_impl_plan_generate_blocked_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": "ref_20260101T000000Z_abcd1234",
                            "artifact_type": "product_spec",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "approved",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / "ref_20260101T000000Z_abcd1234"
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": "ref_20260101T000000Z_abcd1234",
                    "artifact_type": "product_spec",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "approved",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {
                        "product_gap": {"angle_status": "active", "summary_lines": ["ok"]},
                        "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                    },
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertNotIn(ACTION_IMPLEMENTATION_PLAN_GENERATE, ids)
            self.assertIn(ACTION_SIGNALS_COLLECT, ids)
            self.assertFalse(out["eligibility_facts"]["implementation_plan_generate_eligible"])
            br = out["progression"]["implementation_plan"]["blocked_reason_codes"]
            self.assertIn(RC_IMPL_PLAN_BLOCKED_SIGNALS_STALE, br)

    def test_impl_plan_generate_blocked_when_refinement_waiting(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            ref = root / "runs" / "refinement"
            idea_sid = "ref_idea_20260201T000000Z_abcd1234"
            ps_sid = "ref_ps_20260101T000000Z_abcd1234"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": idea_sid,
                            "artifact_type": "idea",
                            "source_id": "src",
                            "product_id": pid,
                            "status": "in_review",
                            "current_round": 0,
                            "updated_at_utc": "2026-02-01T00:00:00+00:00",
                        },
                        {
                            "session_id": ps_sid,
                            "artifact_type": "product_spec",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "approved",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            idea_d = ref / idea_sid
            _write(
                idea_d / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": idea_sid,
                    "artifact_type": "idea",
                    "source_id": "src",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "in_review",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-02-01T00:00:00+00:00",
                    "updated_at_utc": "2026-02-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(idea_d / "drafts" / "round_0.json", {"schema": "argus.artifact_draft.v1", "round_number": 0})
            ps_d = ref / ps_sid
            _write(
                ps_d / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": ps_sid,
                    "artifact_type": "product_spec",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "approved",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {
                        "product_gap": {"angle_status": "active", "summary_lines": ["ok"]},
                        "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                    },
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertEqual(out["overall_status"], STATUS_BLOCKED_WAITING_INPUT)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertNotIn(ACTION_IMPLEMENTATION_PLAN_GENERATE, ids)
            self.assertFalse(out["eligibility_facts"]["implementation_plan_generate_eligible"])
            br = out["progression"]["implementation_plan"]["blocked_reason_codes"]
            self.assertIn(RC_IMPL_PLAN_BLOCKED_REFINEMENT_WAITING, br)

    def test_impl_plan_stale_vs_product_spec_fact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            ref = root / "runs" / "refinement"
            ip_sid = "ref_ip_20260101T000000Z_abcd1234"
            ps_sid = "ref_ps_20260202T000000Z_abcd1234"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": ps_sid,
                            "artifact_type": "product_spec",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "approved",
                            "current_round": 0,
                            "updated_at_utc": "2026-02-02T00:00:00+00:00",
                        },
                        {
                            "session_id": ip_sid,
                            "artifact_type": "implementation_plan",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "draft",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            _write(
                ref / ps_sid / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": ps_sid,
                    "artifact_type": "product_spec",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "approved",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-02-02T00:00:00+00:00",
                    "updated_at_utc": "2026-02-02T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(
                ref / ip_sid / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": ip_sid,
                    "artifact_type": "implementation_plan",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "draft",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {
                        "product_gap": {"angle_status": "active", "summary_lines": ["ok"]},
                        "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                    },
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertTrue(out["artifacts"]["implementation_plan"]["stale_vs_product_spec"])
            self.assertTrue(out["progression"]["implementation_plan"]["implementation_plan_stale_vs_product_spec"])
            self.assertNotIn(ACTION_IMPLEMENTATION_PLAN_GENERATE, {x["action_id"] for x in out["eligible_actions"]})

    def test_impl_plan_next_action_prioritized_over_partial_gap_escalation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": "ref_20260101T000000Z_abcd1234",
                            "artifact_type": "product_spec",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "approved",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / "ref_20260101T000000Z_abcd1234"
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": "ref_20260101T000000Z_abcd1234",
                    "artifact_type": "product_spec",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "approved",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {
                        "product_gap": {"angle_status": "partial", "summary_lines": ["ok"]},
                        "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                    },
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertIn(ACTION_ESCALATION_CONSIDER, {x["action_id"] for x in out["eligible_actions"]})
            self.assertIn(ACTION_IMPLEMENTATION_PLAN_GENERATE, {x["action_id"] for x in out["eligible_actions"]})
            self.assertEqual(out["next_action"], ACTION_IMPLEMENTATION_PLAN_GENERATE)
            impl_idx = next(
                i
                for i, x in enumerate(out["eligible_actions"])
                if x["action_id"] == ACTION_IMPLEMENTATION_PLAN_GENERATE
            )
            p = orchestration_advancement_payload(out, selected_at_utc="2026-01-01T00:00:00+00:00")
            self.assertEqual(p["selected_action"], ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(p["eligible_action_index"], impl_idx)

    def test_write_orchestration_task_impl_plan_when_spec_final(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": "ref_20260101T000000Z_abcd1234",
                            "artifact_type": "product_spec",
                            "source_id": "x",
                            "product_id": pid,
                            "status": "approved",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / "ref_20260101T000000Z_abcd1234"
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": "ref_20260101T000000Z_abcd1234",
                    "artifact_type": "product_spec",
                    "source_id": "x",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "approved",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {
                        "product_gap": {"angle_status": "active", "summary_lines": ["ok"]},
                        "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                    },
                },
            )
            write_orchestration_state(root, pid)
            tpath = orchestration_task_path(root, pid)
            self.assertTrue(tpath.is_file())
            task = json.loads(tpath.read_text(encoding="utf-8"))
            self.assertEqual(task["action"], ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(task["task_type"], task_type_for_action(ACTION_IMPLEMENTATION_PLAN_GENERATE))
            self.assertIn(RC_PRODUCT_SPEC_FINALIZED_NO_IMPL_PLAN, task["reason_codes"])

    def test_temporal_worst_stale_eligible_signals_collect(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "temporal" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.temporal_bundle.v1",
                    "product_id": pid,
                    "repo_root": str(root),
                    "collected_at_utc": now.isoformat(),
                    "source_signal_schema": "argus.signal_collection.v1",
                    "record_count": 0,
                    "worst_freshness_status": "stale",
                    "signals": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertTrue(out["eligibility_facts"]["temporal_freshness_stale"])
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_SIGNALS_COLLECT, ids)
            self.assertIn(ACTION_TEMPORAL_REFRESH, ids)
            sig = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_SIGNALS_COLLECT)
            self.assertIn(RC_TEMPORAL_WORST_STALE, sig["reason_codes"])
            tr = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_TEMPORAL_REFRESH)
            self.assertIn(RC_TEMPORAL_WORST_STALE, tr["reason_codes"])
            ordered = [x["action_id"] for x in out["eligible_actions"]]
            self.assertEqual(ordered[0], ACTION_TEMPORAL_REFRESH)
            self.assertLess(ordered.index(ACTION_TEMPORAL_REFRESH), ordered.index(ACTION_SIGNALS_COLLECT))
            self.assertEqual(out["next_action"], ACTION_TEMPORAL_REFRESH)
            rule = out["eligibility_facts"].get("eligible_actions_order_rule") or ""
            self.assertIn(ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS, rule)
            self.assertEqual(
                rule.split(ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR),
                [ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS],
            )

    def test_findings_generate_eligible_when_signals_fresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fg_elig"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_FINDINGS_GENERATE, ids)
            row = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_FINDINGS_GENERATE)
            self.assertIn(RC_FINDINGS_GENERATE_SIGNALS_READY, row["reason_codes"])
            self.assertTrue(out["eligibility_facts"]["findings_generate_eligible"])

    def test_findings_generate_not_eligible_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fg_stale"
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertNotIn(ACTION_FINDINGS_GENERATE, ids)
            self.assertFalse(out["eligibility_facts"]["findings_generate_eligible"])

    def test_decisions_generate_eligible_when_signals_fresh_and_findings_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dg_elig"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "findings" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.findings_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "repo_root": str(root),
                    "finding_count": 0,
                    "findings": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_DECISIONS_GENERATE, ids)
            row = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_DECISIONS_GENERATE)
            self.assertIn(RC_DECISIONS_GENERATE_FINDINGS_READY, row["reason_codes"])
            self.assertTrue(out["eligibility_facts"]["decisions_generate_eligible"])

    def test_escalation_packet_generate_eligible_with_findings_and_audit_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_epkg"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "findings" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.findings_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "repo_root": str(root),
                    "finding_count": 0,
                    "findings": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_ESCALATION_PACKET_GENERATE, ids)
            row = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_ESCALATION_PACKET_GENERATE)
            self.assertIn(RC_ESCALATION_PACKET_GENERATE_POSTURE, row["reason_codes"])
            self.assertTrue(out["eligibility_facts"]["escalation_packet_generate_eligible"])

    def test_escalation_packet_generate_not_eligible_without_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_epk_nf"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertNotIn(ACTION_ESCALATION_PACKET_GENERATE, ids)
            self.assertFalse(out["eligibility_facts"]["escalation_packet_generate_eligible"])

    def test_decisions_generate_not_eligible_without_findings_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dg_nf"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertNotIn(ACTION_DECISIONS_GENERATE, ids)
            self.assertFalse(out["eligibility_facts"]["decisions_generate_eligible"])

    def test_signals_time_stale_does_not_prioritize_temporal_refresh(self) -> None:
        """Stale signal collection requires ``signals_collect``; ``temporal_refresh`` is not eligible."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            _write(
                root / "runs" / "temporal" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.temporal_bundle.v1",
                    "product_id": pid,
                    "repo_root": str(root),
                    "collected_at_utc": "2026-01-01T00:00:00+00:00",
                    "source_signal_schema": "argus.signal_collection.v1",
                    "record_count": 0,
                    "worst_freshness_status": "stale",
                    "signals": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_SIGNALS_COLLECT, ids)
            self.assertNotIn(ACTION_TEMPORAL_REFRESH, ids)
            ordered = [x["action_id"] for x in out["eligible_actions"]]
            self.assertEqual(ordered[0], ACTION_SIGNALS_COLLECT)
            rule = out["eligibility_facts"].get("eligible_actions_order_rule") or ""
            self.assertNotIn(ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS, rule)

    def test_temporal_latest_absent_eligible_temporal_refresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_TEMPORAL_REFRESH, ids)
            tr = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_TEMPORAL_REFRESH)
            self.assertIn(RC_TEMPORAL_LATEST_ABSENT, tr["reason_codes"])

    def test_audit_product_gap_stub_eligible_audit_and_escalation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            now = datetime.now(timezone.utc)
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "records": [],
                },
            )
            _write(
                root / "runs" / "temporal" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.temporal_bundle.v1",
                    "product_id": pid,
                    "repo_root": str(root),
                    "collected_at_utc": now.isoformat(),
                    "source_signal_schema": "argus.signal_collection.v1",
                    "record_count": 0,
                    "worst_freshness_status": "fresh",
                    "signals": [],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertTrue(out["eligibility_facts"]["audit_product_gap_incomplete"])
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_AUDIT_RUN, ids)
            self.assertIn(ACTION_ESCALATION_CONSIDER, ids)
            audit = next(x for x in out["eligible_actions"] if x["action_id"] == ACTION_AUDIT_RUN)
            self.assertIn(RC_AUDIT_PRODUCT_GAP_INCOMPLETE, audit["reason_codes"])

    def test_blocked_waiting_when_reviews_missing_after_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            sid = "ref_20260101T000000Z_abcd1234"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": sid,
                            "artifact_type": "product_spec",
                            "product_id": pid,
                            "status": "in_review",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / sid
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": sid,
                    "artifact_type": "product_spec",
                    "source_id": "idea1",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "in_review",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(sd / "drafts" / "round_0.json", {"schema": "argus.artifact_draft.v1", "round_number": 0})
            out = evaluate_product_orchestration(root, pid)
            self.assertEqual(out["overall_status"], STATUS_BLOCKED_WAITING_INPUT)
            self.assertEqual(out["orchestration_status"], ORCH_STATUS_BLOCKED_WAITING_INPUT)
            self.assertEqual(out["artifacts"]["refinement"]["review_state"], REFINEMENT_REVIEW_AWAITING_REVIEW_INPUT)
            self.assertTrue(any(b.get("kind") == "refinement_cycle_incomplete" for b in out["blockers"]))
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_REFINEMENT_SUBMIT_REVIEWS_IN, ids)

    def test_rejected_session_escalation_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            sid = "ref_20260101T000000Z_abcd1234"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": sid,
                            "artifact_type": "product_spec",
                            "product_id": pid,
                            "status": "rejected",
                            "current_round": 1,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / sid
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": sid,
                    "artifact_type": "product_spec",
                    "source_id": "idea1",
                    "product_id": pid,
                    "current_round": 1,
                    "max_rounds": 4,
                    "status": "rejected",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertEqual(out["overall_status"], STATUS_BLOCKED_EXHAUSTED)
            self.assertEqual(out["orchestration_status"], ORCH_STATUS_ESCALATED)
            self.assertEqual(out["artifacts"]["refinement"]["review_state"], REFINEMENT_REVIEW_REJECTED)
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_ESCALATION_CONSIDER, ids)

    def test_not_converged_stuck_escalation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            sid = "ref_20260101T000000Z_abcd1234"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": sid,
                            "artifact_type": "product_spec",
                            "product_id": pid,
                            "status": "refining",
                            "current_round": 4,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / sid
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": sid,
                    "artifact_type": "product_spec",
                    "source_id": "idea1",
                    "product_id": pid,
                    "current_round": 4,
                    "max_rounds": 4,
                    "status": "refining",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(
                sd / "reviews" / "round_3.json",
                {
                    "reviews": [
                        {
                            "schema": "argus.stakeholder_review.v1",
                            "stakeholder_type": "product",
                            "verdict": "fail",
                            "blocking": True,
                            "council_mode": "grounded",
                            "confidence_score": 0.5,
                            "objections": ["gap"],
                        }
                    ]
                },
            )
            _write(
                sd / "convergence" / "round_3.json",
                {
                    "schema": "argus.refinement_convergence.v1",
                    "session_id": sid,
                    "round_number": 3,
                    "converged": False,
                    "final_status": "refining",
                    "reasons": ["continue_refining"],
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertEqual(out["artifacts"]["refinement"]["review_state"], REFINEMENT_REVIEW_NOT_CONVERGED_STUCK)
            self.assertTrue(any(b.get("kind") == "refinement_not_converged_stuck" for b in out["blockers"]))
            self.assertEqual(out["overall_status"], STATUS_BLOCKED_EXHAUSTED)
            self.assertEqual(out["orchestration_status"], ORCH_STATUS_ESCALATED)
            self.assertTrue(out["escalation_eligible"])
            codes = [t.get("code") for t in out["escalation_triggers"]]
            self.assertIn("refinement_not_converged_stuck", codes)
            self.assertIn(RC_CONVERGENCE_BLOCKING_GROUNDED, codes)

    def test_review_input_wait_exceeded_escalation_trigger(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            sid = "ref_20260101T000000Z_abcd1234"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": sid,
                            "artifact_type": "product_spec",
                            "product_id": pid,
                            "status": "in_review",
                            "current_round": 0,
                            "updated_at_utc": "2020-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / sid
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": sid,
                    "artifact_type": "product_spec",
                    "source_id": "idea1",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "in_review",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2020-01-01T00:00:00+00:00",
                    "updated_at_utc": "2020-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(sd / "drafts" / "round_0.json", {"schema": "argus.artifact_draft.v1", "round_number": 0})
            out = evaluate_product_orchestration(root, pid)
            self.assertTrue(any(t.get("code") == RC_REVIEW_INPUT_WAIT_EXCEEDED for t in out["escalation_triggers"]))
            ids = {x["action_id"] for x in out["eligible_actions"]}
            self.assertIn(ACTION_ESCALATION_CONSIDER, ids)

    def test_write_orchestration_creates_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "px"
            path = write_orchestration_state(root, pid)
            self.assertEqual(path, orchestration_latest_path(root, pid))
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["product_id"], pid)

    def test_write_orchestration_emits_task_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "px"
            write_orchestration_state(root, pid)
            tpath = orchestration_task_path(root, pid)
            self.assertTrue(tpath.is_file())
            task = json.loads(tpath.read_text(encoding="utf-8"))
            self.assertEqual(task["schema"], ORCHESTRATION_TASK_SCHEMA)
            self.assertEqual(task["product_id"], pid)
            self.assertEqual(task["artifact_type"], "orchestration_task")
            self.assertEqual(task["action"], ACTION_SIGNALS_COLLECT)
            self.assertEqual(task["task_type"], task_type_for_action(ACTION_SIGNALS_COLLECT))
            self.assertEqual(task["status"], "pending")
            self.assertIsInstance(task["reason_codes"], list)
            self.assertIsInstance(task["blockers"], list)
            self.assertEqual(task["source_state_ref"], f"runs/orchestration/latest/{pid}.json")
            self.assertIn("inputs", task)
            self.assertIn("dependencies", task)
            self.assertIn("artifact_paths", task["dependencies"])

    def test_task_artifact_removed_when_next_action_none(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "px"
            write_orchestration_state(root, pid)
            tpath = orchestration_task_path(root, pid)
            self.assertTrue(tpath.is_file())
            write_orchestration_state_payload(
                root,
                pid,
                {
                    "schema": ORCHESTRATION_STATE_SCHEMA,
                    "product_id": pid,
                    "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                    "orchestration_status": "complete",
                    "orchestration_status_reason": "no eligible actions",
                    "overall_status": "no_action",
                    "next_action": "none",
                    "eligible_actions": [],
                    "blockers": [],
                    "eligibility_facts": _EMPTY_ELIGIBILITY_FACTS,
                    "artifacts": {},
                },
            )
            self.assertFalse(tpath.is_file())

    def test_write_batch_creates_index(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            idx_path, rows = write_orchestration_states_batch(root, ["z1", "a2"])
            self.assertEqual(idx_path, orchestration_index_path(root))
            self.assertTrue(idx_path.is_file())
            idx = json.loads(idx_path.read_text(encoding="utf-8"))
            self.assertEqual(idx["schema"], ORCHESTRATION_INDEX_SCHEMA)
            self.assertEqual(len(idx["products"]), 2)
            self.assertEqual(len(rows), 2)
            ids = {x["product_id"] for x in idx["products"]}
            self.assertEqual(ids, {"a2", "z1"})

    def test_fleet_governance_rollup_written_on_batch(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_orchestration_states_batch(root, ["z1", "a2"])
            rollup_p = orchestration_fleet_governance_rollup_path(root)
            self.assertTrue(rollup_p.is_file())
            data = json.loads(rollup_p.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], ORCHESTRATION_FLEET_GOVERNANCE_ROLLUP_SCHEMA)
            self.assertEqual(len(data["products"]), 2)
            self.assertEqual([x["product_id"] for x in data["products"]], ["a2", "z1"])
            self.assertEqual(
                data["artifact_links"]["fleet_governance_rollup_repo_relative"],
                "runs/orchestration/latest/fleet_governance_rollup.json",
            )
            self.assertEqual(
                data["artifact_links"]["orchestration_index_repo_relative"],
                "runs/orchestration/latest/index.json",
            )
            for ent in data["products"]:
                self.assertIn("phase2_posture", ent)
                self.assertIn("chain_furthest_ready", ent["phase2_posture"])
                self.assertIn("experiment_posture", ent)
                self.assertIn("experiments_propose_eligible", ent["experiment_posture"])

    def test_fleet_governance_rollup_deterministic_synthetic_payload(self) -> None:
        prior = {
            "ranked_product_ids": ["a", "b"],
            "entries": [
                {
                    "product_id": "a",
                    "priority_rank": 0,
                    "priority_tuple": [0, 0, 0, 1, 0],
                    "priority_labels": {"has_eligible_actions": True},
                },
                {
                    "product_id": "b",
                    "priority_rank": 1,
                    "priority_tuple": [0, 0, 0, 0, 0],
                    "priority_labels": {"has_eligible_actions": False},
                },
            ],
        }
        by_pid = {
            "a": {
                "orchestration_status": "eligible",
                "orchestration_status_reason": "x",
                "orchestration_status_reason_codes": ["z", "a"],
                "overall_status": "eligible",
                "next_action": "signals_collect",
                "eligible_actions": [{"action_id": "signals_collect", "reason": "r", "reason_codes": []}],
                "escalation_eligible": False,
                "escalation_triggers": [],
                "eligibility_facts": {
                    "findings_generate_eligible": False,
                    "decisions_generate_eligible": False,
                    "ideas_generate_eligible": False,
                    "escalation_packet_generate_eligible": False,
                    "signals_refresh_needed": True,
                    "signals_collection_time_stale": True,
                },
                "orchestration_posture": {
                    "execution_feedback_crosswalk": {
                        "next_action_matches_recent_failed_execution": False,
                        "next_action_matches_recent_queued_unhandled": False,
                    },
                    "retry_reopened": {"active": False, "action_ids": []},
                    "repeat_execution_feedback_thresholds": {"failures": [], "queued_unhandled": []},
                },
                "artifacts": {"refinement": {"review_state": "absent"}},
            },
            "b": {
                "orchestration_status": "complete",
                "orchestration_status_reason": "done",
                "orchestration_status_reason_codes": [],
                "overall_status": "no_action",
                "next_action": "none",
                "eligible_actions": [],
                "escalation_eligible": False,
                "escalation_triggers": [],
                "eligibility_facts": {
                    "findings_generate_eligible": False,
                    "decisions_generate_eligible": False,
                    "ideas_generate_eligible": False,
                    "escalation_packet_generate_eligible": False,
                    "signals_refresh_needed": False,
                    "signals_collection_time_stale": False,
                },
                "orchestration_posture": {},
                "artifacts": {},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p1 = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["a", "b"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
            p2 = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["a", "b"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
            self.assertEqual(p1, p2)
            self.assertEqual(p1["products"][0]["eligible_action_ids"], ["signals_collect"])
            self.assertEqual(p1["products"][0]["orchestration_status_reason_codes"], ["a", "z"])
            p2a = p1["products"][0]["phase2_posture"]
            self.assertFalse(p2a["findings_generate_eligible"])
            self.assertFalse(p2a["decisions_generate_eligible"])
            self.assertFalse(p2a["ideas_generate_eligible"])
            self.assertFalse(p2a["escalation_packet_generate_eligible"])
            self.assertEqual(p2a["chain_furthest_ready"], "observe_refresh")
            self.assertEqual(
                p2a["artifact_links"],
                {
                    "signals_latest_repo_relative": None,
                    "findings_latest_repo_relative": None,
                    "decisions_latest_repo_relative": None,
                    "ideas_latest_repo_relative": None,
                    "escalation_packet_latest_repo_relative": None,
                },
            )
            p2b = p1["products"][1]["phase2_posture"]
            self.assertEqual(p2b["chain_furthest_ready"], "none")
            exp_a = p1["products"][0]["experiment_posture"]
            self.assertFalse(exp_a["experiments_propose_eligible"])
            self.assertFalse(exp_a["experiment_proposals_present"])
            self.assertIsNone(exp_a["artifact_links"]["experiments_latest_repo_relative"])
            self.assertNotIn("proposal_count", exp_a)
            exp_b = p1["products"][1]["experiment_posture"]
            self.assertFalse(exp_b["experiments_propose_eligible"])
            self.assertFalse(exp_b["experiment_proposals_present"])

    def test_fleet_governance_rollup_experiment_posture_synthetic(self) -> None:
        """Per-product experiment posture: eligibility facts, fallback to eligible ids, artifact file."""
        prior = {
            "ranked_product_ids": ["p_elig", "p_file", "p_fallback"],
            "entries": [
                {"product_id": "p_elig", "priority_rank": 0, "priority_tuple": [], "priority_labels": {}},
                {"product_id": "p_file", "priority_rank": 1, "priority_tuple": [], "priority_labels": {}},
                {"product_id": "p_fallback", "priority_rank": 2, "priority_tuple": [], "priority_labels": {}},
            ],
        }
        base = {
            "orchestration_status": "eligible",
            "orchestration_status_reason": "",
            "orchestration_status_reason_codes": [],
            "overall_status": "eligible",
            "escalation_eligible": False,
            "escalation_triggers": [],
            "orchestration_posture": {},
            "artifacts": {},
        }
        by_pid = {
            "p_elig": {
                **base,
                "next_action": "experiments_propose",
                "eligible_actions": [
                    {"action_id": "experiments_propose", "reason": "ready", "reason_codes": []},
                ],
                "eligibility_facts": {"experiments_propose_eligible": True},
            },
            "p_file": {
                **base,
                "next_action": "none",
                "eligible_actions": [],
                "eligibility_facts": {"experiments_propose_eligible": False},
            },
            "p_fallback": {
                **base,
                "next_action": "experiments_propose",
                "eligible_actions": [
                    {"action_id": "experiments_propose", "reason": "x", "reason_codes": []},
                ],
                "eligibility_facts": _EMPTY_ELIGIBILITY_FACTS,
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prop_p = (
                root
                / "runs"
                / "experiments"
                / "proposals"
                / "latest"
                / "p_file.json"
            )
            _write(
                prop_p,
                {
                    "schema": "argus.experiment_proposals_run.v1",
                    "generated_at_utc": "2026-01-01T00:00:00+00:00",
                    "repo_root": str(root),
                    "proposals": [{"x": 1}, {"x": 2}],
                },
            )
            out = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["p_elig", "p_file", "p_fallback"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
        e, f, fb = out["products"]
        self.assertTrue(e["experiment_posture"]["experiments_propose_eligible"])
        self.assertFalse(e["experiment_posture"]["experiment_proposals_present"])
        self.assertIsNone(e["experiment_posture"]["artifact_links"]["experiments_latest_repo_relative"])

        self.assertFalse(f["experiment_posture"]["experiments_propose_eligible"])
        self.assertTrue(f["experiment_posture"]["experiment_proposals_present"])
        self.assertEqual(f["experiment_posture"]["proposal_count"], 2)
        self.assertEqual(
            f["experiment_posture"]["artifact_links"]["experiments_latest_repo_relative"],
            "runs/experiments/proposals/latest/p_file.json",
        )

        self.assertTrue(fb["experiment_posture"]["experiments_propose_eligible"])
        self.assertFalse(fb["experiment_posture"]["experiment_proposals_present"])

    def test_fleet_governance_rollup_idea_refinement_posture_synthetic(self) -> None:
        """Idea corridor stages surface distinct ``idea_refinement_posture`` per product."""
        sid = "ref_idea_sess_1"
        prior = {
            "ranked_product_ids": ["p_ready", "p_wait", "p_run"],
            "entries": [
                {"product_id": "p_ready", "priority_rank": 0, "priority_tuple": [], "priority_labels": {}},
                {"product_id": "p_wait", "priority_rank": 1, "priority_tuple": [], "priority_labels": {}},
                {"product_id": "p_run", "priority_rank": 2, "priority_tuple": [], "priority_labels": {}},
            ],
        }
        base = {
            "orchestration_status": "eligible",
            "orchestration_status_reason": "",
            "orchestration_status_reason_codes": [],
            "overall_status": "eligible",
            "escalation_eligible": False,
            "escalation_triggers": [],
            "orchestration_posture": {},
        }
        by_pid = {
            "p_ready": {
                **base,
                "next_action": "refinement_start_idea",
                "eligible_actions": [
                    {"action_id": "refinement_start_idea", "reason": "x", "reason_codes": []},
                ],
                "eligibility_facts": {
                    "refinement_start_idea_eligible": True,
                    "idea_refinement_session_present_non_terminal": False,
                    "refinement_submit_reviews_in_eligible": False,
                    "refinement_run_eligible_for_idea_session": False,
                },
                "artifacts": {
                    "refinement": {
                        "review_state": "absent",
                        "idea": {"phase": "absent", "session_id": None, "status": None, "current_round": None},
                    },
                },
            },
            "p_wait": {
                **base,
                "next_action": "refinement_submit_reviews_in",
                "eligible_actions": [
                    {
                        "action_id": "refinement_submit_reviews_in",
                        "reason": "reviews_in missing",
                        "reason_codes": [],
                    },
                ],
                "eligibility_facts": {
                    "refinement_start_idea_eligible": False,
                    "idea_refinement_session_present_non_terminal": True,
                    "refinement_submit_reviews_in_eligible": True,
                    "refinement_run_eligible_for_idea_session": False,
                },
                "artifacts": {
                    "refinement": {
                        "review_state": "awaiting_review_input",
                        "idea": {
                            "phase": "in_progress",
                            "session_id": sid,
                            "status": "in_review",
                            "current_round": 0,
                        },
                    },
                },
            },
            "p_run": {
                **base,
                "next_action": "refinement_run",
                "eligible_actions": [
                    {
                        "action_id": "refinement_run",
                        "reason": f"session {sid} may accept `argus refine run`",
                        "reason_codes": [],
                    },
                ],
                "eligibility_facts": {
                    "refinement_start_idea_eligible": False,
                    "idea_refinement_session_present_non_terminal": True,
                    "refinement_submit_reviews_in_eligible": False,
                    "refinement_run_eligible_for_idea_session": True,
                },
                "artifacts": {
                    "refinement": {
                        "review_state": "in_progress",
                        "idea": {
                            "phase": "in_progress",
                            "session_id": sid,
                            "status": "draft",
                            "current_round": 0,
                        },
                    },
                },
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["p_ready", "p_wait", "p_run"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
        r, w, rrun = out["products"]
        self.assertTrue(r["idea_refinement_posture"]["refinement_start_idea_eligible"])
        self.assertFalse(r["idea_refinement_posture"]["idea_refinement_session_present_non_terminal"])
        self.assertIsNone(r["idea_refinement_posture"]["idea_session"])
        self.assertTrue(w["idea_refinement_posture"]["next_action_is_refinement_submit_reviews_in"])
        self.assertTrue(w["idea_refinement_posture"]["refinement_submit_reviews_in_eligible"])
        self.assertEqual(w["idea_refinement_posture"]["idea_session"]["session_id"], sid)
        self.assertTrue(rrun["idea_refinement_posture"]["refinement_run_eligible_for_idea_session"])
        self.assertEqual(rrun["idea_refinement_posture"]["idea_session"]["session_id"], sid)

    def test_eligibility_includes_idea_refinement_fleet_facts_and_artifact_idea(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            out = evaluate_product_orchestration(root, pid)
            facts = out.get("eligibility_facts") or {}
            self.assertIn("idea_refinement_session_present_non_terminal", facts)
            self.assertIn("refinement_submit_reviews_in_eligible", facts)
            self.assertIn("refinement_run_eligible_for_idea_session", facts)
            self.assertIn("idea", out["artifacts"]["refinement"])

    def test_operator_summary_includes_idea_refinement_posture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_op"
            _minimal_product(root, pid)
            _write(
                orchestration_latest_path(root, pid),
                {
                    "schema": ORCHESTRATION_STATE_SCHEMA,
                    "product_id": pid,
                    "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "orchestration_status_reason": "x",
                    "orchestration_status_reason_codes": [],
                    "overall_status": STATUS_ELIGIBLE,
                    "next_action": "refinement_start_idea",
                    "eligible_actions": [],
                    "eligibility_facts": {
                        "refinement_start_idea_eligible": True,
                        "idea_refinement_session_present_non_terminal": False,
                        "refinement_submit_reviews_in_eligible": False,
                        "refinement_run_eligible_for_idea_session": False,
                    },
                    "artifacts": {
                        "refinement": {
                            "review_state": REFINEMENT_REVIEW_ABSENT,
                            "idea": {
                                "phase": "absent",
                                "session_id": None,
                                "status": None,
                                "current_round": None,
                            },
                        },
                    },
                    "waiting_inputs": [],
                    "escalation_eligible": False,
                    "escalation_triggers": [],
                    "blockers": [],
                },
            )
            batch = {
                "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                "selected_product_id": pid,
                "advancement_payload": {
                    "selected_action": None,
                    "snapshot_next_action": "refinement_start_idea",
                },
            }
            p = build_operator_summary_payload(root, batch)
        self.assertIsNotNone(p.get("idea_refinement_posture"))
        self.assertTrue(p["idea_refinement_posture"]["refinement_start_idea_eligible"])
        self.assertIn("idea_refinement: refinement_start_idea_eligible", p["compact_summary_lines"])

    def test_operator_summary_includes_experiment_posture(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp"
            _minimal_product(root, pid)
            prop_p = (
                root
                / "runs"
                / "experiments"
                / "proposals"
                / "latest"
                / f"{pid}.json"
            )
            _write(
                prop_p,
                {
                    "schema": "argus.experiment_proposals_run.v1",
                    "generated_at_utc": "2026-01-01T00:00:00+00:00",
                    "repo_root": str(root),
                    "proposals": [],
                },
            )
            _write(
                orchestration_latest_path(root, pid),
                {
                    "schema": ORCHESTRATION_STATE_SCHEMA,
                    "product_id": pid,
                    "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "orchestration_status_reason": "x",
                    "orchestration_status_reason_codes": [],
                    "overall_status": STATUS_ELIGIBLE,
                    "next_action": ACTION_EXPERIMENTS_PROPOSE,
                    "eligible_actions": [
                        {
                            "action_id": ACTION_EXPERIMENTS_PROPOSE,
                            "reason": "ready",
                            "reason_codes": [],
                        },
                    ],
                    "eligibility_facts": {"experiments_propose_eligible": True},
                    "artifacts": {
                        "refinement": {
                            "review_state": REFINEMENT_REVIEW_ABSENT,
                            "idea": {
                                "phase": "absent",
                                "session_id": None,
                                "status": None,
                                "current_round": None,
                            },
                        },
                    },
                    "waiting_inputs": [],
                    "escalation_eligible": False,
                    "escalation_triggers": [],
                    "blockers": [],
                },
            )
            batch = {
                "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                "selected_product_id": pid,
                "advancement_payload": {
                    "selected_action": None,
                    "snapshot_next_action": ACTION_EXPERIMENTS_PROPOSE,
                },
            }
            p = build_operator_summary_payload(root, batch)
        self.assertIsNotNone(p.get("experiment_posture"))
        self.assertTrue(p["experiment_posture"]["experiments_propose_eligible"])
        self.assertTrue(p["experiment_posture"]["experiment_proposals_present"])
        self.assertEqual(p["experiment_posture"]["proposal_count"], 0)
        self.assertIn("experiments: propose_ready", p["compact_summary_lines"])
        self.assertIn("experiments: proposals_present", p["compact_summary_lines"])

    def test_fleet_governance_rollup_phase2_posture_chain_stages(self) -> None:
        """Rollup ``chain_furthest_ready`` reflects observe vs interpret vs ideas vs govern from evaluated state."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            now = datetime.now(timezone.utc).isoformat()
            for pid in ("p_obs", "p_int", "p_gov"):
                _minimal_product(root, pid)

            _write(
                root / "runs" / "signals" / "latest" / "p_obs.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": "p_obs",
                    "collected_at_utc": "2020-01-01T00:00:00+00:00",
                    "records": [],
                },
            )
            _write(
                root / "runs" / "signals" / "latest" / "p_int.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": "p_int",
                    "collected_at_utc": now,
                    "records": [],
                },
            )
            _write(
                root / "runs" / "signals" / "latest" / "p_gov.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": "p_gov",
                    "collected_at_utc": now,
                    "records": [],
                },
            )
            _write(
                root / "runs" / "findings" / "latest" / "p_gov.json",
                {
                    "schema": "argus.findings_bundle.v1",
                    "product_id": "p_gov",
                    "generated_at_utc": now,
                    "repo_root": str(root),
                    "finding_count": 0,
                    "findings": [],
                },
            )
            _write(
                root / "runs" / "decisions" / "latest" / "p_gov.json",
                {
                    "schema": "argus.decisions_bundle.v1",
                    "product_id": "p_gov",
                    "generated_at_utc": now,
                    "repo_root": str(root),
                    "decisions": [],
                },
            )
            _write(
                root / "runs" / "audit" / "p_gov" / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": "p_gov",
                    "generated_at_utc": now,
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            _write(
                root / "runs" / "escalations" / "latest" / "esc_20260101T000000Z_p_gov.json",
                {
                    "schema": "argus.escalation_packet.v1",
                    "packet_id": "esc_20260101T000000Z_p_gov",
                    "product_id": "p_gov",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "triggering_rules": ["x"],
                },
            )
            _write(
                root / "runs" / "escalations" / "latest" / "esc_20260601T000000Z_p_gov.json",
                {
                    "schema": "argus.escalation_packet.v1",
                    "packet_id": "esc_20260601T000000Z_p_gov",
                    "product_id": "p_gov",
                    "created_at": "2026-06-01T00:00:00+00:00",
                    "triggering_rules": ["x"],
                },
            )
            _write(
                root / "runs" / "ideas" / "latest.json",
                {
                    "schema": "argus.ideas_bundle.v1",
                    "product_id": "p_gov",
                    "generated_at_utc": now,
                    "repo_root": str(root),
                    "idea_count": 0,
                    "ideas": [],
                },
            )

            write_orchestration_states_batch(root, ["p_gov", "p_int", "p_obs"])
            data = json.loads(orchestration_fleet_governance_rollup_path(root).read_text(encoding="utf-8"))
            by_pid = {x["product_id"]: x["phase2_posture"] for x in data["products"]}
            self.assertEqual(by_pid["p_obs"]["chain_furthest_ready"], "observe_refresh")
            self.assertTrue(by_pid["p_int"]["findings_generate_eligible"])
            self.assertEqual(by_pid["p_int"]["chain_furthest_ready"], "interpret")
            self.assertEqual(by_pid["p_gov"]["chain_furthest_ready"], "govern")
            self.assertTrue(by_pid["p_gov"]["escalation_packet_generate_eligible"])
            self.assertTrue(by_pid["p_gov"]["ideas_generate_eligible"])
            gl = by_pid["p_gov"]["artifact_links"]
            self.assertEqual(
                gl["signals_latest_repo_relative"],
                "runs/signals/latest/p_gov.json",
            )
            self.assertEqual(
                gl["findings_latest_repo_relative"],
                "runs/findings/latest/p_gov.json",
            )
            self.assertEqual(
                gl["decisions_latest_repo_relative"],
                "runs/decisions/latest/p_gov.json",
            )
            self.assertEqual(gl["ideas_latest_repo_relative"], "runs/ideas/latest.json")
            self.assertEqual(
                gl["escalation_packet_latest_repo_relative"],
                "runs/escalations/latest/esc_20260601T000000Z_p_gov.json",
            )
            self.assertIsNone(by_pid["p_int"]["artifact_links"]["escalation_packet_latest_repo_relative"])
            self.assertIsNone(by_pid["p_int"]["artifact_links"]["ideas_latest_repo_relative"])

    def test_fleet_governance_rollup_synthetic_chain_furthest_ready_ideas_stage(self) -> None:
        """``ideas`` headline when ideas_generate is eligible but escalation packet is not."""
        prior = {
            "ranked_product_ids": ["x"],
            "entries": [
                {
                    "product_id": "x",
                    "priority_rank": 0,
                    "priority_tuple": [0, 0, 0, 1, 0],
                    "priority_labels": {},
                },
            ],
        }
        by_pid = {
            "x": {
                "orchestration_status": "eligible",
                "orchestration_status_reason": "",
                "orchestration_status_reason_codes": [],
                "overall_status": "eligible",
                "next_action": "ideas_generate",
                "eligible_actions": [{"action_id": "ideas_generate", "reason": "r", "reason_codes": []}],
                "escalation_eligible": False,
                "escalation_triggers": [],
                "eligibility_facts": {
                    "findings_generate_eligible": True,
                    "decisions_generate_eligible": True,
                    "ideas_generate_eligible": True,
                    "escalation_packet_generate_eligible": False,
                    "signals_refresh_needed": False,
                    "signals_collection_time_stale": False,
                },
                "orchestration_posture": {},
                "artifacts": {},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["x"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
            p2 = out["products"][0]["phase2_posture"]
            self.assertTrue(p2["ideas_generate_eligible"])
            self.assertEqual(p2["chain_furthest_ready"], "ideas")

    def test_fleet_governance_rollup_synthetic_chain_furthest_ready_decide_without_ideas(self) -> None:
        """``decide`` when decisions are eligible but ideas gate is false (stable vs ``ideas``)."""
        prior = {
            "ranked_product_ids": ["y"],
            "entries": [
                {
                    "product_id": "y",
                    "priority_rank": 0,
                    "priority_tuple": [0, 0, 0, 1, 0],
                    "priority_labels": {},
                },
            ],
        }
        by_pid = {
            "y": {
                "orchestration_status": "eligible",
                "orchestration_status_reason": "",
                "orchestration_status_reason_codes": [],
                "overall_status": "eligible",
                "next_action": "decisions_generate",
                "eligible_actions": [{"action_id": "decisions_generate", "reason": "r", "reason_codes": []}],
                "escalation_eligible": False,
                "escalation_triggers": [],
                "eligibility_facts": {
                    "findings_generate_eligible": True,
                    "decisions_generate_eligible": True,
                    "ideas_generate_eligible": False,
                    "escalation_packet_generate_eligible": False,
                    "signals_refresh_needed": False,
                    "signals_collection_time_stale": False,
                },
                "orchestration_posture": {},
                "artifacts": {},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["y"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
            p2 = out["products"][0]["phase2_posture"]
            self.assertFalse(p2["ideas_generate_eligible"])
            self.assertEqual(p2["chain_furthest_ready"], "decide")

    def test_fleet_governance_rollup_synthetic_chain_govern_overrides_ideas(self) -> None:
        """``govern`` wins when both ideas and escalation packet are eligible."""
        prior = {
            "ranked_product_ids": ["z"],
            "entries": [
                {
                    "product_id": "z",
                    "priority_rank": 0,
                    "priority_tuple": [0, 0, 0, 1, 0],
                    "priority_labels": {},
                },
            ],
        }
        by_pid = {
            "z": {
                "orchestration_status": "eligible",
                "orchestration_status_reason": "",
                "orchestration_status_reason_codes": [],
                "overall_status": "eligible",
                "next_action": "escalation_packet_generate",
                "eligible_actions": [
                    {"action_id": "ideas_generate", "reason": "i", "reason_codes": []},
                    {"action_id": "escalation_packet_generate", "reason": "e", "reason_codes": []},
                ],
                "escalation_eligible": True,
                "escalation_triggers": [],
                "eligibility_facts": {
                    "findings_generate_eligible": True,
                    "decisions_generate_eligible": True,
                    "ideas_generate_eligible": True,
                    "escalation_packet_generate_eligible": True,
                    "signals_refresh_needed": False,
                    "signals_collection_time_stale": False,
                },
                "orchestration_posture": {},
                "artifacts": {},
            },
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = build_fleet_governance_rollup_payload(
                root,
                evaluated_at_utc="2026-01-01T00:00:00+00:00",
                product_ids=["z"],
                by_pid=by_pid,
                cross_product_prioritization=prior,
            )
            p2 = out["products"][0]["phase2_posture"]
            self.assertTrue(p2["ideas_generate_eligible"])
            self.assertTrue(p2["escalation_packet_generate_eligible"])
            self.assertEqual(p2["chain_furthest_ready"], "govern")

    def test_fleet_governance_rollup_optional_artifact_links(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "solo"
            adv = orchestration_advancement_path(root, pid)
            adv.parent.mkdir(parents=True, exist_ok=True)
            adv.write_text(
                dumps_json({"schema": ORCHESTRATION_ADVANCEMENT_SCHEMA, "product_id": pid}) + "\n",
                encoding="utf-8",
            )
            write_orchestration_states_batch(root, [pid])
            data = json.loads(orchestration_fleet_governance_rollup_path(root).read_text(encoding="utf-8"))
            ent = data["products"][0]["artifact_links"]
            self.assertEqual(ent["orchestration_state_repo_relative"], f"runs/orchestration/latest/{pid}.json")
            self.assertEqual(ent["advancement_repo_relative"], f"runs/orchestration/latest/advancements/{pid}.json")
            self.assertIsNone(ent.get("escalation_task_state_repo_relative"))
            self.assertIsNone(ent.get("progression_run_repo_relative"))

    def test_advancement_payload_skipped_when_complete_no_eligible(self) -> None:
        p = orchestration_advancement_payload(
            {
                "product_id": "p",
                "orchestration_status": ORCH_STATUS_COMPLETE,
                "eligible_actions": [],
                "next_action": "none",
                "eligibility_facts": {"k": True},
            },
            selected_at_utc="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(p["schema"], ORCHESTRATION_ADVANCEMENT_SCHEMA)
        self.assertEqual(p["action_status"], ACTION_STATUS_SKIPPED)
        self.assertIsNone(p["selected_action"])
        self.assertEqual(p["source_eligibility_facts"], {"k": True})

    def test_advancement_payload_waiting_input_queues_refinement_submit_reviews_in(self) -> None:
        p = orchestration_advancement_payload(
            {
                "product_id": "p",
                "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                "eligible_actions": [{"action_id": ACTION_REFINEMENT_SUBMIT_REVIEWS_IN, "reason": "x"}],
                "next_action": ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
                "eligibility_facts": _EMPTY_ELIGIBILITY_FACTS,
            },
            selected_at_utc="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(p["action_status"], ACTION_STATUS_QUEUED)
        self.assertEqual(p["selected_action"], ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)

    def test_advancement_payload_blocked_waiting_input_without_submit_next(self) -> None:
        p = orchestration_advancement_payload(
            {
                "product_id": "p",
                "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                "eligible_actions": [{"action_id": ACTION_SIGNALS_COLLECT, "reason": "x"}],
                "next_action": "none",
                "eligibility_facts": _EMPTY_ELIGIBILITY_FACTS,
            },
            selected_at_utc="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(p["action_status"], ACTION_STATUS_BLOCKED)
        self.assertEqual(p["selected_action"], ACTION_SIGNALS_COLLECT)

    def test_advancement_payload_blocked_waiting_approval(self) -> None:
        p = orchestration_advancement_payload(
            {
                "product_id": "p",
                "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
                "eligible_actions": [{"action_id": ACTION_SIGNALS_COLLECT, "reason": "x"}],
                "next_action": "none",
                "eligibility_facts": _EMPTY_ELIGIBILITY_FACTS,
            },
            selected_at_utc="2026-01-01T00:00:00+00:00",
        )
        self.assertEqual(p["action_status"], ACTION_STATUS_BLOCKED)
        self.assertIn("blocked_waiting_approval", p["transition_reason"])

    def test_waiting_inputs_signals_absent_and_reason_codes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            out = evaluate_product_orchestration(root, pid)
            kinds = [x["kind"] for x in out["waiting_inputs"]]
            self.assertIn(WAITING_KIND_OBSERVABILITY_SIGNALS, kinds)
            self.assertIn(RC_SIGNALS_BUNDLE_ABSENT, out["orchestration_status_reason_codes"])
            wi = next(x for x in out["waiting_inputs"] if x["kind"] == WAITING_KIND_OBSERVABILITY_SIGNALS)
            self.assertIn("expected_paths", wi)

    def test_blocked_waiting_approval_over_stale_refresh(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            appr = root / "runs" / "approval" / "records"
            appr.mkdir(parents=True)
            _write(
                appr / "appr_20260101T000000Z_deadbeef.json",
                {
                    "schema": "argus.approval.v1",
                    "approval_id": "appr_20260101T000000Z_deadbeef",
                    "action_id": "run",
                    "product_id": pid,
                    "status": "pending",
                    "reason": "",
                    "created_at": "2026-01-01T00:00:00+00:00",
                    "decided_at": None,
                    "metadata": {},
                },
            )
            out = evaluate_product_orchestration(root, pid)
            self.assertEqual(out["orchestration_status"], ORCH_STATUS_BLOCKED_WAITING_APPROVAL)
            self.assertEqual(len(out["artifacts"]["execution"]["pending_approvals"]), 1)
            kinds = [x["kind"] for x in out["waiting_inputs"]]
            self.assertIn(WAITING_KIND_EXECUTION_APPROVAL, kinds)
            self.assertIn(RC_APPROVAL_PENDING, out["orchestration_status_reason_codes"])

    def test_waiting_inputs_refinement_expected_paths(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            sid = "ref_20260101T000000Z_abcd1234"
            ref = root / "runs" / "refinement"
            _write(
                ref / "index.json",
                {
                    "schema": "argus.refinement_index.v1",
                    "sessions": [
                        {
                            "session_id": sid,
                            "artifact_type": "product_spec",
                            "product_id": pid,
                            "status": "in_review",
                            "current_round": 0,
                            "updated_at_utc": "2026-01-01T00:00:00+00:00",
                        },
                    ],
                },
            )
            sd = ref / sid
            _write(
                sd / "session.json",
                {
                    "schema": "argus.refinement_session.v1",
                    "session_id": sid,
                    "artifact_type": "product_spec",
                    "source_id": "idea1",
                    "product_id": pid,
                    "current_round": 0,
                    "max_rounds": 4,
                    "status": "in_review",
                    "required_stakeholders": ["product"],
                    "optional_stakeholders": [],
                    "created_at_utc": "2026-01-01T00:00:00+00:00",
                    "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    "meta": {},
                },
            )
            _write(sd / "drafts" / "round_0.json", {"schema": "argus.artifact_draft.v1", "round_number": 0})
            out = evaluate_product_orchestration(root, pid)
            w = next(x for x in out["waiting_inputs"] if x["kind"] == WAITING_KIND_REFINEMENT_GROUNDED_INPUT)
            self.assertTrue(any("reviews_in" in str(p) for p in w.get("expected_paths") or []))

    def test_advancement_payload_queued_first_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = evaluate_product_orchestration(root, "p1")
            p = orchestration_advancement_payload(out, selected_at_utc="2026-01-01T00:00:00+00:00")
            self.assertEqual(p["action_status"], ACTION_STATUS_QUEUED)
            self.assertEqual(p["selected_action"], out["next_action"])
            na_idx = next(
                (i for i, x in enumerate(out["eligible_actions"]) if x["action_id"] == out["next_action"]),
                0,
            )
            self.assertEqual(p["eligible_action_index"], na_idx)

    def test_advance_orchestration_writes_advancement_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "px"
            path, payload = advance_orchestration(root, pid, refresh_state=False)
            self.assertEqual(path, orchestration_advancement_path(root, pid))
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], ORCHESTRATION_ADVANCEMENT_SCHEMA)
            self.assertEqual(data["action_status"], payload["action_status"])
            self.assertIn("source_eligibility_facts", data)


if __name__ == "__main__":
    unittest.main()
