"""Tests for :mod:`argus.portfolio.outcomes`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.outcomes import (
    PORTFOLIO_OUTCOMES_SCHEMA,
    evaluate_portfolio_outcomes,
    render_portfolio_outcomes_markdown,
    run_portfolio_outcomes,
)
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA


def _write(root: Path, rel: str, name: str, payload: dict) -> Path:
    d = root / "runs" / "portfolio" / rel
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _row(
    *,
    rank: int,
    tier: str,
    debt: float,
    na: str = "signals_collect",
    orch: str = "eligible",
    conf: float | None = 0.5,
    fps: str = "success",
    gt: str | None = None,
) -> dict:
    r: dict = {
        "queue_rank": rank,
        "priority_score": 50.0,
        "readiness_tier": tier,
        "next_action": na,
        "understanding_debt": debt,
        "orchestration_status": orch,
        "first_pass_status": fps,
    }
    if gt is not None:
        r["gating_tier"] = gt
    if conf is not None:
        r["top_decision_confidence"] = conf
    return r


def _delta(run_id: str, pid: str, row: dict) -> dict:
    return {
        "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": f"{run_id[:4]}-01-01T00:00:00+00:00",
        "baseline_for_next_run": {
            "evaluated_at_utc": run_id,
            "per_product": {pid: row},
            "queue_product_order": [pid],
        },
    }


def _prog(run_id: str, pid: str, outcome: str) -> dict:
    return {
        "schema": PORTFOLIO_PROGRESSION_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": run_id,
        "products": [{"product_id": pid, "outcome": outcome}],
        "summary_counts": {outcome: 1},
    }


def _interv(run_id: str, flagged: list) -> dict:
    return {
        "schema": PORTFOLIO_INTERVENTION_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": run_id,
        "flagged_products": flagged,
        "inputs": {},
    }


class TestPortfolioOutcomes(unittest.TestCase):
    def test_empty_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            self.assertEqual(pl["schema"], PORTFOLIO_OUTCOMES_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            self.assertEqual(pl["portfolio_outcome_summary"]["products_evaluated"], 0)
            self.assertIn("outcomes.no_delta_or_quiescence_baseline", pl["outcome_reason_codes"])

    def test_improving_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_imp"
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta(
                    "20260101T000001Z",
                    pid,
                    _row(rank=5, tier="observe_gap", debt=0.4, conf=0.4),
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=2, tier="advance_ready", debt=0.18, conf=0.62, na="none"),
                ),
            )
            _write(root, "progression", "20260115T000000Z.json", _prog("20260115T000000Z", pid, "advanced"))
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000000Z.json", _interv("20260202T000000Z", []))

            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = next(x for x in pl["per_product_outcomes"] if x["product_id"] == pid)
            self.assertEqual(row["overall_trajectory"], "positive")
            self.assertEqual(row["readiness_trajectory"], "improved")
            self.assertEqual(row["understanding_debt_trajectory"], "decreased")
            self.assertEqual(row["queue_rank_trajectory"], "improved")
            self.assertIn(pid, pl["products_with_positive_trajectory"])

    def test_degrading_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_deg"
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta(
                    "20260101T000001Z",
                    pid,
                    _row(rank=2, tier="advance_ready", debt=0.2, conf=0.7),
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=6, tier="interpret_gap", debt=0.45, conf=0.35),
                ),
            )
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = next(x for x in pl["per_product_outcomes"] if x["product_id"] == pid)
            self.assertEqual(row["overall_trajectory"], "negative")
            self.assertEqual(row["readiness_trajectory"], "regressed")
            self.assertIn(pid, pl["products_with_negative_trajectory"])

    def test_repeated_blocked_progression(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_blk"
            base = _row(rank=1, tier="observe_gap", debt=0.3, conf=0.5, orch="blocked_waiting_input")
            _write(root, "delta_report", "20260101T000001Z.json", _delta("20260101T000001Z", pid, base))
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    {**base, "orchestration_status": "blocked_waiting_input"},
                ),
            )
            for i, rid in enumerate(
                ["20260105T000000Z", "20260112T000000Z", "20260119T000000Z", "20260126T000000Z"]
            ):
                _write(root, "progression", f"{rid}.json", _prog(rid, pid, "blocked_waiting"))
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = next(x for x in pl["per_product_outcomes"] if x["product_id"] == pid)
            self.assertIn("outcomes.progression_repeated_blocked", row["reason_codes"])
            self.assertEqual(row["blocked_pattern"], "persisted")

    def test_partial_import_recovery(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_impfix"
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta(
                    "20260101T000001Z",
                    pid,
                    _row(rank=3, tier="import_incomplete", debt=0.5, fps="partial", gt="failed"),
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=3, tier="observe_gap", debt=0.35, fps="success", gt="complete"),
                ),
            )
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = next(x for x in pl["per_product_outcomes"] if x["product_id"] == pid)
            self.assertEqual(row["import_health_trajectory"], "recovered")

    def test_confidence_only_improvement(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_conf"
            t = "interpret_gap"
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta(
                    "20260101T000001Z",
                    pid,
                    _row(rank=4, tier=t, debt=0.33, conf=0.32),
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=4, tier=t, debt=0.33, conf=0.55),
                ),
            )
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = next(x for x in pl["per_product_outcomes"] if x["product_id"] == pid)
            self.assertEqual(row["decision_confidence_trajectory"], "improved")
            self.assertEqual(row["readiness_trajectory"], "unchanged")
            self.assertEqual(row["understanding_debt_trajectory"], "flat")

    def test_no_meaningful_movement(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_flat"
            row = _row(rank=3, tier="observe_gap", debt=0.31, conf=0.5)
            _write(root, "delta_report", "20260101T000001Z.json", _delta("20260101T000001Z", pid, dict(row)))
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta("20260201T000002Z", pid, dict(row)),
            )
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            r = next(x for x in pl["per_product_outcomes"] if x["product_id"] == pid)
            self.assertEqual(r["overall_trajectory"], "no_meaningful_movement")
            self.assertIn(pid, pl["products_with_no_meaningful_movement"])

    def test_mixed_portfolio_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d1 = {
                "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
                "run_id": "20260101T000001Z",
                "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                "baseline_for_next_run": {
                    "evaluated_at_utc": "20260101T000001Z",
                    "per_product": {
                        "good": _row(rank=5, tier="observe_gap", debt=0.4),
                        "bad": _row(rank=2, tier="advance_ready", debt=0.2),
                    },
                    "queue_product_order": ["good", "bad"],
                },
            }
            d2 = {
                "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
                "run_id": "20260201T000002Z",
                "evaluated_at_utc": "2026-02-01T00:00:00+00:00",
                "baseline_for_next_run": {
                    "evaluated_at_utc": "20260201T000002Z",
                    "per_product": {
                        "good": _row(rank=2, tier="advance_ready", debt=0.18),
                        "bad": _row(rank=6, tier="interpret_gap", debt=0.5),
                    },
                    "queue_product_order": ["good", "bad"],
                },
            }
            _write(root, "delta_report", "20260101T000001Z.json", d1)
            _write(root, "delta_report", "20260201T000002Z.json", d2)
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            summ = pl["portfolio_outcome_summary"]
            self.assertEqual(summ["positive_count"], 1)
            self.assertEqual(summ["negative_count"], 1)
            self.assertEqual(set(pl["products_with_positive_trajectory"]), {"good"})
            self.assertEqual(set(pl["products_with_negative_trajectory"]), {"bad"})

    def test_run_writes_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p"
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta("20260101T000001Z", pid, _row(rank=1, tier="observe_gap", debt=0.4)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta("20260201T000002Z", pid, _row(rank=1, tier="observe_gap", debt=0.39)),
            )
            pl = run_portfolio_outcomes(root, limit_history=10, write_artifacts=True)
            latest = root / "runs" / "portfolio" / "outcomes" / "latest.json"
            self.assertTrue(latest.is_file())
            self.assertIn("Portfolio outcomes", render_portfolio_outcomes_markdown(pl))


class TestOutcomesIntervention(unittest.TestCase):
    def test_intervention_resolved(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p"
            _write(root, "delta_report", "20260101T000001Z.json", _delta("20260101T000001Z", pid, _row(rank=1, tier="observe_gap", debt=0.3)))
            _write(root, "delta_report", "20260201T000002Z.json", _delta("20260201T000002Z", pid, _row(rank=1, tier="observe_gap", debt=0.29)))
            _write(
                root,
                "intervention",
                "20260110T000000Z.json",
                _interv(
                    "20260110T000000Z",
                    [{"product_id": pid, "intervention_category": "human_review", "severity": "high"}],
                ),
            )
            _write(root, "intervention", "20260202T000000Z.json", _interv("20260202T000000Z", []))
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = pl["per_product_outcomes"][0]
            self.assertEqual(row["intervention_pattern"], "resolved")
