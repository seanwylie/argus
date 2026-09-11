"""Portfolio operator queue (``argus.operator_queue.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA
from argus.orchestrator.operator_snapshot import (
    OPERATOR_SNAPSHOT_SCHEMA,
    operator_snapshot_json_path,
)
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    build_operator_queue_payload,
    load_operator_view,
    write_operator_queue,
)
from argus.products.apply_signal_instrumentation import PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA
from argus.products.signal_instrumentation import PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA


def _product(root: Path, pid: str, *, stage: str = "validate") -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: QueueTest
owner:
  team: test
lifecycle:
  stage: {stage}
metrics:
  local_paths: [metrics/]
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
    (pr / "scripts").mkdir(parents=True)
    for n in ("start.sh", "stop.sh", "analyze.sh"):
        (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")


def _write_snap(root: Path, pid: str, body: dict) -> None:
    p = operator_snapshot_json_path(root, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _write_inst_artifact(root: Path, pid: str, *, status: str) -> None:
    d = root / "runs" / "products" / "signal_instrumentation" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA,
        "product_id": pid,
        "evaluated_at_utc": "2026-01-01T00:00:00Z",
        "ok": True,
        "instrumentation_status": status,
        "signal_coverage_assessment": "test",
        "missing_signal_dimensions": [],
        "proposed_signal_contract": {},
        "synthetic_seed_signals": [],
        "observability_notes": [],
        "recommended_next_step": "x",
    }
    (d / f"{pid}.json").write_text(json.dumps(pl), encoding="utf-8")


def _write_apply_artifact(
    root: Path,
    pid: str,
    *,
    apply_status: str,
    post_instrumentation_status: str,
) -> None:
    d = root / "runs" / "products" / "signal_instrumentation_apply" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA,
        "product_id": pid,
        "work_order_id": "wo_q",
        "execution_id": f"ex_{pid}",
        "applied_at_utc": "2026-01-01T00:00:00Z",
        "apply_status": apply_status,
        "files_written": [],
        "files_updated": [],
        "contract_applied": {},
        "signal_dimensions_applied": [],
        "validation_summary": "test",
        "notes": "",
        "post_apply": {
            "instrumentation_status": post_instrumentation_status,
            "ok": post_instrumentation_status == "adequate",
            "missing_signal_dimensions": [],
        },
    }
    (d / f"{pid}.json").write_text(json.dumps(pl), encoding="utf-8")


def _band_test_snap(pid: str) -> dict:
    """Minimal operator snapshot so queue rows are built from snapshots (not fallback-only)."""
    return {
        "schema": OPERATOR_SNAPSHOT_SCHEMA,
        "product_id": pid,
        "readiness": {"readiness_tier": "observe_gap", "understanding_debt": 0.3},
        "import_health": {"first_pass_status": "success"},
        "artifact_freshness_summary": {
            "bundles": {"signals": {"phase": "present", "staleness": "fresh"}},
            "eligibility_flags": {},
        },
        "waiting_and_blocking": {
            "waiting_inputs": [],
            "orchestration_status": "eligible",
            "blockers": [],
        },
        "decision_summary": {"top_decision_confidence": 0.7},
        "next_action": "none",
        "next_action_policy": {"action_family": "none"},
        "operator_recommendation": "ok",
    }


class TestOperatorQueue(unittest.TestCase):
    def test_mature_ready_ranks_lower_than_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid, stage in (
                ("q_mature", "maintain"),
                ("q_blocked", "grow"),
            ):
                _product(root, pid, stage=stage)
            _write_snap(
                root,
                "q_mature",
                {
                    "schema": OPERATOR_SNAPSHOT_SCHEMA,
                    "product_id": "q_mature",
                    "readiness": {"readiness_tier": "advance_ready", "understanding_debt": 0.05},
                    "import_health": {"first_pass_status": "success"},
                    "artifact_freshness_summary": {
                        "bundles": {"signals": {"phase": "present", "staleness": "fresh"}},
                        "eligibility_flags": {},
                    },
                    "waiting_and_blocking": {
                        "waiting_inputs": [],
                        "orchestration_status": "eligible",
                        "blockers": [],
                    },
                    "decision_summary": {"top_decision_confidence": 0.92},
                    "next_action": "none",
                    "next_action_policy": {"action_family": "none"},
                    "operator_recommendation": "mature",
                },
            )
            _write_snap(
                root,
                "q_blocked",
                {
                    "schema": OPERATOR_SNAPSHOT_SCHEMA,
                    "product_id": "q_blocked",
                    "readiness": {"readiness_tier": "interpret_gap", "understanding_debt": 0.4},
                    "import_health": {"first_pass_status": "success"},
                    "artifact_freshness_summary": {"bundles": {}, "eligibility_flags": {}},
                    "waiting_and_blocking": {
                        "waiting_inputs": [{"kind": "refinement_grounded_input"}],
                        "orchestration_status": "blocked_waiting_input",
                        "blockers": [],
                    },
                    "decision_summary": {"top_decision_confidence": 0.8},
                    "next_action": "none",
                    "next_action_policy": {"action_family": "refinement"},
                    "operator_recommendation": "blocked",
                },
            )
            pl = build_operator_queue_payload(root)
            self.assertEqual(pl["schema"], OPERATOR_QUEUE_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            by_id = {e["product_id"]: e for e in pl["entries"]}
            self.assertGreater(
                by_id["q_blocked"]["priority_score"],
                by_id["q_mature"]["priority_score"],
            )
            self.assertLess(by_id["q_blocked"]["queue_rank"], by_id["q_mature"]["queue_rank"])

    def test_partial_import_scores_high(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "q_partial", stage="build")
            _write_snap(
                root,
                "q_partial",
                {
                    "schema": OPERATOR_SNAPSHOT_SCHEMA,
                    "product_id": "q_partial",
                    "readiness": {"readiness_tier": "import_incomplete", "understanding_debt": 0.5},
                    "import_health": {"first_pass_status": "partial"},
                    "artifact_freshness_summary": {"bundles": {}, "eligibility_flags": {}},
                    "waiting_and_blocking": {
                        "waiting_inputs": [],
                        "orchestration_status": "eligible",
                        "blockers": [],
                    },
                    "decision_summary": {},
                    "next_action": "findings_generate",
                    "next_action_policy": {"action_family": "generation_chain"},
                    "operator_recommendation": "partial",
                },
            )
            pl = build_operator_queue_payload(root)
            e = next(x for x in pl["entries"] if x["product_id"] == "q_partial")
            self.assertIn("first-pass", e["priority_reason"].lower())
            self.assertGreater(e["priority_score"], 80.0)

    def test_stale_but_high_value_product(self) -> None:
        """Advance-ready tier but stale observability — still attracts attention."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "q_stale", stage="grow")
            _write_snap(
                root,
                "q_stale",
                {
                    "schema": OPERATOR_SNAPSHOT_SCHEMA,
                    "product_id": "q_stale",
                    "readiness": {"readiness_tier": "advance_ready", "understanding_debt": 0.15},
                    "import_health": {"first_pass_status": "success"},
                    "artifact_freshness_summary": {
                        "bundles": {"signals": {"phase": "present", "staleness": "stale"}},
                        "eligibility_flags": {
                            "signals_collection_time_stale": True,
                            "signals_refresh_needed": True,
                        },
                    },
                    "waiting_and_blocking": {
                        "waiting_inputs": [],
                        "orchestration_status": "stale_refresh_needed",
                        "blockers": [],
                    },
                    "decision_summary": {"top_decision_confidence": 0.88},
                    "next_action": "signals_collect",
                    "next_action_policy": {"action_family": "observability"},
                    "operator_recommendation": "refresh signals",
                },
            )
            pl = build_operator_queue_payload(root)
            e = next(x for x in pl["entries"] if x["product_id"] == "q_stale")
            self.assertIn("signals", e["priority_reason"].lower())
            self.assertGreater(e["priority_score"], 60.0)

    def test_missing_snapshot_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "q_nosnap", stage="idea")
            pl = build_operator_queue_payload(root)
            e = next(x for x in pl["entries"] if x["product_id"] == "q_nosnap")
            self.assertEqual(e["operator_view_source"], "none")
            self.assertIn("orchestration state", e["recommendation"])
            self.assertIn("no_operator_artifact", e["priority_score_breakdown"])
            self.assertIn("signal_contract_operability_status", e)
            self.assertIn("signal_contract_hint", e)
            self.assertIn("signal_contract_signals_surface_state", e)

    def test_orchestration_latest_fallback(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "q_orch", stage="validate")
            orch = root / "runs" / "orchestration" / "latest"
            orch.mkdir(parents=True)
            orch_file = orch / "q_orch.json"
            orch_file.write_text(
                json.dumps(
                    {
                        "schema": "argus.orchestration_state.v1",
                        "product_id": "q_orch",
                        "evaluated_at_utc": "2026-01-01T00:00:00Z",
                        "import_health": {"first_pass_status": "success"},
                        "readiness_reason": "ok",
                        "orchestration_status": "eligible",
                        "orchestration_status_reason": "x",
                        "orchestration_status_reason_codes": [],
                        "waiting_inputs": [],
                        "overall_status": "eligible",
                        "artifacts": {
                            "signals": {"phase": "present", "staleness": "fresh", "collected_at_utc": None, "path": None},
                            "temporal": {"phase": "absent", "staleness": "absent", "worst_freshness_status": None, "collected_at_utc": None, "path": None},
                            "audit": {"phase": "absent", "staleness": "fresh", "generated_at_utc": None, "path": None, "angle_status": {}},
                            "execution": {"phase": "none", "record_count": 0, "path": None, "pending_approvals": []},
                            "product_spec": {"phase": "absent", "session_id": None, "status": None, "current_round": None},
                            "implementation_plan": {"phase": "absent", "session_id": None, "status": None, "current_round": None, "stale_vs_product_spec": False},
                            "refinement": {"review_state": "absent", "idea": {"phase": "absent"}, "primary_sessions": [], "allowed_review_states": []},
                        },
                        "eligibility_facts": {"import_readiness_tier": "success", "signals_collection_time_stale": False, "signals_refresh_needed": False, "temporal_freshness_stale": False, "audit_product_gap_incomplete": True, "audit_security_stub": False},
                        "eligible_actions": [],
                        "blockers": [],
                        "next_action": "none",
                        "next_action_policy": {"schema": "argus.next_action_policy.v1", "rule_applied": "x", "ordering_basis": "y", "tie_break": "z", "influences": {}, "planning": {}, "action_family": "none"},
                        "readiness": {
                            "schema": "argus.orchestration_readiness.v1",
                            "readiness_tier": "interpret_gap",
                            "understanding_debt": 0.3,
                            "confidence_gate": "caution",
                            "readiness_reason_codes": [],
                            "readiness_policy_hint": "close_audit_gaps",
                            "progression_quiescence": False,
                            "metrics": {"top_decision_confidence": None, "advisor_conflict": False, "confidence_threshold": 0.45},
                        },
                    }
                ),
                encoding="utf-8",
            )
            view, src = load_operator_view(root, "q_orch")
            self.assertEqual(src, "orchestration_latest")
            self.assertIsNotNone(view)
            pl = build_operator_queue_payload(root)
            e = next(x for x in pl["entries"] if x["product_id"] == "q_orch")
            self.assertEqual(e["operator_view_source"], "orchestration_latest")

    def test_write_outputs(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "q_w", stage="idea")
            jpath, mpath = write_operator_queue(root)
            self.assertTrue(jpath.name == "latest.json")
            self.assertTrue(mpath.name == "latest.md")
            data = json.loads(jpath.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], OPERATOR_QUEUE_SCHEMA)

    def test_signal_instrumentation_band_needs_followup_post_apply_adequate(self) -> None:
        """Pins ``signal_instrumentation_band`` for the three load-bearing queue states (read-only fixtures)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("q_need", "q_follow", "q_resolved"):
                _product(root, pid, stage="validate")
                _write_snap(root, pid, _band_test_snap(pid))
            _write_inst_artifact(root, "q_need", status="weak")
            _write_inst_artifact(root, "q_follow", status="sparse")
            _write_apply_artifact(
                root,
                "q_follow",
                apply_status="partial",
                post_instrumentation_status="weak",
            )
            _write_inst_artifact(root, "q_resolved", status="missing")
            _write_apply_artifact(
                root,
                "q_resolved",
                apply_status="success",
                post_instrumentation_status="adequate",
            )
            pl = build_operator_queue_payload(root)
            by_id = {e["product_id"]: e for e in pl["entries"]}
            self.assertEqual(by_id["q_need"]["signal_instrumentation_band"], "needs_instrumentation")
            self.assertTrue(by_id["q_need"]["signal_instrumentation_pressure"])
            self.assertEqual(by_id["q_follow"]["signal_instrumentation_band"], "apply_followup")
            self.assertTrue(by_id["q_follow"]["signal_instrumentation_pressure"])
            self.assertEqual(by_id["q_resolved"]["signal_instrumentation_band"], "post_apply_adequate")
            self.assertFalse(by_id["q_resolved"]["signal_instrumentation_pressure"])
            ctx = pl.get("signal_instrumentation_context") or {}
            raw = set(ctx.get("products_under_instrumentation_pressure_raw") or [])
            eff = set(ctx.get("products_under_instrumentation_pressure") or [])
            self.assertEqual(raw, {"q_follow", "q_need", "q_resolved"})
            self.assertEqual(eff, {"q_follow", "q_need"})
            self.assertIn("q_follow", ctx.get("products_instrumentation_apply_followup") or [])
            self.assertIn("q_resolved", ctx.get("products_instrumentation_resolved_via_apply") or [])


if __name__ == "__main__":
    unittest.main()
