"""Integration tests: signal instrumentation awareness across lifecycle, summary, narrative, autonomy memory."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.dashboard.narrative import evaluate_operator_narrative
from argus.dashboard.operator_summary import evaluate_operator_summary
from argus.portfolio.autonomous_runner import PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA
from argus.portfolio.autonomy_memory import build_portfolio_autonomy_memory_payload
from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle
from argus.products.apply_signal_instrumentation import PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA
from argus.products.signal_instrumentation import PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA


def _product_min(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: X
owner:
  team: t
lifecycle:
  stage: validate
metrics:
  local_paths: [metrics/]
  primary: []
signals:
  - type: filesystem
    enabled: true
actions:
  analyze: "./scripts/a.sh"
constraints:
  max_monthly_cost_usd: 10
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "a.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "m.txt").write_text("1", encoding="utf-8")


def _write_inst(root: Path, pid: str, *, status: str = "sparse") -> None:
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


def _write_apply(
    root: Path,
    pid: str,
    *,
    apply_status: str = "success",
    post_instrumentation_status: str = "adequate",
) -> None:
    d = root / "runs" / "products" / "signal_instrumentation_apply" / "latest"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA,
        "product_id": pid,
        "work_order_id": "wo_sig",
        "execution_id": "exec_sig",
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


class SignalInstrumentationIntegrationTests(unittest.TestCase):
    def test_lifecycle_lists_pressure_when_artifact_present(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "thin")
            _write_inst(root, "thin", status="sparse")
            pl = evaluate_portfolio_lifecycle(root)
            self.assertIn("thin", pl["products_under_instrumentation_pressure"])
            row = next(r for r in pl["per_product_lifecycle"] if r["product_id"] == "thin")
            ev = row["evidence"]["signal_instrumentation"]
            self.assertTrue(ev["artifact_present"])
            self.assertEqual(ev["instrumentation_status"], "sparse")

    def test_lifecycle_effective_pressure_drops_when_apply_adequate(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "thin")
            _write_inst(root, "thin", status="sparse")
            _write_apply(root, "thin", apply_status="success", post_instrumentation_status="adequate")
            pl = evaluate_portfolio_lifecycle(root)
            self.assertNotIn("thin", pl["products_under_instrumentation_pressure"])
            self.assertIn("thin", pl["products_instrumentation_resolved_via_apply"])
            self.assertIn("thin", pl["products_under_instrumentation_pressure_raw"])

    def test_lifecycle_partial_apply_remains_under_pressure(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "thin")
            _write_inst(root, "thin", status="sparse")
            _write_apply(root, "thin", apply_status="partial", post_instrumentation_status="weak")
            pl = evaluate_portfolio_lifecycle(root)
            self.assertIn("thin", pl["products_under_instrumentation_pressure"])
            self.assertIn("thin", pl["products_instrumentation_apply_followup"])

    def test_lifecycle_graceful_without_artifacts(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "solo")
            pl = evaluate_portfolio_lifecycle(root)
            self.assertEqual(pl["products_under_instrumentation_pressure"], [])
            row = next(r for r in pl["per_product_lifecycle"] if r["product_id"] == "solo")
            self.assertFalse(row["evidence"]["signal_instrumentation"]["artifact_present"])

    def test_operator_summary_snapshot_and_watch(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "thin")
            _write_inst(root, "thin", status="missing")
            with patch("argus.dashboard.operator_summary._load_queue", return_value=None):
                with patch("argus.dashboard.operator_summary._load_progression", return_value=None):
                    with patch("argus.dashboard.operator_summary._load_quiescence", return_value=None):
                        with patch("argus.dashboard.operator_summary._load_delta", return_value=None):
                            with patch("argus.dashboard.operator_summary._load_cycle", return_value=None):
                                with patch(
                                    "argus.dashboard.operator_summary._load_patterns",
                                    return_value=None,
                                ):
                                    with patch(
                                        "argus.dashboard.operator_summary._load_canonical_portfolio_outcomes",
                                        return_value={
                                            "schema": "argus.portfolio_outcomes.v1",
                                            "portfolio_outcome_summary": {
                                                "products_evaluated": 0,
                                                "positive_count": 0,
                                                "negative_count": 0,
                                                "no_meaningful_movement_count": 0,
                                            },
                                        },
                                    ):
                                        with patch(
                                            "argus.dashboard.operator_summary._build_operator_intervention_inbox_view"
                                        ) as bi:
                                            bi.return_value = {
                                                "schema": "argus.intervention_inbox.v1",
                                                "open_items": [],
                                            }
                                            pl = evaluate_operator_summary(root)
            self.assertIn("thin", pl["signal_instrumentation_snapshot"]["products_under_instrumentation_pressure"])
            watch = pl["top_products_to_watch"]
            if watch:
                tw = next((w for w in watch if w.get("product_id") == "thin"), None)
                if tw:
                    self.assertIn("instrumentation", str(tw.get("reason")).lower())

    def test_autonomy_memory_inspect_alignment_note(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "thin")
            _write_inst(root, "thin", status="sparse")
            ar = root / "runs" / "portfolio" / "autonomous_runner"
            ar.mkdir(parents=True)
            for i in range(3):
                sid = f"2026010{i + 1}T000000Z"
                pl = {
                    "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
                    "session_id": sid,
                    "started_at_utc": "2026-01-01T00:00:00Z",
                    "finished_at_utc": "2026-01-01T00:00:00Z",
                    "cycles_run": 1,
                    "stop_reason": "cycle_overall_recommendation",
                    "stop_reason_codes": [
                        "autonomous_runner.stop.cycle_overall.inspect_specific_products",
                    ],
                    "lifecycle_session_influence": {
                        "primary_signal": "neutral",
                        "inputs_snapshot": {
                            "products_under_repair_pressure": ["thin"],
                            "products_under_retirement_pressure": [],
                            "products_entering": [],
                            "products_exiting": [],
                        },
                    },
                    "promotable_actions": [],
                    "blocked_promotions": [],
                    "promotion_execution": {
                        "allow_promotion": False,
                        "skipped_reason": "x",
                        "steps": [],
                    },
                }
                (ar / f"{sid}.json").write_text(json.dumps(pl), encoding="utf-8")
            mem = build_portfolio_autonomy_memory_payload(root, limit_history=10)
            self.assertTrue(any("inspect" in n.lower() for n in mem["signal_instrumentation_advisory_notes"]))

    def test_narrative_includes_signal_instrumentation_section(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _product_min(root, "thin")
            _write_inst(root, "thin", status="sparse")
            with patch("argus.dashboard.narrative.evaluate_portfolio_history") as h:
                h.return_value = {
                    "schema": "argus.portfolio_history.v1",
                    "run_id": "h",
                    "loaded_artifact_counts": {"cycle": 3, "delta_report": 3},
                    "trend_summaries": {},
                }
                with patch("argus.dashboard.narrative.load_canonical_portfolio_outcomes") as o:
                    o.return_value = {
                        "schema": "argus.portfolio_outcomes.v1",
                        "run_id": "o1",
                        "portfolio_outcome_summary": {
                            "products_evaluated": 1,
                            "positive_count": 0,
                            "negative_count": 0,
                            "no_meaningful_movement_count": 1,
                        },
                    }
                    with patch("argus.dashboard.narrative.evaluate_portfolio_patterns") as p:
                        p.return_value = {
                            "schema": "argus.portfolio_patterns.v1",
                            "run_id": "p",
                            "detected_patterns": [],
                        }
                        with patch("argus.dashboard.narrative.load_latest_n_artifacts", return_value=[]):
                            pl = evaluate_operator_narrative(root)
            sec = pl.get("sections") or {}
            self.assertTrue(sec.get("signal_instrumentation"))


if __name__ == "__main__":
    unittest.main()
