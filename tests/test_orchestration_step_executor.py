"""In-process orchestration step executor and ``advance --execute``."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from argus.core.serialize import dumps_json
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import save_experiment
from argus.orchestrator.advancement import advance_orchestration
from argus.orchestrator.eligibility import (
    ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR,
    ELIGIBLE_ORDER_RULE_EOA_AFTER_IMPL,
    ELIGIBLE_ORDER_RULE_EOA_FIRST,
    ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS,
    evaluate_product_orchestration,
)
from argus.orchestrator.execution_feedback import load_orchestration_feedback_summary
from argus.orchestrator.refinement_idea_pick import ORCHESTRATION_IDEA_SELECTION_RULE
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
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ACTION_TEMPORAL_REFRESH,
    ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA,
    ORCHESTRATION_TASK_SCHEMA,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from argus.orchestrator.task_artifact import orchestration_task_path, task_type_for_action
from argus.refinement.models import ArtifactType
from argus.refinement.session import create_session


def _orch_fresh_now() -> datetime:
    """UTC instant within ``SIGNAL_STALE_HOURS`` of real time — fixed calendar dates go stale over time."""
    return datetime.now(timezone.utc) - timedelta(hours=1)


def _minimal_product(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: OrchExecTest
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
        ("start.sh", "#!/bin/sh\necho start\n" + "x" * 50),
        ("stop.sh", "#!/bin/sh\necho stop\n" + "y" * 50),
        ("analyze.sh", "#!/bin/sh\necho analyze\n" + "z" * 50),
    ):
        (pr / "scripts" / name).write_text(body, encoding="utf-8")
    (pr / "metrics").mkdir(exist_ok=True)
    (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")
    # Phase 1: allow in-process orchestration (tests expect handlers to run by default).
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


def _idea_row(
    idea_id: str,
    title: str,
    *,
    expected_value: float = 0.5,
    product_id: str | None = None,
) -> dict:
    return {
        "idea_id": idea_id,
        "title": title,
        "description": "d",
        "type": "explore",
        "source": "synthesis",
        "novelty_score": 0.5,
        "adjacency_score": 0.5,
        "expected_value_score": expected_value,
        "confidence_score": 0.5,
        "cost_estimate": "",
        "channel_type": "hybrid",
        "monetization_type": "hybrid",
        "rationale": "",
        "diversity_impact_score": 0.0,
        "product_id": product_id,
    }


def _write_ideas_latest_bundle(root: Path, pid: str, ideas_rows: list[dict], *, now_iso: str) -> None:
    p = root / "runs" / "ideas" / "latest.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        dumps_json(
            {
                "schema": "argus.ideas_bundle.v1",
                "product_id": pid,
                "generated_at_utc": now_iso,
                "repo_root": str(root),
                "idea_count": len(ideas_rows),
                "ideas": ideas_rows,
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _write_impl_plan_generate_eligible_fixture(root: Path, pid: str) -> None:
    """Matches ``test_product_spec_finalized_impl_plan_eligible`` — orchestration allows impl plan start."""
    _minimal_product(root, pid)
    ref = root / "runs" / "refinement"
    (ref / "ref_20260101T000000Z_abcd1234").mkdir(parents=True, exist_ok=True)
    (ref / "ref_20260101T000000Z_abcd1234" / "drafts").mkdir(exist_ok=True)
    (ref / "ref_20260101T000000Z_abcd1234" / "reviews").mkdir(exist_ok=True)
    (ref / "ref_20260101T000000Z_abcd1234" / "reviews_in").mkdir(exist_ok=True)
    (ref / "ref_20260101T000000Z_abcd1234" / "synthesis").mkdir(exist_ok=True)
    (ref / "ref_20260101T000000Z_abcd1234" / "convergence").mkdir(exist_ok=True)
    (ref / "ref_20260101T000000Z_abcd1234" / "outcomes").mkdir(exist_ok=True)
    (ref / "index.json").write_text(
        dumps_json(
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
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (ref / "ref_20260101T000000Z_abcd1234" / "session.json").write_text(
        dumps_json(
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
            }
        )
        + "\n",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    (root / "runs" / "signals" / "latest").mkdir(parents=True, exist_ok=True)
    (root / "runs" / "signals" / "latest" / f"{pid}.json").write_text(
        dumps_json(
            {
                "schema": "argus.signal_collection.v1",
                "product_id": pid,
                "collected_at_utc": now.isoformat(),
                "records": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "runs" / "audit" / pid).mkdir(parents=True, exist_ok=True)
    (root / "runs" / "audit" / pid / "bundle.json").write_text(
        dumps_json(
            {
                "schema": "argus.audit_bundle.v1",
                "product_id": pid,
                "generated_at_utc": now.isoformat(),
                "angles": {
                    "product_gap": {"angle_status": "active", "summary_lines": ["ok"]},
                    "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _fixture_in_review_missing_reviews_in(root: Path, product_id: str) -> str:
    """Session in_review with draft, no reviews/ and no reviews_in/ (matches eligibility for submit action)."""
    sid = "ref_20260101T000000Z_abcd1234"
    ref = root / "runs" / "refinement"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "index.json").write_text(
        dumps_json(
            {
                "schema": "argus.refinement_index.v1",
                "sessions": [
                    {
                        "session_id": sid,
                        "artifact_type": "product_spec",
                        "product_id": product_id,
                        "status": "in_review",
                        "current_round": 0,
                        "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sd = ref / sid
    (sd / "drafts").mkdir(parents=True)
    (sd / "reviews_in").mkdir(parents=True)
    (sd / "session.json").write_text(
        dumps_json(
            {
                "schema": "argus.refinement_session.v1",
                "session_id": sid,
                "artifact_type": "product_spec",
                "source_id": "idea1",
                "product_id": product_id,
                "current_round": 0,
                "max_rounds": 4,
                "status": "in_review",
                "required_stakeholders": ["product"],
                "optional_stakeholders": [],
                "created_at_utc": "2026-01-01T00:00:00+00:00",
                "updated_at_utc": "2026-01-01T00:00:00+00:00",
                "meta": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (sd / "drafts" / "round_0.json").write_text(
        dumps_json({"schema": "argus.artifact_draft.v1", "round_number": 0}) + "\n",
        encoding="utf-8",
    )
    return sid


def _fixture_escalation_consider_eligible(root: Path, product_id: str) -> None:
    """Approved product_spec + fresh signals + partial audit gaps → escalation_consider eligible (see orchestration tests)."""
    _minimal_product(root, product_id)
    ref = root / "runs" / "refinement"
    ref.mkdir(parents=True, exist_ok=True)
    (ref / "index.json").write_text(
        dumps_json(
            {
                "schema": "argus.refinement_index.v1",
                "sessions": [
                    {
                        "session_id": "ref_20260101T000000Z_abcd1234",
                        "artifact_type": "product_spec",
                        "source_id": "x",
                        "product_id": product_id,
                        "status": "approved",
                        "current_round": 0,
                        "updated_at_utc": "2026-01-01T00:00:00+00:00",
                    },
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sd = ref / "ref_20260101T000000Z_abcd1234"
    (sd / "drafts").mkdir(parents=True, exist_ok=True)
    (sd / "session.json").write_text(
        dumps_json(
            {
                "schema": "argus.refinement_session.v1",
                "session_id": "ref_20260101T000000Z_abcd1234",
                "artifact_type": "product_spec",
                "source_id": "x",
                "product_id": product_id,
                "current_round": 0,
                "max_rounds": 4,
                "status": "approved",
                "required_stakeholders": ["product"],
                "optional_stakeholders": [],
                "created_at_utc": "2026-01-01T00:00:00+00:00",
                "updated_at_utc": "2026-01-01T00:00:00+00:00",
                "meta": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    now = datetime.now(timezone.utc)
    (root / "runs" / "signals" / "latest").mkdir(parents=True, exist_ok=True)
    (root / "runs" / "signals" / "latest" / f"{product_id}.json").write_text(
        dumps_json(
            {
                "schema": "argus.signal_collection.v1",
                "product_id": product_id,
                "collected_at_utc": now.isoformat(),
                "records": [],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (root / "runs" / "audit" / product_id).mkdir(parents=True, exist_ok=True)
    (root / "runs" / "audit" / product_id / "bundle.json").write_text(
        dumps_json(
            {
                "schema": "argus.audit_bundle.v1",
                "product_id": product_id,
                "generated_at_utc": now.isoformat(),
                "angles": {
                    "product_gap": {"angle_status": "partial", "summary_lines": ["ok"]},
                    "security": {"angle_status": "partial", "summary_lines": ["ok"]},
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


class TestOrchestrationStepExecutor(unittest.TestCase):
    def test_refinement_run_fails_when_not_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            r = execute_orchestration_action(root, "p1", ACTION_REFINEMENT_RUN)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)

    def test_refinement_run_executes_one_cycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            create_session(root, ArtifactType.PRODUCT_SPEC, "p1", product_id="p1")
            execute_orchestration_action(root, "p1", ACTION_SIGNALS_COLLECT)
            execute_orchestration_action(root, "p1", ACTION_AUDIT_RUN)
            r = execute_orchestration_action(root, "p1", ACTION_REFINEMENT_RUN)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            self.assertIn("session_id", r.get("execution_detail") or {})

    def test_unknown_action_is_queued_unhandled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = execute_orchestration_action(root, "p1", "not_a_real_action_id")
            self.assertEqual(r["action_status"], ACTION_STATUS_QUEUED_UNHANDLED)
            self.assertIn("reason", r.get("execution_detail") or {})

    def test_refinement_submit_reviews_in_executes_and_writes_reviews_in(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rin"
            _minimal_product(root, pid)
            sid = _fixture_in_review_missing_reviews_in(root, pid)
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = _orch_fresh_now()
                r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            rip = root / "runs" / "refinement" / sid / "reviews_in" / "round_0.json"
            self.assertTrue(rip.is_file())
            data = json.loads(rip.read_text(encoding="utf-8"))
            self.assertIn("product", data)
            self.assertIn("rationale", data["product"])

    def test_refinement_submit_reviews_in_fails_when_no_session_needs_it(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("no session", (r.get("execution_error") or "").lower())

    def test_refinement_submit_reviews_in_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rin2"
            _minimal_product(root, pid)
            _fixture_in_review_missing_reviews_in(root, pid)
            now = _orch_fresh_now()
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(
                ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
                summ.get("queued_unhandled_action_ids") or [],
            )
            st = (summ.get("by_action_id") or {}).get(ACTION_REFINEMENT_SUBMIT_REVIEWS_IN, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_escalation_consider_executes_and_writes_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_esc"
            _fixture_escalation_consider_eligible(root, pid)
            st0 = evaluate_product_orchestration(root, pid)
            self.assertIn(ACTION_ESCALATION_CONSIDER, {x["action_id"] for x in st0["eligible_actions"]})
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = _orch_fresh_now()
                r = execute_orchestration_action(root, pid, ACTION_ESCALATION_CONSIDER)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA)
            ap = root / det["escalation_consider_artifact_path"]
            self.assertTrue(ap.is_file())
            data = json.loads(ap.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], ORCHESTRATION_ESCALATION_CONSIDER_ARTIFACT_SCHEMA)
            self.assertEqual(data["product_id"], pid)
            self.assertIsInstance(data.get("escalation_triggers"), list)

    def test_escalation_consider_fails_when_not_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            fake = evaluate_product_orchestration(root, pid)
            fake = dict(fake)
            fake["eligible_actions"] = [
                x for x in (fake.get("eligible_actions") or []) if x.get("action_id") != ACTION_ESCALATION_CONSIDER
            ]
            with patch("argus.orchestrator.step_executor.evaluate_product_orchestration", return_value=fake):
                r = execute_orchestration_action(root, pid, ACTION_ESCALATION_CONSIDER)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("not an eligible action", (r.get("execution_error") or "").lower())

    def test_escalation_consider_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_esc2"
            _fixture_escalation_consider_eligible(root, pid)
            now = _orch_fresh_now()
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_ESCALATION_CONSIDER)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_ESCALATION_CONSIDER, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_ESCALATION_CONSIDER, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_refinement_start_product_spec_creates_session(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            r = execute_orchestration_action(root, "p1", ACTION_REFINEMENT_START_PRODUCT_SPEC)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            self.assertIn("session_id", r.get("execution_detail") or {})

    def test_refinement_start_product_spec_fails_when_session_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            first = execute_orchestration_action(root, "p1", ACTION_REFINEMENT_START_PRODUCT_SPEC)
            self.assertEqual(first["action_status"], ACTION_STATUS_EXECUTED)
            second = execute_orchestration_action(root, "p1", ACTION_REFINEMENT_START_PRODUCT_SPEC)
            self.assertEqual(second["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("already exists", (second.get("execution_error") or "").lower())

    def test_refinement_start_idea_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_REFINEMENT_START_IDEA), "start_idea_refinement")

    def test_refinement_start_idea_fails_without_ideas_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_no_ideas"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("refinement_start_idea_eligible", True))

    def test_refinement_start_idea_fails_wrong_bundle_product_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_wrong"
            _minimal_product(root, pid)
            _write_ideas_latest_bundle(
                root,
                "other_product",
                [_idea_row("idea_a", "a", product_id="other_product")],
                now_iso=_orch_fresh_now().isoformat(),
            )
            r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)

    def test_refinement_start_idea_fails_empty_ideas(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_empty"
            _minimal_product(root, pid)
            _write_ideas_latest_bundle(root, pid, [], now_iso="2026-04-12T12:00:00+00:00")
            r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("refinement_start_idea_eligible", True))

    def test_refinement_start_idea_picks_highest_rank_not_list_order(self) -> None:
        """Explicit policy: rank_key desc, then idea_id asc — higher EV wins even if listed second."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rank"
            _minimal_product(root, pid)
            _write_ideas_latest_bundle(
                root,
                pid,
                [
                    _idea_row("idea_low", "low", expected_value=0.1, product_id=pid),
                    _idea_row("idea_high", "high", expected_value=0.95, product_id=pid),
                ],
                now_iso=_orch_fresh_now().isoformat(),
            )
            r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            self.assertEqual((r.get("execution_detail") or {}).get("source_idea_id"), "idea_high")
            self.assertEqual(
                (r.get("execution_detail") or {}).get("idea_selection_rule"),
                ORCHESTRATION_IDEA_SELECTION_RULE,
            )

    def test_refinement_start_idea_creates_session_and_feedback_not_queued_unhandled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rs_idea"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            _write_ideas_latest_bundle(
                root,
                pid,
                [_idea_row("idea_only", "t", product_id=pid)],
                now_iso=now.isoformat(),
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                r = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            sid = str(det.get("session_id") or "")
            self.assertTrue(sid.startswith("ref_"))
            self.assertEqual(det.get("draft_generation_in_this_action"), "none")
            path = root / "runs" / "refinement" / sid / "session.json"
            self.assertTrue(path.is_file())
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_REFINEMENT_START_IDEA, summ.get("queued_unhandled_action_ids") or [])

    def test_refinement_start_idea_second_call_fails_not_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_rs_dup"
            _minimal_product(root, pid)
            _write_ideas_latest_bundle(
                root,
                pid,
                [_idea_row("idea_x", "x", product_id=pid)],
                now_iso=_orch_fresh_now().isoformat(),
            )
            first = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(first["action_status"], ACTION_STATUS_EXECUTED)
            second = execute_orchestration_action(root, pid, ACTION_REFINEMENT_START_IDEA)
            self.assertEqual(second["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((second.get("execution_detail") or {}).get("refinement_start_idea_eligible", True))

    def test_refinement_start_idea_eligible_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_elig"
            _minimal_product(root, pid)
            _write_ideas_latest_bundle(
                root,
                pid,
                [_idea_row("idea_e", "e", product_id=pid)],
                now_iso=_orch_fresh_now().isoformat(),
            )
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue((st.get("eligibility_facts") or {}).get("refinement_start_idea_eligible"))

    def test_implementation_plan_generate_fails_when_not_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            r = execute_orchestration_action(root, "p1", ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("not eligible", (r.get("execution_error") or "").lower())

    def test_implementation_plan_generate_creates_session_when_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _write_impl_plan_generate_eligible_fixture(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("artifact_type"), "implementation_plan")
            self.assertEqual(det.get("source_id"), pid)

    def test_implementation_plan_generate_fails_when_session_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _write_impl_plan_generate_eligible_fixture(root, pid)
            first = execute_orchestration_action(root, pid, ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(first["action_status"], ACTION_STATUS_EXECUTED)
            second = execute_orchestration_action(root, pid, ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(second["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("already exists", (second.get("execution_error") or "").lower())

    def test_orchestration_state_refresh_executed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            r = execute_orchestration_action(root, "p1", ACTION_ORCHESTRATION_STATE_REFRESH)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            self.assertIn("orchestration_state_path", r.get("execution_detail") or {})

    def test_temporal_refresh_executes_and_writes_temporal_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_tref"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_TEMPORAL_REFRESH)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.temporal_bundle.v1")
            rel = str(det.get("temporal_latest_path") or "")
            self.assertTrue(rel.endswith(f"runs/temporal/latest/{pid}.json"))
            tpath = root / rel
            self.assertTrue(tpath.is_file())
            data = json.loads(tpath.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), "argus.temporal_bundle.v1")
            self.assertEqual(data.get("product_id"), pid)

    def test_findings_generate_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_FINDINGS_GENERATE), "generate_findings")

    def test_decisions_generate_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_DECISIONS_GENERATE), "generate_decisions")

    def test_decisions_refresh_from_surfaced_findings_task_type(self) -> None:
        self.assertEqual(
            task_type_for_action(ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS),
            "refresh_decisions_from_surfaced_findings",
        )

    def test_ideas_generate_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_IDEAS_GENERATE), "generate_ideas")

    def test_ideas_refresh_from_surfaced_findings_task_type(self) -> None:
        self.assertEqual(
            task_type_for_action(ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS),
            "refresh_ideas_from_surfaced_findings",
        )

    def test_escalation_packet_generate_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_ESCALATION_PACKET_GENERATE), "generate_escalation_packet")

    def test_experiments_propose_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_PROPOSE), "propose_experiments")

    def test_experiments_prioritize_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_PRIORITIZE), "prioritize_experiments")

    def test_experiments_create_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_CREATE), "create_experiment")

    def test_experiments_activate_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_ACTIVATE), "activate_experiment")

    def test_experiments_close_stale_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_CLOSE_STALE), "close_stale_experiments")

    def test_experiments_evaluate_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_EVALUATE), "evaluate_experiments")

    def test_experiments_surface_findings_task_type(self) -> None:
        self.assertEqual(task_type_for_action(ACTION_EXPERIMENTS_SURFACE_FINDINGS), "surface_experiment_findings")

    def test_findings_generate_executes_and_writes_findings_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fin_gen"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.findings_bundle.v1")
            rel = str(det.get("findings_latest_path") or "")
            self.assertTrue(rel.endswith(f"runs/findings/latest/{pid}.json"))
            fpath = root / rel
            self.assertTrue(fpath.is_file())
            data = json.loads(fpath.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), "argus.findings_bundle.v1")
            self.assertEqual(data.get("product_id"), pid)

    def test_findings_generate_fails_without_signals_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fin_miss"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("signals", (r.get("execution_error") or "").lower())

    def test_findings_generate_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fin_stale"
            _minimal_product(root, pid)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": "2020-01-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("non-stale", (r.get("execution_error") or "").lower())

    def test_decisions_generate_executes_and_writes_decisions_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dec_gen"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.decisions_bundle.v1")
            self.assertIn("lifecycle_stage", det)
            rel = str(det.get("decisions_latest_path") or "")
            self.assertTrue(rel.endswith(f"runs/decisions/latest/{pid}.json"))
            dpath = root / rel
            self.assertTrue(dpath.is_file())
            data = json.loads(dpath.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), "argus.decisions_bundle.v1")
            self.assertEqual(data.get("product_id"), pid)

    def test_decisions_generate_fails_without_findings_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dec_miss"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            err = (r.get("execution_error") or "").lower()
            self.assertTrue("findings" in err or "eligible" in err)

    def test_decisions_generate_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dec_stale"
            _minimal_product(root, pid)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": "2020-01-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2020-06-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("fresh", (r.get("execution_error") or "").lower())

    def test_escalation_packet_generate_executes_and_writes_packet(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_epk_ok"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "audit" / pid).mkdir(parents=True, exist_ok=True)
            (root / "runs" / "audit" / pid / "bundle.json").write_text(
                dumps_json(
                    {
                        "schema": "argus.audit_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_ESCALATION_PACKET_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.escalation_packet.v1")
            self.assertIn("packet_id", det)
            rel = str(det.get("escalation_latest_path") or "")
            self.assertTrue(rel.startswith("runs/escalations/latest/"))
            ppath = root / rel
            self.assertTrue(ppath.is_file())
            data = json.loads(ppath.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), "argus.escalation_packet.v1")
            self.assertEqual(data.get("product_id"), pid)

    def test_escalation_packet_generate_fails_without_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_epk_miss"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            (root / "runs" / "audit" / pid).mkdir(parents=True, exist_ok=True)
            (root / "runs" / "audit" / pid / "bundle.json").write_text(
                dumps_json(
                    {
                        "schema": "argus.audit_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_ESCALATION_PACKET_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("findings", (r.get("execution_error") or "").lower())

    def test_escalation_packet_generate_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_epk_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "audit" / pid).mkdir(parents=True, exist_ok=True)
            (root / "runs" / "audit" / pid / "bundle.json").write_text(
                dumps_json(
                    {
                        "schema": "argus.audit_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_ESCALATION_PACKET_GENERATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_ESCALATION_PACKET_GENERATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_ESCALATION_PACKET_GENERATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_decisions_generate_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dec_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_DECISIONS_GENERATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_DECISIONS_GENERATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_DECISIONS_GENERATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_ideas_generate_fails_without_decisions_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ideas_no_dec"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            st0 = evaluate_product_orchestration(root, pid)
            self.assertFalse((st0.get("eligibility_facts") or {}).get("ideas_generate_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_IDEAS_GENERATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("ideas_generate_eligible", True))

    def test_ideas_generate_run_pipeline_uses_deterministic_flags(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ideas_flags"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            gen_p = root / "runs" / "ideas" / "stubts.json"
            gen_p.parent.mkdir(parents=True, exist_ok=True)
            bundle = SimpleNamespace(schema="argus.ideas_bundle.v1", ideas=[], meta={})
            with patch("argus.idea_generation.pipeline.run_pipeline", return_value=(gen_p, bundle)) as rp:
                execute_orchestration_action(root, pid, ACTION_IDEAS_GENERATE)
            rp.assert_called_once()
            self.assertEqual(rp.call_args.kwargs.get("advisor_expansion"), False)
            self.assertEqual(rp.call_args.kwargs.get("llm_idea_expansion"), False)
            self.assertEqual(rp.call_args.kwargs.get("max_advisor_expansion_ideas"), 0)
            self.assertEqual(rp.call_args.kwargs.get("max_llm_expansion_ideas"), 0)

    def test_ideas_generate_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ideas_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_IDEAS_GENERATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_IDEAS_GENERATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_IDEAS_GENERATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)
            self.assertTrue((root / "runs" / "ideas" / "latest.json").is_file())

    def test_experiments_propose_writes_proposals_artifact_and_detail(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_prop"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PROPOSE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.experiment_proposals_run.v1")
            self.assertEqual(det.get("proposal_artifact_paths"), [det.get("proposals_latest_path")])
            self.assertIn("authority_note", det)
            prop = root / "runs" / "experiments" / "proposals" / "latest" / f"{pid}.json"
            self.assertTrue(prop.is_file())
            loaded = json.loads(prop.read_text(encoding="utf-8"))
            self.assertEqual(loaded.get("schema"), "argus.experiment_proposals_run.v1")
            self.assertIsInstance(loaded.get("proposals"), list)

    def test_experiments_propose_fails_without_decisions_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_nopre"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PROPOSE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("experiments_propose_eligible", True))

    def test_experiments_propose_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_stale"
            _minimal_product(root, pid)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": "2020-01-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PROPOSE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("fresh", (r.get("execution_error") or "").lower())

    def test_experiments_propose_eligibility_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_elig"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            now_s = now.isoformat()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now_s,
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now_s,
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            st_missing = evaluate_product_orchestration(root, pid)
            self.assertFalse((st_missing.get("eligibility_facts") or {}).get("experiments_propose_eligible"))
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now_s,
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            st0 = evaluate_product_orchestration(root, pid)
            self.assertTrue((st0.get("eligibility_facts") or {}).get("experiments_propose_eligible"))

    def test_experiments_propose_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PROPOSE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_EXPERIMENTS_PROPOSE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_EXPERIMENTS_PROPOSE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def _spine_and_proposals_latest(
        self,
        root: Path,
        pid: str,
        *,
        now: datetime,
        proposals: list[dict],
    ) -> None:
        sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
        sigp.parent.mkdir(parents=True, exist_ok=True)
        sigp.write_text(
            dumps_json(
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now.isoformat(),
                    "repo_root": str(root),
                    "records": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
        fp.parent.mkdir(parents=True, exist_ok=True)
        fp.write_text(
            dumps_json(
                {
                    "schema": "argus.findings_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "repo_root": str(root),
                    "finding_count": 0,
                    "findings": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(
            dumps_json(
                {
                    "schema": "argus.decisions_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now.isoformat(),
                    "repo_root": str(root),
                    "decisions": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        prop = root / "runs" / "experiments" / "proposals" / "latest" / f"{pid}.json"
        prop.parent.mkdir(parents=True, exist_ok=True)
        prop.write_text(
            dumps_json(
                {
                    "schema": "argus.experiment_proposals_run.v1",
                    "generated_at_utc": now.isoformat(),
                    "repo_root": str(root),
                    "proposals": proposals,
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def test_experiments_prioritize_writes_prioritization_artifact_and_detail(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_pri"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.experiment_prioritization_run.v2")
            self.assertEqual(det.get("ranked_count"), 0)
            self.assertIsNone(det.get("top_experiment_id"))
            self.assertIsNone(det.get("top_score"))
            self.assertIn("authority_note", det)
            rel = str(det.get("prioritization_latest_path") or "")
            self.assertTrue(rel.startswith("runs/experiments/prioritization/latest/"))
            self.assertTrue((root / rel).is_file())
            loaded = json.loads((root / rel).read_text(encoding="utf-8"))
            self.assertEqual(loaded.get("schema"), "argus.experiment_prioritization_run.v2")

    def test_experiments_prioritize_does_not_call_propose_experiments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_no_reprop"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_only_from_file",
                "product_id": pid,
                "hypothesis": "ONLY_FROM_FILE",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            with patch("argus.experiments.propose.propose_experiments") as m_prop:
                m_prop.side_effect = AssertionError("propose_experiments must not run for experiments_prioritize")
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            self.assertEqual((r.get("execution_detail") or {}).get("top_experiment_id"), "pr_only_from_file")
            m_prop.assert_not_called()

    def test_experiments_prioritize_fails_without_proposals_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_pri_miss"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("experiments_prioritize_eligible", True))

    def test_experiments_prioritize_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pri_stale"
            _minimal_product(root, pid)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": "2020-01-01T00:00:00+00:00",
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            fp = root / "runs" / "findings" / "latest" / f"{pid}.json"
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(
                dumps_json(
                    {
                        "schema": "argus.findings_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "repo_root": str(root),
                        "finding_count": 0,
                        "findings": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dp.parent.mkdir(parents=True, exist_ok=True)
            dp.write_text(
                dumps_json(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "repo_root": str(root),
                        "decisions": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            prop = root / "runs" / "experiments" / "proposals" / "latest" / f"{pid}.json"
            prop.parent.mkdir(parents=True, exist_ok=True)
            prop.write_text(
                dumps_json(
                    {
                        "schema": "argus.experiment_proposals_run.v1",
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "repo_root": str(root),
                        "proposals": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("fresh", (r.get("execution_error") or "").lower())

    def test_experiments_prioritize_eligibility_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pri_elig"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue((st.get("eligibility_facts") or {}).get("experiments_prioritize_eligible"))
            (root / "runs" / "experiments" / "proposals" / "latest" / f"{pid}.json").unlink()
            st2 = evaluate_product_orchestration(root, pid)
            self.assertFalse((st2.get("eligibility_facts") or {}).get("experiments_prioritize_eligible"))

    def test_experiments_prioritize_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pri_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_EXPERIMENTS_PRIORITIZE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_EXPERIMENTS_PRIORITIZE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_experiments_create_writes_experiment_from_prioritization_only(self) -> None:
        """Success path: top-ranked row from persisted prioritization JSON (no re-prioritize)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_cre"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_from_pri",
                "product_id": pid,
                "hypothesis": "ORIGINAL",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            pri_rel = f"runs/experiments/prioritization/latest/{pid}.json"
            raw_pri = json.loads((root / pri_rel).read_text(encoding="utf-8"))
            rows = raw_pri["by_product"][pid]
            rows[0]["proposal"]["hypothesis"] = "FROM_PRIORITIZATION_FILE"
            (root / pri_rel).write_text(dumps_json(raw_pri) + "\n", encoding="utf-8")
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.experiment.v1")
            self.assertEqual(det.get("source_proposal_id"), "pr_from_pri")
            self.assertEqual(det.get("source_rank"), 1)
            self.assertIn("authority_note", det)
            self.assertIn("quota", det)
            exp_rel = str(det.get("experiment_path") or "")
            self.assertTrue(exp_rel.startswith("runs/experiments/"))
            data = json.loads((root / exp_rel).read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), "argus.experiment.v1")
            self.assertEqual(data.get("hypothesis"), "FROM_PRIORITIZATION_FILE")
            self.assertEqual(data.get("source_proposal_id"), "pr_from_pri")

    def test_experiments_create_dedupe_skips_when_source_proposal_id_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_exp_dedupe"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_dedupe",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            r1 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r1["action_status"], ACTION_STATUS_EXECUTED)
            eid = (r1.get("execution_detail") or {}).get("experiment_id")
            self.assertIsNotNone(eid)
            r2 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r2["action_status"], ACTION_STATUS_EXECUTED)
            det2 = r2.get("execution_detail") or {}
            self.assertTrue(det2.get("dedupe_skipped"))
            self.assertEqual(det2.get("experiment_id"), eid)

    def test_experiments_create_fails_not_eligible_missing_spine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_spine"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("experiments_create_eligible", True))

    def test_experiments_create_fails_when_prioritization_missing_after_forced_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_miss"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_x",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"experiments_create_eligible": True}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("prioritization", (r.get("execution_error") or "").lower())

    def test_experiments_create_fails_empty_ranked_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_empty"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_x",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            pri_p = root / "runs" / "experiments" / "prioritization" / "latest" / f"{pid}.json"
            raw = json.loads(pri_p.read_text(encoding="utf-8"))
            raw["by_product"][pid] = []
            pri_p.write_text(dumps_json(raw) + "\n", encoding="utf-8")
            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"experiments_create_eligible": True}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("ranked", (r.get("execution_error") or "").lower())

    def test_experiments_create_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_stale"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_x",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            raw_s = json.loads(sigp.read_text(encoding="utf-8"))
            raw_s["collected_at_utc"] = "2020-01-01T00:00:00+00:00"
            sigp.write_text(dumps_json(raw_s) + "\n", encoding="utf-8")
            r2 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r2["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r2.get("execution_detail") or {}).get("experiments_create_eligible", True))
            self.assertIn("fresh", (r2.get("execution_error") or "").lower())

    def test_experiments_create_fails_when_quota_exhausted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_quota"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_q",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            with patch(
                "argus.autonomy.quotas.check_experiment_create_allowed",
                return_value=(False, "experiment create quota exhausted: 9/9"),
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("quota", (r.get("execution_error") or "").lower())

    def test_experiments_create_eligibility_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_elig"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_e",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse((st.get("eligibility_facts") or {}).get("experiments_create_eligible"))
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            st2 = evaluate_product_orchestration(root, pid)
            self.assertTrue((st2.get("eligibility_facts") or {}).get("experiments_create_eligible"))

    def test_experiments_create_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cre_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            one_prop = {
                "proposal_id": "pr_fb",
                "product_id": pid,
                "hypothesis": "H",
                "type": "growth",
                "description": "d",
                "expected_outcome": "e",
                "success_metrics": [],
                "estimated_effort": "small",
                "confidence": 0.55,
                "rationale": "unit_test",
                "schema": "argus.experiment_proposal.v1",
            }
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[one_prop])
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_EXPERIMENTS_CREATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_EXPERIMENTS_CREATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_experiments_activate_transitions_proposed_to_active_on_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_ok"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_act_1",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.experiment.v1")
            self.assertEqual(det.get("experiment_id"), "exp_act_1")
            self.assertEqual(det.get("previous_status"), "proposed")
            self.assertEqual(det.get("new_status"), "active")
            self.assertEqual(det.get("selection_rule"), "only_proposed_experiment")
            self.assertIn("authority_note", det)
            rel = str(det.get("experiment_path") or "")
            self.assertTrue(rel.startswith("runs/experiments/"))
            raw = json.loads((root / rel).read_text(encoding="utf-8"))
            self.assertEqual(raw.get("status"), "active")

    def test_experiments_activate_does_not_call_propose_experiments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_no_prop"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_act_np",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            with patch("argus.experiments.propose.propose_experiments") as m_prop:
                m_prop.side_effect = AssertionError("propose_experiments must not run for experiments_activate")
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            m_prop.assert_not_called()
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))

    def test_experiments_activate_two_proposed_selects_smallest_created_at_then_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_two"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            t_early = "2026-04-01T12:00:00+00:00"
            t_late = "2026-04-11T12:00:00+00:00"
            save_experiment(
                root,
                Experiment(
                    id="exp_late",
                    product_id=pid,
                    hypothesis="L",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=t_late,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=t_late,
                ),
            )
            save_experiment(
                root,
                Experiment(
                    id="exp_early",
                    product_id=pid,
                    hypothesis="E",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=t_early,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=t_early,
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            self.assertEqual((r.get("execution_detail") or {}).get("experiment_id"), "exp_early")
            self.assertEqual(
                (r.get("execution_detail") or {}).get("selection_rule"),
                "created_at_then_experiment_id",
            )

    def test_experiments_activate_fails_without_experiments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_none"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"experiments_activate_eligible": True}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("at least one persisted experiment", (r.get("execution_error") or "").lower())

    def test_experiments_activate_fails_without_proposed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_noprop"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_done",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=now.isoformat(),
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"experiments_activate_eligible": True}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("proposed", (r.get("execution_error") or "").lower())

    def test_experiments_activate_fails_not_eligible_missing_spine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_spine"
            _minimal_product(root, pid)
            _ts = _orch_fresh_now().isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_sp",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=_ts,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=_ts,
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("experiments_activate_eligible", True))

    def test_experiments_activate_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_stale"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_stale",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            raw_s = json.loads(sigp.read_text(encoding="utf-8"))
            raw_s["collected_at_utc"] = "2020-01-01T00:00:00+00:00"
            sigp.write_text(dumps_json(raw_s) + "\n", encoding="utf-8")
            r2 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r2["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r2.get("execution_detail") or {}).get("experiments_activate_eligible", True))

    def test_experiments_activate_eligibility_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_elig"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse((st.get("eligibility_facts") or {}).get("experiments_activate_eligible"))
            save_experiment(
                root,
                Experiment(
                    id="exp_elig",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            st2 = evaluate_product_orchestration(root, pid)
            self.assertTrue((st2.get("eligibility_facts") or {}).get("experiments_activate_eligible"))

    def test_experiments_activate_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_act_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_fb",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_EXPERIMENTS_ACTIVATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_EXPERIMENTS_ACTIVATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_experiments_close_stale_transitions_stale_to_failed_on_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_ok"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            old = (now - timedelta(days=120)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_cs_old",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=old,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=old,
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.experiment.v1")
            self.assertEqual(det.get("affected_experiment_ids"), ["exp_cs_old"])
            self.assertEqual((det.get("status_transitions") or [{}])[0].get("new_status"), "failed")
            self.assertIn("stale_close_rule", det)
            self.assertIn("authority_note", det)
            raw = json.loads((root / "runs" / "experiments" / "exp_cs_old.json").read_text(encoding="utf-8"))
            self.assertEqual(raw.get("status"), "failed")
            self.assertIsNotNone(raw.get("end_at"))

    def test_experiments_close_stale_two_experiments_sorted_deterministically(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_two"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            old = (now - timedelta(days=120)).isoformat()
            older = (now - timedelta(days=121)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_cs_b",
                    product_id=pid,
                    hypothesis="B",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=old,
                    end_at=None,
                    status=ExperimentStatus.ACTIVE,
                    confidence=0.5,
                    created_at=old,
                ),
            )
            save_experiment(
                root,
                Experiment(
                    id="exp_cs_a",
                    product_id=pid,
                    hypothesis="A",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=older,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=older,
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            self.assertEqual(
                (r.get("execution_detail") or {}).get("affected_experiment_ids"),
                ["exp_cs_a", "exp_cs_b"],
            )

    def test_experiments_close_stale_fails_without_experiments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_none"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"experiments_close_stale_eligible": True}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("persisted experiment", (r.get("execution_error") or "").lower())

    def test_experiments_close_stale_fails_when_none_match_predicates(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_fresh"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_fresh",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            with patch(
                "argus.orchestrator.step_executor.evaluate_product_orchestration",
                return_value={"eligibility_facts": {"experiments_close_stale_eligible": True}},
            ):
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("matched", (r.get("execution_error") or "").lower())

    def test_experiments_close_stale_eligibility_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_elig"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse((st.get("eligibility_facts") or {}).get("experiments_close_stale_eligible"))
            old = (now - timedelta(days=120)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_elig_cs",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=old,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=old,
                ),
            )
            st2 = evaluate_product_orchestration(root, pid)
            self.assertTrue((st2.get("eligibility_facts") or {}).get("experiments_close_stale_eligible"))

    def test_experiments_close_stale_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            old = (now - timedelta(days=120)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_cs_fb",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=old,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=old,
                ),
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_EXPERIMENTS_CLOSE_STALE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_EXPERIMENTS_CLOSE_STALE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_experiments_close_stale_fails_not_eligible_missing_spine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_cs_spine"
            _minimal_product(root, pid)
            old = (_orch_fresh_now() - timedelta(days=120)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_sp_cs",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=old,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=old,
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("experiments_close_stale_eligible", True))

    def test_experiments_evaluate_updates_persisted_experiment_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_ok"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_ev_only",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            with patch("argus.experiments.propose.propose_experiments") as m_prop:
                m_prop.side_effect = AssertionError("propose_experiments must not run for experiments_evaluate")
                r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            m_prop.assert_not_called()
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED, msg=r.get("execution_error"))
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.experiment_evaluation.v1")
            self.assertEqual(det.get("evaluated_count"), 1)
            self.assertIn("authority_note", det)
            self.assertIn("runs/experiments/exp_ev_only.json", det.get("evaluation_artifact_paths") or [])
            raw = json.loads((root / "runs" / "experiments" / "exp_ev_only.json").read_text(encoding="utf-8"))
            self.assertIsNotNone(raw.get("last_evaluation_verdict"))

    def test_experiments_evaluate_fails_without_experiments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_none"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r.get("execution_detail") or {}).get("experiments_evaluate_eligible", True))

    def test_experiments_evaluate_fails_without_non_terminal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_term"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_done",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=now.isoformat(),
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("non-terminal", (r.get("execution_error") or "").lower())

    def test_experiments_evaluate_fails_when_signals_stale(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_stale"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_stale",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            raw_s = json.loads(sigp.read_text(encoding="utf-8"))
            raw_s["collected_at_utc"] = "2020-01-01T00:00:00+00:00"
            sigp.write_text(dumps_json(raw_s) + "\n", encoding="utf-8")
            r2 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            self.assertEqual(r2["action_status"], ACTION_STATUS_FAILED)
            self.assertFalse((r2.get("execution_detail") or {}).get("experiments_evaluate_eligible", True))

    def test_experiments_evaluate_eligibility_fact_inspectable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_elig"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse((st.get("eligibility_facts") or {}).get("experiments_evaluate_eligible"))
            save_experiment(
                root,
                Experiment(
                    id="exp_elig",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            st2 = evaluate_product_orchestration(root, pid)
            self.assertTrue((st2.get("eligibility_facts") or {}).get("experiments_evaluate_eligible"))

    def test_experiments_evaluate_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ev_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            self._spine_and_proposals_latest(root, pid, now=now, proposals=[])
            save_experiment(
                root,
                Experiment(
                    id="exp_fb",
                    product_id=pid,
                    hypothesis="H",
                    type=ExperimentType.GROWTH,
                    description="d",
                    expected_outcome="e",
                    success_metrics=[],
                    start_at=now.isoformat(),
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now.isoformat(),
                ),
            )
            execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_EXPERIMENTS_EVALUATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_EXPERIMENTS_EVALUATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_findings_generate_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fin_fb"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_FINDINGS_GENERATE, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_FINDINGS_GENERATE, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_temporal_refresh_fails_without_signals_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_tref2"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_TEMPORAL_REFRESH)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("signals/latest", (r.get("execution_error") or "").lower())

    def test_temporal_refresh_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_tref3"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_TEMPORAL_REFRESH)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(ACTION_TEMPORAL_REFRESH, summ.get("queued_unhandled_action_ids") or [])
            st = (summ.get("by_action_id") or {}).get(ACTION_TEMPORAL_REFRESH, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_execution_outcomes_apply_fails_when_nothing_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            r = execute_orchestration_action(root, "p1", ACTION_EXECUTION_OUTCOMES_APPLY)
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)
            self.assertIn("no pending", (r.get("execution_error") or "").lower())

    def test_execution_outcomes_apply_executes_when_unapplied_json_exists(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eoa"
            _minimal_product(root, pid)
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            r = execute_orchestration_action(root, pid, ACTION_EXECUTION_OUTCOMES_APPLY)
            self.assertEqual(r["action_status"], ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.execution_apply_report.v1")
            self.assertEqual(det.get("product_id"), pid)
            self.assertGreaterEqual(int(det.get("files_seen") or 0), 1)

    def test_execution_outcomes_apply_eligible_when_pending_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eoa_elig"
            _minimal_product(root, pid)
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            ids = {x["action_id"] for x in (st.get("eligible_actions") or [])}
            self.assertIn(ACTION_EXECUTION_OUTCOMES_APPLY, ids)
            self.assertTrue(st.get("eligibility_facts", {}).get("execution_outcomes_apply_pending"))

    def test_execution_outcomes_apply_ordered_first_when_pending_non_feedback_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ord_eoa"
            _minimal_product(root, pid)
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in st["eligible_actions"]]
            self.assertEqual(ids[0], ACTION_EXECUTION_OUTCOMES_APPLY)
            self.assertEqual(st["next_action"], ACTION_EXECUTION_OUTCOMES_APPLY)
            self.assertEqual(st["eligibility_facts"].get("eligible_actions_order_rule"), ELIGIBLE_ORDER_RULE_EOA_FIRST)

    def test_execution_outcomes_apply_ordered_after_impl_when_prefer_impl(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ord_impl"
            _write_impl_plan_generate_eligible_fixture(root, pid)
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in st["eligible_actions"]]
            self.assertEqual(ids[0], ACTION_IMPLEMENTATION_PLAN_GENERATE)
            idx_eoa = ids.index(ACTION_EXECUTION_OUTCOMES_APPLY)
            idx_impl = ids.index(ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertLess(idx_impl, idx_eoa)
            self.assertEqual(st["next_action"], ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(st["eligibility_facts"].get("eligible_actions_order_rule"), ELIGIBLE_ORDER_RULE_EOA_AFTER_IMPL)

    def test_eligible_order_stable_without_pending_apply_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_no_eoa"
            _minimal_product(root, pid)
            st = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in st["eligible_actions"]]
            self.assertGreater(len(ids), 0)
            self.assertEqual(ids[0], ACTION_SIGNALS_COLLECT)
            self.assertIsNone(st["eligibility_facts"].get("eligible_actions_order_rule"))

    def test_execution_outcomes_apply_remains_first_when_temporal_refresh_eligible(self) -> None:
        """EOA pending ordering is stronger than temporal_refresh vs signals_collect."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eoa_tr"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "temporal" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "temporal" / "latest" / f"{pid}.json").write_text(
                dumps_json(
                    {
                        "schema": "argus.temporal_bundle.v1",
                        "product_id": pid,
                        "repo_root": str(root),
                        "collected_at_utc": now.isoformat(),
                        "source_signal_schema": "argus.signal_collection.v1",
                        "record_count": 0,
                        "worst_freshness_status": "stale",
                        "signals": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            ids = [a["action_id"] for a in st["eligible_actions"]]
            self.assertEqual(ids[0], ACTION_EXECUTION_OUTCOMES_APPLY)
            self.assertLess(ids.index(ACTION_TEMPORAL_REFRESH), ids.index(ACTION_SIGNALS_COLLECT))
            rule = st["eligibility_facts"].get("eligible_actions_order_rule") or ""
            expected = (
                f"{ELIGIBLE_ORDER_RULE_EOA_FIRST}{ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR}"
                f"{ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS}"
            )
            self.assertEqual(rule, expected)
            parts = rule.split(ELIGIBLE_ACTIONS_ORDER_RULE_SEPARATOR)
            self.assertEqual(
                parts,
                [ELIGIBLE_ORDER_RULE_EOA_FIRST, ELIGIBLE_ORDER_RULE_TEMPORAL_REFRESH_BEFORE_SIGNALS],
            )

    def test_state_hygiene_composed_order_rule_stable_on_repeat_evaluate(self) -> None:
        """Identical tree evaluated twice yields the same composed ordering rule string."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_det_ord"
            _minimal_product(root, pid)
            now = _orch_fresh_now()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now.isoformat(),
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "temporal" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "temporal" / "latest" / f"{pid}.json").write_text(
                dumps_json(
                    {
                        "schema": "argus.temporal_bundle.v1",
                        "product_id": pid,
                        "repo_root": str(root),
                        "collected_at_utc": now.isoformat(),
                        "source_signal_schema": "argus.signal_collection.v1",
                        "record_count": 0,
                        "worst_freshness_status": "stale",
                        "signals": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            a = evaluate_product_orchestration(root, pid)["eligibility_facts"].get("eligible_actions_order_rule")
            b = evaluate_product_orchestration(root, pid)["eligibility_facts"].get("eligible_actions_order_rule")
            self.assertEqual(a, b)

    def test_orchestration_feedback_files_do_not_trigger_apply_pending(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_fb_only"
            _minimal_product(root, pid)
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orchestration_feedback_x.json").write_text(
                '{"success": true, "product_id": "p_fb_only"}\n', encoding="utf-8"
            )
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st.get("eligibility_facts", {}).get("execution_outcomes_apply_pending"))
            ids = {a["action_id"] for a in st["eligible_actions"]}
            self.assertNotIn(ACTION_EXECUTION_OUTCOMES_APPLY, ids)

    def test_execution_outcomes_apply_success_not_queued_unhandled_in_feedback_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eoa_fb"
            _minimal_product(root, pid)
            exdir = root / "runs" / "execution" / pid
            exdir.mkdir(parents=True)
            (exdir / "orphan.json").write_text('{"success": true}\n', encoding="utf-8")
            now = _orch_fresh_now()
            with patch("argus.orchestrator.execution_feedback.datetime") as dt:
                dt.now.return_value = now
                execute_orchestration_action(root, pid, ACTION_EXECUTION_OUTCOMES_APPLY)
            summ = load_orchestration_feedback_summary(root, pid, now=now)
            self.assertNotIn(
                ACTION_EXECUTION_OUTCOMES_APPLY,
                summ.get("queued_unhandled_action_ids") or [],
            )
            st = (summ.get("by_action_id") or {}).get(ACTION_EXECUTION_OUTCOMES_APPLY, {})
            self.assertEqual(st.get("orchestration_action_status"), ACTION_STATUS_EXECUTED)

    def test_signals_collect_invalid_product_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = execute_orchestration_action(root, "nope", "signals_collect")
            self.assertEqual(r["action_status"], ACTION_STATUS_FAILED)

    def test_advance_execute_runs_signals_collect(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            _path, payload = advance_orchestration(root, pid, refresh_state=False, execute=True)
            self.assertEqual(payload.get("action_status"), ACTION_STATUS_EXECUTED)
            self.assertEqual(payload.get("selected_action"), "signals_collect")
            self.assertIn("collection_path", payload.get("execution_detail") or {})

    def test_task_artifact_step_execution_after_advance_execute(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            advance_orchestration(root, pid, refresh_state=True, execute=True)
            tpath = orchestration_task_path(root, pid)
            self.assertTrue(tpath.is_file())
            task = json.loads(tpath.read_text(encoding="utf-8"))
            self.assertEqual(task["schema"], ORCHESTRATION_TASK_SCHEMA)
            self.assertIn("step_execution", task)
            self.assertEqual(task["step_execution"].get("action_status"), ACTION_STATUS_EXECUTED)


if __name__ == "__main__":
    unittest.main()
