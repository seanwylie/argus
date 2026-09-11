"""Dashboard aggregation for ``portfolio_priority_trends.json`` (read-only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from argus.dashboard.data import build_dashboard_payload
from argus.orchestrator.portfolio_priority_trends import PORTFOLIO_PRIORITY_TRENDS_SCHEMA


def _valid_trends() -> dict:
    return {
        "schema": PORTFOLIO_PRIORITY_TRENDS_SCHEMA,
        "schema_version": "1",
        "generated_at_utc": "2026-04-12T12:00:00+00:00",
        "window_size": 3,
        "generations_considered": 3,
        "churn_summary": "Top product changed 1 of 2 time(s) between consecutive snapshots.",
        "portfolio_stability": "shifting",
        "portfolio_stability_score": 0.5,
        "top_products_to_inspect": ["a", "b", "c"],
        "operator_recommendations": ["Top products to inspect now: a, b, c"],
        "products": [
            {
                "product_id": "a",
                "latest_rank": 1,
                "average_rank": 1.5,
                "best_rank": 1,
                "worst_rank": 2,
                "times_ranked_first": 2,
                "rising": False,
                "falling": False,
                "stable": True,
                "trend_summary": "Rank #1 in 2 of 3 snapshot(s)",
                "latest_priority_score": 100,
                "average_priority_score": 95.0,
            },
            {
                "product_id": "b",
                "latest_rank": 2,
                "average_rank": 2.0,
                "best_rank": 2,
                "worst_rank": 2,
                "times_ranked_first": 0,
                "rising": True,
                "falling": False,
                "stable": False,
                "trend_summary": "Rising",
                "latest_priority_score": 50,
                "average_priority_score": 50.0,
            },
        ],
    }


class TestDashboardPortfolioTrends(unittest.TestCase):
    def test_missing_file_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            p = build_dashboard_payload(root)
            b = p["orchestration_portfolio_priority_trends"]
            self.assertFalse(b["present"])
            self.assertIsNone(b.get("load_error"))
            codes = [w.get("code") for w in p["diagnostics"]["warnings"]]
            self.assertNotIn("portfolio_priority_trends_invalid", codes)

    def test_valid_surfaces_block(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            path = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(_valid_trends()), encoding="utf-8")
            p = build_dashboard_payload(root)
            b = p["orchestration_portfolio_priority_trends"]
            self.assertTrue(b["present"])
            self.assertIsNone(b.get("load_error"))
            self.assertEqual(b["portfolio_stability"], "shifting")
            self.assertEqual(b["portfolio_stability_score"], 0.5)
            self.assertEqual(b["top_products_to_inspect"], ["a", "b", "c"])
            self.assertTrue(len(b["operator_recommendations"]) >= 1)
            self.assertEqual(len(b["top_trending_products"]), 2)
            self.assertEqual(b["top_trending_products"][0]["product_id"], "a")

    def test_malformed_warns_not_strict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            pth = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            pth.parent.mkdir(parents=True)
            pth.write_text("{", encoding="utf-8")
            p = build_dashboard_payload(root, strict=False)
            b = p["orchestration_portfolio_priority_trends"]
            self.assertFalse(b["present"])
            self.assertIsNotNone(b.get("load_error"))
            codes = [w.get("code") for w in p["diagnostics"]["warnings"]]
            self.assertIn("portfolio_priority_trends_invalid", codes)


if __name__ == "__main__":
    unittest.main()
