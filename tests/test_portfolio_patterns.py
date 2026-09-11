"""Tests for :mod:`argus.portfolio.patterns`."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.patterns import (
    PORTFOLIO_PATTERNS_SCHEMA,
    detect_cross_product_patterns,
    evaluate_portfolio_patterns,
    run_portfolio_patterns,
)


def _row_outcome(
    pid: str,
    *,
    overall: str = "no_meaningful_movement",
    debt: str = "flat",
    readiness: str = "unchanged",
    conf: str = "flat",
    blocked: str = "never_blocked",
    imp: str = "unchanged_ok",
) -> dict:
    return {
        "product_id": pid,
        "readiness_trajectory": readiness,
        "understanding_debt_trajectory": debt,
        "queue_rank_trajectory": "flat",
        "next_action_pattern": "repeated",
        "blocked_pattern": blocked,
        "import_health_trajectory": imp,
        "decision_confidence_trajectory": conf,
        "intervention_pattern": "unknown",
        "progression_blocked": {"blocked_count": 0, "total_runs": 0, "consecutive_blocked_prefix": 0},
        "overall_trajectory": overall,
        "snapshot_run_span": {"first": "a", "last": "b", "count": 2},
        "reason_codes": [],
    }


class TestDetectCrossProductPatterns(unittest.TestCase):
    def test_multi_product_stagnation(self) -> None:
        per = [
            _row_outcome("a"),
            _row_outcome("b"),
            _row_outcome("c"),
        ]
        meta = {"a": {"product_shape": "python_service"}, "b": {"product_shape": "python_service"}, "c": {"product_shape": "js_frontend"}}
        pats = detect_cross_product_patterns(
            per_product_outcomes=per,
            product_meta=meta,
            intervention_latest=None,
            intervention_history=[],
        )
        ids = {p["pattern_id"] for p in pats}
        self.assertIn("patterns.outcomes.parallel_stagnation_debt_and_readiness", ids)

    def test_mixed_shapes_negative_cluster(self) -> None:
        per = [
            {**_row_outcome("a", overall="negative"), "readiness_trajectory": "regressed"},
            {**_row_outcome("b", overall="negative"), "readiness_trajectory": "regressed"},
        ]
        meta = {
            "a": {"product_shape": "js_frontend", "first_pass_status": None},
            "b": {"product_shape": "js_frontend", "first_pass_status": None},
        }
        pats = detect_cross_product_patterns(
            per_product_outcomes=per,
            product_meta=meta,
            intervention_latest=None,
            intervention_history=[],
        )
        ids = {p["pattern_id"] for p in pats}
        self.assertTrue(any(x.startswith("patterns.shape.correlated_negative_outcomes.") for x in ids))

    def test_isolated_vs_systemic(self) -> None:
        per = [
            {**_row_outcome("only_neg", overall="negative"), "readiness_trajectory": "regressed"},
            _row_outcome("ok1", overall="positive"),
        ]
        meta = {
            "only_neg": {"product_shape": "python_service"},
            "ok1": {"product_shape": "python_service"},
        }
        pats = detect_cross_product_patterns(
            per_product_outcomes=per,
            product_meta=meta,
            intervention_latest=None,
            intervention_history=[],
        )
        # One negative alone — not enough for shape cluster (needs 2)
        self.assertFalse(any("shape.correlated" in p["pattern_id"] for p in pats))

    def test_shared_intervention_category(self) -> None:
        inv = {
            "schema": PORTFOLIO_INTERVENTION_SCHEMA,
            "run_id": "20260101T000000Z",
            "flagged_products": [
                {"product_id": "x", "intervention_category": "human_review"},
                {"product_id": "y", "intervention_category": "human_review"},
            ],
        }
        pats = detect_cross_product_patterns(
            per_product_outcomes=[],
            product_meta={},
            intervention_latest=inv,
            intervention_history=[],
        )
        self.assertTrue(any("intervention.shared_category.human_review" in p["pattern_id"] for p in pats))

    def test_intervention_history_recurrence(self) -> None:
        hist = [
            (
                "20260101T000000Z",
                {
                    "schema": PORTFOLIO_INTERVENTION_SCHEMA,
                    "flagged_products": [
                        {"product_id": "u", "intervention_category": "policy_tuning"},
                        {"product_id": "v", "intervention_category": "policy_tuning"},
                    ],
                },
            ),
            (
                "20260102T000000Z",
                {
                    "schema": PORTFOLIO_INTERVENTION_SCHEMA,
                    "flagged_products": [
                        {"product_id": "u", "intervention_category": "policy_tuning"},
                        {"product_id": "v", "intervention_category": "policy_tuning"},
                    ],
                },
            ),
        ]
        pats = detect_cross_product_patterns(
            per_product_outcomes=[],
            product_meta={},
            intervention_latest=None,
            intervention_history=hist,
        )
        self.assertTrue(any("recurring_category.policy_tuning" in p["pattern_id"] for p in pats))

    def test_no_pattern_empty_inputs(self) -> None:
        pats = detect_cross_product_patterns(
            per_product_outcomes=[],
            product_meta={},
            intervention_latest=None,
            intervention_history=[],
        )
        self.assertEqual(pats, [])


class TestPortfolioPatternsIntegration(unittest.TestCase):
    def test_empty_repo_evaluate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = evaluate_portfolio_patterns(root, limit_history=5)
            self.assertEqual(pl["schema"], PORTFOLIO_PATTERNS_SCHEMA)
            self.assertEqual(pl.get("detected_patterns"), [])

    def test_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = run_portfolio_patterns(root, limit_history=5, write_artifacts=True)
            self.assertTrue((root / "runs" / "portfolio" / "patterns" / "latest.json").is_file())
            self.assertEqual(pl["schema"], PORTFOLIO_PATTERNS_SCHEMA)


if __name__ == "__main__":
    unittest.main()
