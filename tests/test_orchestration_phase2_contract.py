"""Phase-2 orchestration contract proofs — handled analysis + governance actions (model-contracts.md)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from argus.core.serialize import dumps_json
from argus.escalation.models import PACKET_SCHEMA as ESCALATION_PACKET_SCHEMA
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import list_experiments, save_experiment
from argus.findings.experiment_surfaced import (
    EXPERIMENT_SURFACED_SCHEMA,
    build_surfaced_findings,
    save_experiment_surfaced_bundle,
)
from argus.orchestrator.eligibility import (
    ELIGIBLE_ORDER_RULE_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN,
    RC_DECISIONS_GENERATE_FINDINGS_READY,
    RC_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS_READY,
    RC_ESCALATION_PACKET_GENERATE_POSTURE,
    RC_EXPERIMENTS_ACTIVATE_READY,
    RC_EXPERIMENTS_CLOSE_STALE_READY,
    RC_EXPERIMENTS_CREATE_READY,
    RC_EXPERIMENTS_EVALUATE_READY,
    RC_EXPERIMENTS_PRIORITIZE_READY,
    RC_EXPERIMENTS_PROPOSE_READY,
    RC_EXPERIMENTS_SURFACE_FINDINGS_READY,
    RC_FINDINGS_GENERATE_SIGNALS_READY,
    RC_IDEAS_GENERATE_DECISIONS_READY,
    RC_IDEAS_REFRESH_FROM_SURFACED_FINDINGS_READY,
    EligibleAction,
    _state_hygiene_order_phase2_observe_interpret_decide_govern,
    evaluate_product_orchestration,
)
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_PACKET_GENERATE,
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
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_SIGNALS_COLLECT,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from tests.test_orchestration_phase1_contract import PHASE1_STEP_EXECUTOR_ACTION_IDS
from tests.test_orchestration_state import _write
from tests.test_orchestration_step_executor import (
    _idea_row,
    _minimal_product,
    _orch_fresh_now,
    _write_ideas_latest_bundle,
)

# Phase-2: in-process handlers for interpret → decide → govern (subset of full dispatch table).
PHASE2_HANDLED_ORCHESTRATION_ACTION_IDS: frozenset[str] = frozenset(
    {
        ACTION_FINDINGS_GENERATE,
        ACTION_DECISIONS_GENERATE,
        ACTION_IDEAS_GENERATE,
        ACTION_EXPERIMENTS_PROPOSE,
        ACTION_EXPERIMENTS_PRIORITIZE,
        ACTION_EXPERIMENTS_CREATE,
        ACTION_EXPERIMENTS_ACTIVATE,
        ACTION_EXPERIMENTS_EVALUATE,
        ACTION_EXPERIMENTS_CLOSE_STALE,
        ACTION_EXPERIMENTS_SURFACE_FINDINGS,
        ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_ESCALATION_PACKET_GENERATE,
    }
)

# Documented execution_detail schema ids for successful writes (parity with step_executor).
_EXPECTED_SUCCESS_SCHEMA_BY_ACTION: dict[str, str] = {
    ACTION_FINDINGS_GENERATE: "argus.findings_bundle.v1",
    ACTION_DECISIONS_GENERATE: "argus.decisions_bundle.v1",
    ACTION_IDEAS_GENERATE: "argus.ideas_bundle.v1",
    ACTION_EXPERIMENTS_PROPOSE: "argus.experiment_proposals_run.v1",
    ACTION_EXPERIMENTS_PRIORITIZE: "argus.experiment_prioritization_run.v2",
    ACTION_EXPERIMENTS_CREATE: "argus.experiment.v1",
    ACTION_EXPERIMENTS_ACTIVATE: "argus.experiment.v1",
    ACTION_EXPERIMENTS_EVALUATE: "argus.experiment_evaluation.v1",
    ACTION_EXPERIMENTS_CLOSE_STALE: "argus.experiment.v1",
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: EXPERIMENT_SURFACED_SCHEMA,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: "argus.decisions_bundle.v1",
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: "argus.ideas_bundle.v1",
    ACTION_ESCALATION_PACKET_GENERATE: ESCALATION_PACKET_SCHEMA,
}


class TestOrchestrationPhase2Contract(unittest.TestCase):
    def test_phase2_handled_ids_are_subset_of_full_dispatch_table(self) -> None:
        self.assertTrue(
            PHASE2_HANDLED_ORCHESTRATION_ACTION_IDS <= PHASE1_STEP_EXECUTOR_ACTION_IDS,
            msg=f"Phase-2 ids must remain a subset of the frozen dispatch table: {PHASE2_HANDLED_ORCHESTRATION_ACTION_IDS - PHASE1_STEP_EXECUTOR_ACTION_IDS}",
        )
        self.assertEqual(len(PHASE2_HANDLED_ORCHESTRATION_ACTION_IDS), 13)

    def test_phase2_dispatch_coverage_matches_schema_map_and_documented_count(self) -> None:
        """Each Phase-2 handled id must map to a documented success schema (parity with model-contracts.md)."""
        self.assertEqual(len(_EXPECTED_SUCCESS_SCHEMA_BY_ACTION), 13)
        self.assertEqual(
            set(_EXPECTED_SUCCESS_SCHEMA_BY_ACTION.keys()),
            PHASE2_HANDLED_ORCHESTRATION_ACTION_IDS,
        )

    def test_phase2_reason_codes_stable(self) -> None:
        self.assertEqual(RC_FINDINGS_GENERATE_SIGNALS_READY, "findings_generate_signals_ready")
        self.assertEqual(RC_DECISIONS_GENERATE_FINDINGS_READY, "decisions_generate_findings_ready")
        self.assertEqual(RC_ESCALATION_PACKET_GENERATE_POSTURE, "escalation_packet_generate_posture_ready")
        self.assertEqual(RC_IDEAS_GENERATE_DECISIONS_READY, "ideas_generate_decisions_ready")
        self.assertEqual(RC_EXPERIMENTS_PROPOSE_READY, "experiments_propose_ready")
        self.assertEqual(RC_EXPERIMENTS_PRIORITIZE_READY, "experiments_prioritize_ready")
        self.assertEqual(RC_EXPERIMENTS_CREATE_READY, "experiments_create_ready")
        self.assertEqual(RC_EXPERIMENTS_ACTIVATE_READY, "experiments_activate_ready")
        self.assertEqual(RC_EXPERIMENTS_EVALUATE_READY, "experiments_evaluate_ready")
        self.assertEqual(RC_EXPERIMENTS_CLOSE_STALE_READY, "experiments_close_stale_ready")
        self.assertEqual(RC_EXPERIMENTS_SURFACE_FINDINGS_READY, "experiments_surface_findings_ready")
        self.assertEqual(
            RC_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS_READY,
            "decisions_refresh_from_surfaced_findings_ready",
        )
        self.assertEqual(RC_IDEAS_REFRESH_FROM_SURFACED_FINDINGS_READY, "ideas_refresh_from_surfaced_findings_ready")

    def test_phase2_success_execution_detail_schema_parity(self) -> None:
        """Each Phase-2 action returns its documented bundle schema on successful write."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_p2_schema"
            _minimal_product(root, pid)
            now_iso = _orch_fresh_now().isoformat()
            sigp = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sigp.parent.mkdir(parents=True, exist_ok=True)
            sigp.write_text(
                dumps_json(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": pid,
                        "collected_at_utc": now_iso,
                        "repo_root": str(root),
                        "records": [],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            # findings_generate
            r1 = execute_orchestration_action(root, pid, ACTION_FINDINGS_GENERATE)
            self.assertEqual(r1.get("action_status"), "executed")
            self.assertEqual(
                (r1.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_FINDINGS_GENERATE],
            )
            # decisions_generate
            r2 = execute_orchestration_action(root, pid, ACTION_DECISIONS_GENERATE)
            self.assertEqual(r2.get("action_status"), "executed")
            self.assertEqual(
                (r2.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_DECISIONS_GENERATE],
            )
            # ideas_generate — deterministic pipeline (no advisor/LLM expansion)
            r2b = execute_orchestration_action(root, pid, ACTION_IDEAS_GENERATE)
            self.assertEqual(r2b.get("action_status"), "executed", msg=r2b.get("execution_error"))
            det_ideas = r2b.get("execution_detail") or {}
            self.assertEqual(det_ideas.get("schema"), _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_IDEAS_GENERATE])
            self.assertIs(det_ideas.get("advisor_expansion"), False)
            self.assertIs(det_ideas.get("llm_idea_expansion"), False)
            # experiments_propose — deterministic proposals artifact (hypotheses only)
            r2c = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PROPOSE)
            self.assertEqual(r2c.get("action_status"), "executed", msg=r2c.get("execution_error"))
            self.assertEqual(
                (r2c.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_PROPOSE],
            )
            prop_rel = str((r2c.get("execution_detail") or {}).get("proposals_latest_path") or "")
            self.assertTrue(prop_rel.startswith("runs/experiments/proposals/latest/"))
            # experiments_prioritize — deterministic ranking from persisted proposals only
            r2d = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertEqual(r2d.get("action_status"), "executed", msg=r2d.get("execution_error"))
            self.assertEqual(
                (r2d.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_PRIORITIZE],
            )
            pri_rel = str((r2d.get("execution_detail") or {}).get("prioritization_latest_path") or "")
            self.assertTrue(pri_rel.startswith("runs/experiments/prioritization/latest/"))
            if int((r2d.get("execution_detail") or {}).get("ranked_count") or 0) > 0:
                r2e = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CREATE)
                self.assertEqual(r2e.get("action_status"), "executed", msg=r2e.get("execution_error"))
                self.assertEqual(
                    (r2e.get("execution_detail") or {}).get("schema"),
                    _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_CREATE],
                )
            save_experiment(
                root,
                Experiment(
                    id="exp_p2_schema_eval",
                    product_id=pid,
                    hypothesis="parity_eval",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_iso,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now_iso,
                ),
            )
            r2act = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_ACTIVATE)
            self.assertEqual(r2act.get("action_status"), "executed", msg=r2act.get("execution_error"))
            self.assertEqual(
                (r2act.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_ACTIVATE],
            )
            self.assertEqual((r2act.get("execution_detail") or {}).get("previous_status"), "proposed")
            self.assertEqual((r2act.get("execution_detail") or {}).get("new_status"), "active")
            r2ev = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_EVALUATE)
            self.assertEqual(r2ev.get("action_status"), "executed", msg=r2ev.get("execution_error"))
            self.assertEqual(
                (r2ev.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_EVALUATE],
            )
            old_iso = (_orch_fresh_now() - timedelta(days=120)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_p2_schema_staleonly",
                    product_id=pid,
                    hypothesis="stale_only",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=old_iso,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=old_iso,
                ),
            )
            r2cs = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_CLOSE_STALE)
            self.assertEqual(r2cs.get("action_status"), "executed", msg=r2cs.get("execution_error"))
            self.assertEqual(
                (r2cs.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_CLOSE_STALE],
            )
            self.assertIn(
                "exp_p2_schema_staleonly",
                (r2cs.get("execution_detail") or {}).get("affected_experiment_ids") or [],
            )
            r2sf = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r2sf.get("action_status"), "executed", msg=r2sf.get("execution_error"))
            self.assertEqual(
                (r2sf.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_EXPERIMENTS_SURFACE_FINDINGS],
            )
            sf_count = int((r2sf.get("execution_detail") or {}).get("surfaced_finding_count") or 0)
            self.assertGreaterEqual(sf_count, 2)
            p_dec = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p_surf = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
            p_ideas = root / "runs" / "ideas" / "latest.json"
            raw_d = json.loads(p_dec.read_text(encoding="utf-8"))
            raw_sf = json.loads(p_surf.read_text(encoding="utf-8"))
            raw_i = json.loads(p_ideas.read_text(encoding="utf-8"))
            td = datetime.fromisoformat(str(raw_d["generated_at_utc"]).replace("Z", "+00:00"))
            ti = datetime.fromisoformat(str(raw_i["generated_at_utc"]).replace("Z", "+00:00"))
            tmax = max(td, ti)
            raw_sf["generated_at_utc"] = (tmax + timedelta(seconds=5)).isoformat()
            p_surf.write_text(dumps_json(raw_sf) + "\n", encoding="utf-8")
            r2dr = execute_orchestration_action(root, pid, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r2dr.get("action_status"), "executed", msg=r2dr.get("execution_error"))
            self.assertEqual(
                (r2dr.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS],
            )
            self.assertGreaterEqual(int((r2dr.get("execution_detail") or {}).get("surfaced_findings_used_count") or 0), 1)
            r2ir = execute_orchestration_action(root, pid, ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r2ir.get("action_status"), "executed", msg=r2ir.get("execution_error"))
            self.assertEqual(
                (r2ir.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS],
            )
            # escalation_packet_generate — findings + audit gap (structural posture)
            (root / "runs" / "audit" / pid).mkdir(parents=True, exist_ok=True)
            (root / "runs" / "audit" / pid / "bundle.json").write_text(
                dumps_json(
                    {
                        "schema": "argus.audit_bundle.v1",
                        "product_id": pid,
                        "generated_at_utc": now_iso,
                        "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            r3 = execute_orchestration_action(root, pid, ACTION_ESCALATION_PACKET_GENERATE)
            self.assertEqual(r3.get("action_status"), "executed", msg=r3.get("execution_error"))
            self.assertEqual(
                (r3.get("execution_detail") or {}).get("schema"),
                _EXPECTED_SUCCESS_SCHEMA_BY_ACTION[ACTION_ESCALATION_PACKET_GENERATE],
            )
            rel = str((r3.get("execution_detail") or {}).get("escalation_latest_path") or "")
            self.assertTrue(rel.startswith("runs/escalations/latest/"))
            data = json.loads((root / rel).read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), ESCALATION_PACKET_SCHEMA)

    def test_phase2_chain_reorder_signals_before_findings(self) -> None:
        ctx = SimpleNamespace(deduped=[], eligible_actions_order_rule=None)
        ctx.deduped = [
            EligibleAction(ACTION_AUDIT_RUN, "audit", ()),
            EligibleAction(ACTION_FINDINGS_GENERATE, "f", ()),
            EligibleAction(ACTION_SIGNALS_COLLECT, "s", ()),
        ]
        out = _state_hygiene_order_phase2_observe_interpret_decide_govern(ctx)
        ids = [a.action_id for a in out]
        self.assertEqual(
            ids,
            [ACTION_AUDIT_RUN, ACTION_SIGNALS_COLLECT, ACTION_FINDINGS_GENERATE],
        )
        self.assertEqual(
            ctx.eligible_actions_order_rule,
            ELIGIBLE_ORDER_RULE_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN,
        )

    def test_phase2_chain_reorder_decisions_ideas_refinement_idea_before_escalation_packet(self) -> None:
        ctx = SimpleNamespace(deduped=[], eligible_actions_order_rule=None)
        ctx.deduped = [
            EligibleAction(ACTION_ESCALATION_PACKET_GENERATE, "e", ()),
            EligibleAction(ACTION_REFINEMENT_START_IDEA, "r", ()),
            EligibleAction(ACTION_PLANNING_REFRESH_FROM_STRATEGY, "pl", ()),
            EligibleAction(ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION, "st", ()),
            EligibleAction(ACTION_EXPERIMENTS_SURFACE_FINDINGS, "sf", ()),
            EligibleAction(ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS, "ir", ()),
            EligibleAction(ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS, "dr", ()),
            EligibleAction(ACTION_EXPERIMENTS_CLOSE_STALE, "xcs", ()),
            EligibleAction(ACTION_EXPERIMENTS_EVALUATE, "v", ()),
            EligibleAction(ACTION_EXPERIMENTS_ACTIVATE, "a", ()),
            EligibleAction(ACTION_EXPERIMENTS_CREATE, "c", ()),
            EligibleAction(ACTION_EXPERIMENTS_PRIORITIZE, "z", ()),
            EligibleAction(ACTION_EXPERIMENTS_PROPOSE, "x", ()),
            EligibleAction(ACTION_IDEAS_GENERATE, "i", ()),
            EligibleAction(ACTION_DECISIONS_GENERATE, "d", ()),
        ]
        out = _state_hygiene_order_phase2_observe_interpret_decide_govern(ctx)
        self.assertEqual(
            [a.action_id for a in out],
            [
                ACTION_DECISIONS_GENERATE,
                ACTION_IDEAS_GENERATE,
                ACTION_EXPERIMENTS_PROPOSE,
                ACTION_EXPERIMENTS_PRIORITIZE,
                ACTION_EXPERIMENTS_CREATE,
                ACTION_EXPERIMENTS_ACTIVATE,
                ACTION_EXPERIMENTS_EVALUATE,
                ACTION_EXPERIMENTS_CLOSE_STALE,
                ACTION_EXPERIMENTS_SURFACE_FINDINGS,
                ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
                ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
                ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
                ACTION_PLANNING_REFRESH_FROM_STRATEGY,
                ACTION_REFINEMENT_START_IDEA,
                ACTION_ESCALATION_PACKET_GENERATE,
            ],
        )
        self.assertEqual(
            ctx.eligible_actions_order_rule,
            ELIGIBLE_ORDER_RULE_PHASE2_OBSERVE_INTERPRET_DECIDE_GOVERN,
        )

    def test_phase2_chain_orders_refinement_run_before_ideas_subchain(self) -> None:
        """Non-phase2 actions (e.g. ``refinement_run``) stay before the first phase-2 member; ideas subchain orders."""
        ctx = SimpleNamespace(deduped=[], eligible_actions_order_rule=None)
        ctx.deduped = [
            EligibleAction(ACTION_REFINEMENT_RUN, "run", ()),
            EligibleAction(ACTION_REFINEMENT_START_IDEA, "r", ()),
            EligibleAction(ACTION_IDEAS_GENERATE, "i", ()),
        ]
        out = _state_hygiene_order_phase2_observe_interpret_decide_govern(ctx)
        self.assertEqual(
            [a.action_id for a in out],
            [ACTION_REFINEMENT_RUN, ACTION_IDEAS_GENERATE, ACTION_REFINEMENT_START_IDEA],
        )

    def test_phase2_chain_noop_when_already_canonical(self) -> None:
        ctx = SimpleNamespace(deduped=[], eligible_actions_order_rule=None)
        ctx.deduped = [
            EligibleAction(ACTION_SIGNALS_COLLECT, "s", ()),
            EligibleAction(ACTION_FINDINGS_GENERATE, "f", ()),
        ]
        out = _state_hygiene_order_phase2_observe_interpret_decide_govern(ctx)
        self.assertEqual(out, ctx.deduped)
        self.assertIsNone(ctx.eligible_actions_order_rule)

    def test_phase2_chain_single_member_no_rule(self) -> None:
        ctx = SimpleNamespace(deduped=[], eligible_actions_order_rule=None)
        ctx.deduped = [EligibleAction(ACTION_FINDINGS_GENERATE, "f", ())]
        out = _state_hygiene_order_phase2_observe_interpret_decide_govern(ctx)
        self.assertEqual(out, ctx.deduped)
        self.assertIsNone(ctx.eligible_actions_order_rule)

    def test_evaluate_phase2_chain_order_without_execution_feedback(self) -> None:
        """State hygiene (incl. Phase-2 chain) runs before feedback deprioritize — canonical order on merge."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_p2_order"
            now = datetime.now(timezone.utc)
            now_s = now.isoformat()
            _minimal_product(root, pid)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now_s,
                    "records": [],
                },
            )
            _write(
                root / "runs" / "findings" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.findings_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_s,
                    "repo_root": str(root),
                    "finding_count": 0,
                    "findings": [],
                },
            )
            _write(
                root / "runs" / "decisions" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.decisions_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_s,
                    "repo_root": str(root),
                    "decisions": [],
                },
            )
            _write(
                root / "runs" / "audit" / pid / "bundle.json",
                {
                    "schema": "argus.audit_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_s,
                    "angles": {"product_gap": {"angle_status": "stub", "summary_lines": ["stub"]}},
                },
            )
            _write_ideas_latest_bundle(
                root,
                pid,
                [_idea_row("idea_ord", "o", product_id=pid)],
                now_iso=now_s,
            )
            _write(
                root / "runs" / "experiments" / "proposals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.experiment_proposals_run.v1",
                    "generated_at_utc": now_s,
                    "repo_root": str(root),
                    "proposals": [
                        {
                            "proposal_id": "pr_p2_order",
                            "product_id": pid,
                            "hypothesis": "chain order",
                            "type": "growth",
                            "description": "d",
                            "expected_outcome": "e",
                            "success_metrics": [],
                            "estimated_effort": "small",
                            "confidence": 0.55,
                            "rationale": "r",
                            "schema": "argus.experiment_proposal.v1",
                        }
                    ],
                },
            )
            rp = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_PRIORITIZE)
            self.assertEqual(rp.get("action_status"), "executed", msg=rp.get("execution_error"))
            save_experiment(
                root,
                Experiment(
                    id="exp_p2_order",
                    product_id=pid,
                    hypothesis="chain_order",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            old_s = (now - timedelta(days=120)).isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_p2_order_stale",
                    product_id=pid,
                    hypothesis="chain_stale",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=old_s,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=old_s,
                ),
            )
            save_experiment(
                root,
                Experiment(
                    id="exp_p2_order_surf",
                    product_id=pid,
                    hypothesis="surf_eligible",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            exps_all = list_experiments(root, product_id=pid)
            sf_rows = build_surfaced_findings(exps_all, pid)
            self.assertTrue(sf_rows)
            save_experiment_surfaced_bundle(root, pid, sf_rows)
            p_surf = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
            raw_sf = json.loads(p_surf.read_text(encoding="utf-8"))
            raw_sf["generated_at_utc"] = (now + timedelta(seconds=30)).isoformat()
            p_surf.write_text(dumps_json(raw_sf) + "\n", encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
        ids = [a["action_id"] for a in st["eligible_actions"]]
        self.assertLess(ids.index(ACTION_FINDINGS_GENERATE), ids.index(ACTION_DECISIONS_GENERATE))
        self.assertLess(ids.index(ACTION_DECISIONS_GENERATE), ids.index(ACTION_IDEAS_GENERATE))
        self.assertLess(ids.index(ACTION_IDEAS_GENERATE), ids.index(ACTION_EXPERIMENTS_PROPOSE))
        self.assertLess(ids.index(ACTION_EXPERIMENTS_PROPOSE), ids.index(ACTION_EXPERIMENTS_PRIORITIZE))
        self.assertLess(ids.index(ACTION_EXPERIMENTS_PRIORITIZE), ids.index(ACTION_EXPERIMENTS_CREATE))
        self.assertLess(ids.index(ACTION_EXPERIMENTS_CREATE), ids.index(ACTION_EXPERIMENTS_ACTIVATE))
        self.assertLess(ids.index(ACTION_EXPERIMENTS_ACTIVATE), ids.index(ACTION_EXPERIMENTS_EVALUATE))
        self.assertLess(ids.index(ACTION_EXPERIMENTS_EVALUATE), ids.index(ACTION_EXPERIMENTS_CLOSE_STALE))
        self.assertLess(ids.index(ACTION_EXPERIMENTS_CLOSE_STALE), ids.index(ACTION_EXPERIMENTS_SURFACE_FINDINGS))
        self.assertLess(
            ids.index(ACTION_EXPERIMENTS_SURFACE_FINDINGS),
            ids.index(ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS),
        )
        self.assertLess(
            ids.index(ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS),
            ids.index(ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS),
        )
        self.assertLess(ids.index(ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS), ids.index(ACTION_REFINEMENT_START_IDEA))
        if ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION in ids:
            self.assertLess(
                ids.index(ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS),
                ids.index(ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION),
            )
            self.assertLess(
                ids.index(ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION),
                ids.index(ACTION_REFINEMENT_START_IDEA),
            )
        if ACTION_PLANNING_REFRESH_FROM_STRATEGY in ids:
            self.assertLess(
                ids.index(ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS),
                ids.index(ACTION_PLANNING_REFRESH_FROM_STRATEGY),
            )
            self.assertLess(ids.index(ACTION_PLANNING_REFRESH_FROM_STRATEGY), ids.index(ACTION_REFINEMENT_START_IDEA))
        self.assertLess(ids.index(ACTION_REFINEMENT_START_IDEA), ids.index(ACTION_ESCALATION_PACKET_GENERATE))
        self.assertTrue(st["eligibility_facts"].get("ideas_generate_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_propose_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_prioritize_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_create_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_activate_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_evaluate_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_close_stale_eligible"))
        self.assertTrue(st["eligibility_facts"].get("experiments_surface_findings_eligible"))
        self.assertTrue(st["eligibility_facts"].get("decisions_refresh_from_surfaced_findings_eligible"))
        self.assertTrue(st["eligibility_facts"].get("ideas_refresh_from_surfaced_findings_eligible"))
        self.assertTrue(st["eligibility_facts"].get("refinement_start_idea_eligible"))
        exp_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_PROPOSE)
        self.assertIn(RC_EXPERIMENTS_PROPOSE_READY, exp_row.get("reason_codes") or [])
        pri_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_PRIORITIZE)
        self.assertIn(RC_EXPERIMENTS_PRIORITIZE_READY, pri_row.get("reason_codes") or [])
        cre_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_CREATE)
        self.assertIn(RC_EXPERIMENTS_CREATE_READY, cre_row.get("reason_codes") or [])
        act_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_ACTIVATE)
        self.assertIn(RC_EXPERIMENTS_ACTIVATE_READY, act_row.get("reason_codes") or [])
        ev_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_EVALUATE)
        self.assertIn(RC_EXPERIMENTS_EVALUATE_READY, ev_row.get("reason_codes") or [])
        cs_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_CLOSE_STALE)
        self.assertIn(RC_EXPERIMENTS_CLOSE_STALE_READY, cs_row.get("reason_codes") or [])
        sf_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_EXPERIMENTS_SURFACE_FINDINGS)
        self.assertIn(RC_EXPERIMENTS_SURFACE_FINDINGS_READY, sf_row.get("reason_codes") or [])
        dr_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
        self.assertIn(RC_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS_READY, dr_row.get("reason_codes") or [])
        ir_row = next(a for a in st["eligible_actions"] if a["action_id"] == ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS)
        self.assertIn(RC_IDEAS_REFRESH_FROM_SURFACED_FINDINGS_READY, ir_row.get("reason_codes") or [])


if __name__ == "__main__":
    unittest.main()
