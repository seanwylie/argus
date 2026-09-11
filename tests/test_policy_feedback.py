"""Tests for :mod:`argus.policy.feedback`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA
from argus.policy.feedback import (
    OPERATOR_POLICY_FEEDBACK_SCHEMA,
    evaluate_operator_policy_feedback,
    run_operator_policy_feedback,
)
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA


def _pp(
    product_id: str = "p1",
    *,
    overall: str = "positive",
    readiness: str = "improved",
    debt: str = "decreased",
    rank: str = "improved",
    blocked: str = "never_blocked",
    intervention: str = "clear",
    prog_tot: int = 3,
    prog_blk: int = 0,
) -> dict:
    return {
        "product_id": product_id,
        "readiness_trajectory": readiness,
        "understanding_debt_trajectory": debt,
        "queue_rank_trajectory": rank,
        "next_action_pattern": "changed",
        "blocked_pattern": blocked,
        "import_health_trajectory": "unchanged_ok",
        "decision_confidence_trajectory": "flat",
        "intervention_pattern": intervention,
        "progression_blocked": {
            "blocked_count": prog_blk,
            "total_runs": prog_tot,
            "consecutive_blocked_prefix": 0,
        },
        "overall_trajectory": overall,
        "snapshot_run_span": {"first": "a", "last": "b", "count": 2},
        "reason_codes": [],
    }


def _outcomes_stamp(
    run_id: str,
    *,
    per: list[dict],
    ddm: float = 0.08,
    cdm: float = 0.05,
) -> dict:
    n = len(per)
    pos = sum(1 for p in per if p.get("overall_trajectory") == "positive")
    neg = sum(1 for p in per if p.get("overall_trajectory") == "negative")
    flat = sum(1 for p in per if p.get("overall_trajectory") == "no_meaningful_movement")
    mix = sum(1 for p in per if p.get("overall_trajectory") == "mixed")
    return {
        "schema": PORTFOLIO_OUTCOMES_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": f"{run_id[:4]}-01-01T00:00:00Z",
        "thresholds_used": {"debt_delta_material": ddm, "confidence_delta_material": cdm},
        "per_product_outcomes": per,
        "portfolio_outcome_summary": {
            "products_evaluated": n,
            "positive_count": pos,
            "negative_count": neg,
            "no_meaningful_movement_count": flat,
            "mixed_count": mix,
        },
        "outcome_reason_codes": [],
    }


class TestPolicyFeedback(unittest.TestCase):
    def test_sparse_history(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = evaluate_operator_policy_feedback(root, limit_history=10)
            self.assertEqual(pl["schema"], OPERATOR_POLICY_FEEDBACK_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            self.assertTrue(pl.get("sparse_history_warning"))
            self.assertGreaterEqual(len(pl.get("metrics_over_time") or []), 1)

    def test_improving_portfolio_live(self) -> None:
        """Rich fixtures from portfolio outcomes — positive trajectories."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            from tests.test_portfolio_outcomes import _delta, _interv, _prog, _row, _write

            pid = "g"
            _write(
                root,
                "delta_report",
                "20260101T000000Z.json",
                _delta("20260101T000000Z", pid, _row(rank=5, tier="observe_gap", debt=0.5)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000000Z.json",
                _delta("20260201T000000Z", pid, _row(rank=3, tier="interpret_gap", debt=0.35)),
            )
            _write(root, "progression", "20260201T000001Z.json", _prog("20260201T000001Z", pid, "advanced"))
            _write(root, "intervention", "20260201T000002Z.json", _interv("20260201T000002Z", []))

            pl = evaluate_operator_policy_feedback(root, limit_history=10)
            self.assertEqual(pl["schema"], OPERATOR_POLICY_FEEDBACK_SCHEMA)
            rates = (pl.get("metrics_over_time") or [])[-1].get("rates") or {}
            self.assertGreater(rates.get("readiness_improvement_rate") or 0, 0.0)

    def test_stagnating_portfolio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            per = [_pp(overall="no_meaningful_movement", readiness="unchanged", debt="flat", rank="flat")]
            stamp = _outcomes_stamp("20260101T000000Z", per=per)
            d = root / "runs" / "portfolio" / "outcomes"
            d.mkdir(parents=True)
            (d / "20260101T000000Z.json").write_text(json.dumps(stamp), encoding="utf-8")
            pl = evaluate_operator_policy_feedback(root, limit_history=10)
            stamped_pts = [p for p in (pl.get("metrics_over_time") or []) if p.get("source") == "stamped_portfolio_outcomes"]
            self.assertTrue(stamped_pts)
            rates = stamped_pts[0].get("rates") or {}
            self.assertGreaterEqual(rates.get("no_movement_frequency") or 0, 0.5)

    def test_mixed_signals_and_correlation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            per_a = [_pp("a", overall="positive"), _pp("b", overall="positive")]
            per_b = [_pp("a", overall="negative"), _pp("b", overall="negative")]
            d = root / "runs" / "portfolio" / "outcomes"
            d.mkdir(parents=True)
            (d / "20260101T000000Z.json").write_text(
                json.dumps(_outcomes_stamp("20260101T000000Z", per=per_a, ddm=0.05)),
                encoding="utf-8",
            )
            (d / "20260201T000000Z.json").write_text(
                json.dumps(_outcomes_stamp("20260201T000000Z", per=per_b, ddm=0.20)),
                encoding="utf-8",
            )
            pl = evaluate_operator_policy_feedback(root, limit_history=10)
            self.assertFalse(pl.get("sparse_history_warning"))
            sus = pl.get("suspicious_correlations") or []
            codes = {s.get("code") for s in sus if isinstance(s, dict)}
            self.assertIn("feedback.suspicious.higher_debt_materiality_vs_fewer_positive_outcomes", codes)

    def test_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = run_operator_policy_feedback(root, limit_history=5, write_artifacts=True)
            self.assertTrue((root / "runs" / "policy" / "feedback" / "latest.json").is_file())
            self.assertEqual(pl["schema"], OPERATOR_POLICY_FEEDBACK_SCHEMA)

