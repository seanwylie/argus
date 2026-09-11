"""Tests for :mod:`argus.portfolio.delta_report`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.operator_snapshot import OPERATOR_SNAPSHOT_SCHEMA
from argus.portfolio.delta_report import (
    PORTFOLIO_DELTA_REPORT_SCHEMA,
    evaluate_portfolio_delta_report,
    render_portfolio_delta_report_markdown,
    run_portfolio_delta_report,
    write_portfolio_delta_report_artifacts,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA


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


def _write_queue(root: Path, entries: list[dict], *, generated_at: str = "2026-04-12T12:00:00+00:00") -> None:
    d = root / "runs" / "portfolio" / "operator_queue"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": OPERATOR_QUEUE_SCHEMA,
        "generated_at_utc": generated_at,
        "scoring_weights_version": "1",
        "weight_ledger": {},
        "entries": entries,
    }
    (d / "latest.json").write_text(json.dumps(pl) + "\n", encoding="utf-8")


def _write_quiescence_baseline(
    root: Path,
    per_product: dict,
    queue_order: list[str],
    *,
    ev: str = "2026-04-12T10:00:00+00:00",
) -> None:
    d = root / "runs" / "portfolio" / "quiescence"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PORTFOLIO_QUIESCENCE_SCHEMA,
        "evaluated_at_utc": ev,
        "portfolio_quiescent": False,
        "quiescence_reason_codes": [],
        "recommendation": "run_again",
        "baseline_for_next_run": {
            "evaluated_at_utc": ev,
            "per_product": per_product,
            "queue_product_order": queue_order,
        },
    }
    (d / "latest.json").write_text(json.dumps(pl) + "\n", encoding="utf-8")


def _write_progression_latest(root: Path, products: list[dict], *, run_id: str = "20260412T120000Z") -> None:
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
        "summary_counts": {"advanced": len([p for p in products if p.get("outcome") == "advanced"])},
    }
    (d / "latest.json").write_text(json.dumps(pl) + "\n", encoding="utf-8")


class TestPortfolioDeltaReport(unittest.TestCase):
    def test_no_prior_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _write_snapshot(root, "a")
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 90.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.25,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            r = evaluate_portfolio_delta_report(root)
            self.assertEqual(r["schema"], PORTFOLIO_DELTA_REPORT_SCHEMA)
            self.assertFalse(r["baseline"]["had_per_product"])
            self.assertEqual(r["baseline"]["source"], "none")
            self.assertIn("delta_report.no_prior_baseline", r["delta_reason_codes"])
            self.assertEqual(r["recommended_next_portfolio_action"], "establish_baseline")

    def test_one_product_advanced_list(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _write_snapshot(root, "a")
            prior_fp = {
                "a": {
                    "product_id": "a",
                    "queue_rank": 1,
                    "priority_score": 90.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "signals_collect",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.25,
                    "top_decision_confidence": 0.5,
                    "first_pass_status": "success",
                }
            }
            _write_quiescence_baseline(root, prior_fp, ["a"])
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 90.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.25,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            _write_progression_latest(
                root,
                [{"product_id": "a", "outcome": "advanced", "reason": "ok"}],
            )
            r = evaluate_portfolio_delta_report(root)
            self.assertEqual(r["products_advanced_this_pass"], ["a"])

    def test_newly_actionable(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            prior_fp = {
                "a": {
                    "product_id": "a",
                    "queue_rank": 1,
                    "priority_score": 100.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "none",
                    "orchestration_status": "blocked_waiting_input",
                    "waiting_inputs_count": 1,
                    "blockers_count": 0,
                    "understanding_debt": 0.3,
                    "top_decision_confidence": 0.5,
                    "first_pass_status": "success",
                }
            }
            _write_quiescence_baseline(root, prior_fp, ["a"])
            _write_snapshot(
                root,
                "a",
                next_action="findings_generate",
                orchestration_status="eligible",
            )
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 95.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "findings_generate",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.3,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            r = evaluate_portfolio_delta_report(root)
            self.assertIn("a", r["products_newly_actionable"])

    def test_queue_reshuffle_readiness_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("a", "b", "c"):
                _minimal_product(root, pid)
                _write_snapshot(root, pid)
            prior_fp = {
                "a": {
                    "product_id": "a",
                    "queue_rank": 1,
                    "priority_score": 90.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "signals_collect",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.2,
                    "top_decision_confidence": 0.55,
                    "first_pass_status": "success",
                },
                "b": {
                    "product_id": "b",
                    "queue_rank": 2,
                    "priority_score": 88.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "signals_collect",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.2,
                    "top_decision_confidence": 0.55,
                    "first_pass_status": "success",
                },
                "c": {
                    "product_id": "c",
                    "queue_rank": 3,
                    "priority_score": 86.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "signals_collect",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.2,
                    "top_decision_confidence": 0.55,
                    "first_pass_status": "success",
                },
            }
            _write_quiescence_baseline(root, prior_fp, ["a", "b", "c"])
            # a moves from rank 1 → 3 (Δ+2), material per RANK_SHIFT_MATERIAL; tiers unchanged
            _write_queue(
                root,
                [
                    {
                        "product_id": "b",
                        "queue_rank": 1,
                        "priority_score": 92.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.2,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    },
                    {
                        "product_id": "c",
                        "queue_rank": 2,
                        "priority_score": 90.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.2,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    },
                    {
                        "product_id": "a",
                        "queue_rank": 3,
                        "priority_score": 88.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.2,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    },
                ],
            )
            r = evaluate_portfolio_delta_report(root)
            qm = {x["product_id"]: x for x in r["queue_movement"] if isinstance(x, dict)}
            self.assertIn("a", qm)
            self.assertEqual(qm["a"]["delta"], 2)
            self.assertTrue(qm["a"]["material"])
            self.assertEqual(r["readiness_tier_changed"], [])

    def test_confidence_change_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            prior_fp = {
                "a": {
                    "product_id": "a",
                    "queue_rank": 1,
                    "priority_score": 90.0,
                    "readiness_tier": "advance_ready",
                    "next_action": "none",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.1,
                    "top_decision_confidence": 0.4,
                    "first_pass_status": "success",
                }
            }
            _write_quiescence_baseline(root, prior_fp, ["a"])
            _write_snapshot(root, "a", readiness_tier="advance_ready", top_decision_confidence=0.55)
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 90.0,
                        "readiness_tier": "advance_ready",
                        "next_action": "none",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.1,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            r = evaluate_portfolio_delta_report(root)
            self.assertEqual(len(r["decision_confidence_changed_materially"]), 1)
            self.assertEqual(r["decision_confidence_changed_materially"][0]["product_id"], "a")

    def test_import_health_change_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            prior_fp = {
                "a": {
                    "product_id": "a",
                    "queue_rank": 1,
                    "priority_score": 90.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "signals_collect",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.3,
                    "top_decision_confidence": 0.5,
                    "first_pass_status": "partial",
                }
            }
            _write_quiescence_baseline(root, prior_fp, ["a"])
            _write_snapshot(root, "a", first_pass_status="success")
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 88.0,
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
            r = evaluate_portfolio_delta_report(root)
            self.assertEqual(len(r["import_health_changed"]), 1)
            self.assertEqual(r["recommended_next_portfolio_action"], "inspect_import_health")

    def test_mixed_improve_and_regress(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("a", "b"):
                _minimal_product(root, pid)
            prior_fp = {
                "a": {
                    "product_id": "a",
                    "queue_rank": 1,
                    "priority_score": 90.0,
                    "readiness_tier": "observe_gap",
                    "next_action": "signals_collect",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.4,
                    "top_decision_confidence": 0.5,
                    "first_pass_status": "success",
                },
                "b": {
                    "product_id": "b",
                    "queue_rank": 2,
                    "priority_score": 88.0,
                    "readiness_tier": "advance_ready",
                    "next_action": "none",
                    "orchestration_status": "eligible",
                    "waiting_inputs_count": 0,
                    "blockers_count": 0,
                    "understanding_debt": 0.1,
                    "top_decision_confidence": 0.7,
                    "first_pass_status": "success",
                },
            }
            _write_quiescence_baseline(root, prior_fp, ["a", "b"])
            _write_snapshot(
                root,
                "a",
                readiness_tier="interpret_gap",
                understanding_debt=0.5,
            )
            _write_snapshot(
                root,
                "b",
                readiness_tier="observe_gap",
                understanding_debt=0.35,
            )
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 91.0,
                        "readiness_tier": "interpret_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.5,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    },
                    {
                        "product_id": "b",
                        "queue_rank": 2,
                        "priority_score": 89.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.35,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    },
                ],
            )
            r = evaluate_portfolio_delta_report(root)
            imp = {x["product_id"]: x for x in r["what_improved"] if isinstance(x, dict)}
            reg = {x["product_id"]: x for x in r["what_regressed"] if isinstance(x, dict)}
            self.assertIn("readiness_tier_improved", imp.get("a", {}).get("reasons", []))
            self.assertIn("readiness_tier_regressed", reg.get("b", {}).get("reasons", []))

    def test_write_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _write_snapshot(root, "a")
            _write_queue(
                root,
                [
                    {
                        "product_id": "a",
                        "queue_rank": 1,
                        "priority_score": 90.0,
                        "readiness_tier": "observe_gap",
                        "next_action": "signals_collect",
                        "orchestration_status": "eligible",
                        "understanding_debt": 0.25,
                        "priority_reason": "x",
                        "recommendation": "r",
                        "operator_view_source": "snapshot_file",
                    }
                ],
            )
            payload = evaluate_portfolio_delta_report(root)
            sj, sm, lj, lm = write_portfolio_delta_report_artifacts(root, payload, run_id="20260412T120000Z")
            self.assertTrue(sj.is_file())
            self.assertTrue(sm.is_file())
            self.assertTrue(lj.is_file())
            self.assertTrue(lm.is_file())
            self.assertIn("Portfolio delta report", render_portfolio_delta_report_markdown(payload))
            r2 = run_portfolio_delta_report(root, write_artifacts=False)
            self.assertEqual(r2["schema"], PORTFOLIO_DELTA_REPORT_SCHEMA)


if __name__ == "__main__":
    unittest.main()
