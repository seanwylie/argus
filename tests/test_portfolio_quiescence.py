"""Portfolio quiescence (``argus.portfolio_quiescence.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.operator_snapshot import (
    OPERATOR_SNAPSHOT_SCHEMA,
    operator_snapshot_json_path,
)
from argus.orchestrator.state_models import ORCH_STATUS_BLOCKED_WAITING_INPUT, ORCH_STATUS_ELIGIBLE
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    operator_queue_output_dir,
    write_operator_queue,
)
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir
from argus.portfolio.quiescence import (
    PORTFOLIO_QUIESCENCE_SCHEMA,
    evaluate_portfolio_quiescence,
    portfolio_quiescence_dir,
    run_portfolio_quiescence,
)


def _product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: Q
owner:
  team: test
lifecycle:
  stage: validate
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


def _write_snap(root: Path, pid: str, **kwargs: object) -> None:
    body: dict = {
        "schema": OPERATOR_SNAPSHOT_SCHEMA,
        "product_id": pid,
        "readiness": {"readiness_tier": "advance_ready", "understanding_debt": 0.1},
        "import_health": {"first_pass_status": "success", "gating_tier": "success"},
        "artifact_freshness_summary": {"bundles": {}, "eligibility_flags": {}},
        "waiting_and_blocking": {
            "waiting_inputs": [],
            "orchestration_status": ORCH_STATUS_ELIGIBLE,
            "blockers": [],
        },
        "decision_summary": {"top_decision_confidence": 0.75},
        "next_action": "none",
        "next_action_policy": {"action_family": "none"},
        "operator_recommendation": "ok",
    }
    body.update(kwargs)
    p = operator_snapshot_json_path(root, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(body), encoding="utf-8")


def _minimal_progression(path: Path, *, run_id: str, products: list[dict]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": PORTFOLIO_PROGRESSION_SCHEMA,
                "run_id": run_id,
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "products": products,
            }
        ),
        encoding="utf-8",
    )


class TestPortfolioQuiescence(unittest.TestCase):
    def test_no_prior_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "only")
            _write_snap(root, "only")
            write_operator_queue(root)
            out = evaluate_portfolio_quiescence(root)
            self.assertEqual(out["schema"], PORTFOLIO_QUIESCENCE_SCHEMA)
            self.assertIn("quiescence.no_prior_baseline", out["quiescence_reason_codes"])
            self.assertEqual(out["recommendation"], "run_again")
            self.assertFalse(out["portfolio_quiescent"])

    def test_fully_stable_portfolio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "a")
            _write_snap(root, "a")
            write_operator_queue(root)
            run_portfolio_quiescence(root, write_artifacts=True)
            out2 = evaluate_portfolio_quiescence(root)
            self.assertTrue(out2["portfolio_quiescent"])
            self.assertEqual(out2["recommendation"], "wait")
            self.assertEqual(out2["products_with_material_change"], [])

    def test_products_still_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "blk")
            _write_snap(
                root,
                "blk",
                waiting_and_blocking={
                    "waiting_inputs": [{"kind": "k"}],
                    "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                    "blockers": [],
                },
                next_action="none",
            )
            write_operator_queue(root)
            run_portfolio_quiescence(root, write_artifacts=True)
            out = evaluate_portfolio_quiescence(root)
            self.assertIn("blk", out["products_still_blocked"])

    def test_one_product_newly_actionable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "p1")
            _write_snap(
                root,
                "p1",
                waiting_and_blocking={
                    "waiting_inputs": [{"kind": "k"}],
                    "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                    "blockers": [],
                },
                next_action="none",
            )
            write_operator_queue(root)
            run_portfolio_quiescence(root, write_artifacts=True)
            _write_snap(
                root,
                "p1",
                waiting_and_blocking={
                    "waiting_inputs": [],
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "blockers": [],
                },
                next_action="audit_run",
            )
            write_operator_queue(root)
            out = evaluate_portfolio_quiescence(root)
            self.assertIn("p1", out["products_newly_actionable"])
            self.assertEqual(out["recommendation"], "run_again")

    def test_repeated_blocked_progression(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "w")
            _write_snap(root, "w", next_action="none")
            write_operator_queue(root)
            prog = portfolio_progression_dir(root)
            prog.mkdir(parents=True, exist_ok=True)
            row = {"product_id": "w", "outcome": "blocked_waiting"}
            _minimal_progression(
                prog / "20260101T000000Z.json",
                run_id="r1",
                products=[row],
            )
            _minimal_progression(
                prog / "20260102T000000Z.json",
                run_id="r2",
                products=[row],
            )
            (prog / "latest.json").write_text((prog / "20260102T000000Z.json").read_text(), encoding="utf-8")
            prior_path = portfolio_quiescence_dir(root) / "latest.json"
            prior_path.parent.mkdir(parents=True, exist_ok=True)
            prior_path.write_text(
                json.dumps(
                    {
                        "schema": PORTFOLIO_QUIESCENCE_SCHEMA,
                        "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                        "baseline_for_next_run": {
                            "per_product": {
                                "w": {
                                    "product_id": "w",
                                    "queue_rank": 1,
                                    "priority_score": 100.0,
                                    "readiness_tier": "advance_ready",
                                    "next_action": "none",
                                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                                    "waiting_inputs_count": 0,
                                    "blockers_count": 0,
                                    "top_decision_confidence": 0.75,
                                    "first_pass_status": "success",
                                    "gating_tier": "success",
                                }
                            },
                            "queue_product_order": ["w"],
                        },
                    }
                ),
                encoding="utf-8",
            )
            out = evaluate_portfolio_quiescence(root)
            self.assertIn("quiescence.progression_stuck_repeat", out["quiescence_reason_codes"])
            self.assertEqual(out["recommendation"], "human_review")
            self.assertEqual(len(out["products_stuck_or_repeating"]), 1)

    def test_queue_reshuffle_material_change(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("r1", "r2", "r3"):
                _product(root, pid)
                _write_snap(root, pid)
            qdir = operator_queue_output_dir(root)
            qdir.mkdir(parents=True, exist_ok=True)
            base_entries = [
                {
                    "product_id": "r1",
                    "queue_rank": 1,
                    "priority_score": 300.0,
                    "readiness_tier": "advance_ready",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "none",
                },
                {
                    "product_id": "r2",
                    "queue_rank": 2,
                    "priority_score": 200.0,
                    "readiness_tier": "advance_ready",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "none",
                },
                {
                    "product_id": "r3",
                    "queue_rank": 3,
                    "priority_score": 100.0,
                    "readiness_tier": "advance_ready",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "none",
                },
            ]
            q1 = {
                "schema": OPERATOR_QUEUE_SCHEMA,
                "generated_at_utc": "2026-01-01T00:00:00+00:00",
                "scoring_weights_version": "1",
                "entries": base_entries,
            }
            (qdir / "latest.json").write_text(json.dumps(q1), encoding="utf-8")
            run_portfolio_quiescence(root, write_artifacts=True)
            reshuffled = [
                {
                    "product_id": "r1",
                    "queue_rank": 3,
                    "priority_score": 100.0,
                    "readiness_tier": "advance_ready",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "none",
                },
                {
                    "product_id": "r2",
                    "queue_rank": 2,
                    "priority_score": 200.0,
                    "readiness_tier": "advance_ready",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "none",
                },
                {
                    "product_id": "r3",
                    "queue_rank": 1,
                    "priority_score": 300.0,
                    "readiness_tier": "advance_ready",
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "none",
                },
            ]
            q2 = dict(q1)
            q2["entries"] = reshuffled
            q2["generated_at_utc"] = "2026-01-02T00:00:00+00:00"
            (qdir / "latest.json").write_text(json.dumps(q2), encoding="utf-8")
            out = evaluate_portfolio_quiescence(root)
            self.assertNotEqual(out["queue_rank_changes"], [])
            codes = {c for row in out["products_with_material_change"] for c in row.get("codes", [])}
            self.assertTrue(
                any("queue_rank_shift" in c for c in codes)
                or any("priority_score_shift" in c for c in codes)
            )
            self.assertEqual(out["recommendation"], "run_again")


if __name__ == "__main__":
    unittest.main()
