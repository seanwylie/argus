"""Tests for :mod:`argus.portfolio.intervention`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.operator_snapshot import OPERATOR_SNAPSHOT_SCHEMA
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.intervention import (
    PORTFOLIO_INTERVENTION_SCHEMA,
    RC_OSCILLATING_ACTION,
    RC_QUEUE_PROMINENCE_NO_ADVANCE,
    RC_REPEATED_BLOCKED,
    RC_TIER_DEBT_STAGNATION,
    evaluate_portfolio_intervention,
    run_portfolio_intervention,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA


def _minimal_product(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: T
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 0
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )


def _write_snapshot(
    root: Path,
    product_id: str,
    *,
    readiness_tier: str = "observe_gap",
    understanding_debt: float = 0.25,
    next_action: str = "signals_collect",
    orchestration_status: str = "eligible",
    top_decision_confidence: float | None = 0.5,
    first_pass_status: str = "success",
    gating_tier: str | None = None,
) -> None:
    p = root / "runs" / "orchestration" / "operator_snapshot" / f"{product_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    ih: dict = {"first_pass_status": first_pass_status}
    if gating_tier is not None:
        ih["gating_tier"] = gating_tier
    ds: dict = {}
    if top_decision_confidence is not None:
        ds["top_decision_confidence"] = top_decision_confidence
    payload = {
        "schema": OPERATOR_SNAPSHOT_SCHEMA,
        "product_id": product_id,
        "next_action": next_action,
        "readiness": {
            "schema": "argus.orchestration_readiness.v1",
            "readiness_tier": readiness_tier,
            "understanding_debt": understanding_debt,
        },
        "waiting_and_blocking": {
            "orchestration_status": orchestration_status,
            "waiting_inputs": [],
            "blockers": [],
        },
        "decision_summary": ds,
        "import_health": ih,
    }
    p.write_text(json.dumps(payload), encoding="utf-8")


def _write_queue(root: Path, entries: list[dict]) -> None:
    d = root / "runs" / "portfolio" / "operator_queue"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": OPERATOR_QUEUE_SCHEMA,
        "generated_at_utc": "2026-04-12T12:00:00+00:00",
        "scoring_weights_version": "1",
        "weight_ledger": {},
        "entries": entries,
    }
    (d / "latest.json").write_text(json.dumps(pl) + "\n", encoding="utf-8")


def _write_progression(root: Path, run_id: str, products: list[dict]) -> None:
    d = root / "runs" / "portfolio" / "progression"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PORTFOLIO_PROGRESSION_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": "2026-04-12T12:00:00+00:00",
        "queue_source": "latest",
        "limit": 5,
        "dry_run": False,
        "execute": True,
        "products": products,
        "summary_counts": {},
    }
    (d / f"{run_id}.json").write_text(json.dumps(pl) + "\n", encoding="utf-8")


def _write_delta(root: Path, run_id: str, per_product: dict) -> None:
    d = root / "runs" / "portfolio" / "delta_report"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": "2026-04-12T12:00:00+00:00",
        "baseline_for_next_run": {
            "evaluated_at_utc": "2026-04-12T12:00:00+00:00",
            "per_product": per_product,
            "queue_product_order": list(per_product.keys()),
        },
    }
    (d / f"{run_id}.json").write_text(json.dumps(pl) + "\n", encoding="utf-8")


class TestPortfolioIntervention(unittest.TestCase):
    def test_repeatedly_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "blk")
            _write_snapshot(root, "blk", orchestration_status="blocked_waiting_input")
            _write_queue(
                root,
                [
                    {
                        "product_id": "blk",
                        "queue_rank": 1,
                        "priority_score": 90.0,
                        "readiness_tier": "interpret_gap",
                        "next_action": "refinement_submit_reviews_in",
                        "orchestration_status": "blocked_waiting_input",
                        "understanding_debt": 0.5,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            _write_progression(
                root,
                "20260412T040000Z",
                [
                    {
                        "product_id": "blk",
                        "outcome": "blocked_waiting",
                        "next_action_after": "none",
                        "next_action_before": "none",
                    }
                ],
            )
            _write_progression(
                root,
                "20260412T050000Z",
                [
                    {
                        "product_id": "blk",
                        "outcome": "blocked_waiting",
                        "next_action_after": "none",
                        "next_action_before": "none",
                    }
                ],
            )
            r = evaluate_portfolio_intervention(root)
            self.assertEqual(r["schema"], PORTFOLIO_INTERVENTION_SCHEMA)
            rows = {x["product_id"]: x for x in r["flagged_products"] if isinstance(x, dict)}
            self.assertIn(RC_REPEATED_BLOCKED, rows["blk"]["detection_reason_codes"])

    def test_oscillating_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "osc")
            _write_snapshot(root, "osc", next_action="signals_collect")
            _write_queue(
                root,
                [
                    {
                        "product_id": "osc",
                        "queue_rank": 1,
                        "priority_score": 85.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.3,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            for i, (rid, a, b) in enumerate(
                [
                    ("20260412T010000Z", "signals_collect", "findings_generate"),
                    ("20260412T020000Z", "findings_generate", "signals_collect"),
                    ("20260412T030000Z", "signals_collect", "findings_generate"),
                    ("20260412T040000Z", "findings_generate", "signals_collect"),
                ]
            ):
                _write_progression(
                    root,
                    rid,
                    [
                        {
                            "product_id": "osc",
                            "outcome": "no_action",
                            "next_action_after": b,
                            "next_action_before": a,
                        }
                    ],
                )
            r = evaluate_portfolio_intervention(root)
            codes = []
            for x in r["flagged_products"]:
                if isinstance(x, dict) and x.get("product_id") == "osc":
                    codes.extend(x.get("detection_reason_codes") or [])
            self.assertIn(RC_OSCILLATING_ACTION, codes)

    def test_repeated_partial_import(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "imp")
            _write_snapshot(root, "imp", first_pass_status="partial")
            _write_queue(
                root,
                [
                    {
                        "product_id": "imp",
                        "queue_rank": 1,
                        "priority_score": 92.0,
                        "readiness_tier": "import_incomplete",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.4,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            r = evaluate_portfolio_intervention(root)
            rows = {x["product_id"]: x for x in r["flagged_products"] if isinstance(x, dict)}
            self.assertEqual(rows["imp"]["intervention_category"], "import_repair")

    def test_high_priority_non_advancing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "hq")
            _write_snapshot(root, "hq", readiness_tier="observe_gap")
            _write_queue(
                root,
                [
                    {
                        "product_id": "hq",
                        "queue_rank": 2,
                        "priority_score": 80.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.35,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            _write_progression(
                root,
                "20260412T010000Z",
                [{"product_id": "hq", "outcome": "no_action", "next_action_after": "signals_collect", "next_action_before": "signals_collect"}],
            )
            _write_progression(
                root,
                "20260412T020000Z",
                [{"product_id": "hq", "outcome": "skipped", "next_action_after": "signals_collect", "next_action_before": "signals_collect"}],
            )
            r = evaluate_portfolio_intervention(root)
            rows = {x["product_id"]: x for x in r["flagged_products"] if isinstance(x, dict)}
            self.assertIn(RC_QUEUE_PROMINENCE_NO_ADVANCE, rows["hq"]["detection_reason_codes"])

    def test_benign_stable_not_flagged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "ok")
            _write_snapshot(
                root,
                "ok",
                readiness_tier="advance_ready",
                understanding_debt=0.2,
                next_action="none",
                top_decision_confidence=0.85,
            )
            _write_queue(
                root,
                [
                    {
                        "product_id": "ok",
                        "queue_rank": 5,
                        "priority_score": 30.0,
                        "readiness_tier": "advance_ready",
                        "next_action": "none",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.2,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            r = evaluate_portfolio_intervention(root)
            self.assertEqual(r["flagged_products"], [])
            self.assertIn("ok", r["stable_benign_products"])

    def test_chronic_vs_emerging(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "ch")
            _write_snapshot(root, "ch", orchestration_status="blocked_waiting_input")
            _write_queue(
                root,
                [
                    {
                        "product_id": "ch",
                        "queue_rank": 1,
                        "priority_score": 99.0,
                        "readiness_tier": "interpret_gap",
                        "next_action": "refinement_submit_reviews_in",
                        "orchestration_status": "blocked_waiting_input",
                        "understanding_debt": 0.5,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            for rid in ("20260412T010000Z", "20260412T020000Z", "20260412T030000Z", "20260412T040000Z"):
                _write_progression(
                    root,
                    rid,
                    [
                        {
                            "product_id": "ch",
                            "outcome": "blocked_waiting",
                            "next_action_after": "none",
                            "next_action_before": "none",
                        }
                    ],
                )
            r = evaluate_portfolio_intervention(root)
            rows = {x["product_id"]: x for x in r["flagged_products"] if isinstance(x, dict)}
            self.assertEqual(rows["ch"]["chronicity"], "chronic")

        with TemporaryDirectory() as tmp2:
            root = Path(tmp2)
            _minimal_product(root, "em")
            _write_snapshot(root, "em", orchestration_status="blocked_waiting_input")
            _write_queue(
                root,
                [
                    {
                        "product_id": "em",
                        "queue_rank": 1,
                        "priority_score": 99.0,
                        "readiness_tier": "interpret_gap",
                        "next_action": "refinement_submit_reviews_in",
                        "orchestration_status": "blocked_waiting_input",
                        "understanding_debt": 0.5,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            for rid in ("20260412T010000Z", "20260412T020000Z"):
                _write_progression(
                    root,
                    rid,
                    [
                        {
                            "product_id": "em",
                            "outcome": "blocked_waiting",
                            "next_action_after": "none",
                            "next_action_before": "none",
                        }
                    ],
                )
            r2 = evaluate_portfolio_intervention(root)
            rows2 = {x["product_id"]: x for x in r2["flagged_products"] if isinstance(x, dict)}
            self.assertEqual(rows2["em"]["chronicity"], "emerging")

    def test_thin_evidence_suppresses_tier_stagnation_when_first_pass_pending(self) -> None:
        """Unprofiled + pending first-pass should not get churn stagnation as chronic failure."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "thin")
            _write_snapshot(
                root,
                "thin",
                readiness_tier="unprofiled",
                understanding_debt=0.41,
                first_pass_status="pending",
            )
            _write_queue(
                root,
                [
                    {
                        "product_id": "thin",
                        "queue_rank": 1,
                        "priority_score": 70.0,
                        "readiness_tier": "unprofiled",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.41,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            fp = {
                "thin": {
                    "product_id": "thin",
                    "readiness_tier": "unprofiled",
                    "understanding_debt": 0.41,
                    "queue_rank": 1,
                    "priority_score": 70.0,
                }
            }
            _write_delta(root, "20260412T010000Z", fp)
            _write_delta(root, "20260412T020000Z", fp)
            _write_delta(root, "20260412T030000Z", fp)
            r = evaluate_portfolio_intervention(root)
            rows = {x["product_id"]: x for x in r["flagged_products"] if isinstance(x, dict)}
            self.assertNotIn("thin", rows)
            sup = r.get("thin_evidence_suppressions") or []
            self.assertTrue(any(x.get("product_id") == "thin" for x in sup if isinstance(x, dict)))
            thin_row = next(x for x in sup if isinstance(x, dict) and x.get("product_id") == "thin")
            self.assertIn(RC_TIER_DEBT_STAGNATION, thin_row.get("suppressed_codes") or [])

    def test_tier_debt_stagnation_delta_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "stag")
            _write_snapshot(root, "stag", readiness_tier="observe_gap", understanding_debt=0.41)
            _write_queue(
                root,
                [
                    {
                        "product_id": "stag",
                        "queue_rank": 1,
                        "priority_score": 70.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.41,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            fp = {
                "stag": {
                    "product_id": "stag",
                    "readiness_tier": "observe_gap",
                    "understanding_debt": 0.41,
                    "queue_rank": 1,
                    "priority_score": 70.0,
                }
            }
            _write_delta(root, "20260412T010000Z", fp)
            _write_delta(root, "20260412T020000Z", fp)
            _write_delta(root, "20260412T030000Z", fp)
            r = evaluate_portfolio_intervention(root)
            rows = {x["product_id"]: x for x in r["flagged_products"] if isinstance(x, dict)}
            self.assertIn("intervention.readiness_tier_debt_stagnation", rows["stag"]["detection_reason_codes"])

    def test_run_no_save(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "x")
            _write_snapshot(root, "x")
            _write_queue(
                root,
                [
                    {
                        "product_id": "x",
                        "queue_rank": 1,
                        "priority_score": 50.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.3,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            r = run_portfolio_intervention(root, write_artifacts=False)
            self.assertFalse((root / "runs" / "portfolio" / "intervention" / "latest.json").is_file())
            self.assertEqual(r["schema"], PORTFOLIO_INTERVENTION_SCHEMA)


if __name__ == "__main__":
    unittest.main()
