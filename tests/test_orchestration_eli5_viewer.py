"""Lightweight tests for tools/orchestration_eli5_viewer.py (stdlib helpers, no server)."""

from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


def _load_eli5_module():
    root = Path(__file__).resolve().parent.parent
    path = root / "tools" / "orchestration_eli5_viewer.py"
    spec = importlib.util.spec_from_file_location("orchestration_eli5_viewer", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestEli5Helpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.m = _load_eli5_module()

    def test_strategy_malformed_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "strategy" / "latest" / "p1.json"
            p.parent.mkdir(parents=True)
            p.write_text("{ not json", encoding="utf-8")
            self.assertIsNone(self.m._strategy_context(root, "p1"))

    def test_strategy_and_planning_when_valid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sp = root / "runs" / "strategy" / "latest" / "p1.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(
                json.dumps(
                    {
                        "schema": self.m._SCHEMA_STRATEGY_SNAPSHOT,
                        "product_id": "p1",
                        "posture": "explore",
                        "posture_raw": "explore",
                        "skepticism_applied": True,
                        "skepticism_reason": "test_reason",
                    }
                ),
                encoding="utf-8",
            )
            pl = root / "runs" / "planning" / "latest" / "p1.json"
            pl.parent.mkdir(parents=True)
            pl.write_text(
                json.dumps(
                    {
                        "schema": self.m._SCHEMA_PLANNING_SNAPSHOT,
                        "product_id": "p1",
                        "planning_mode": "validate",
                        "priority_workstreams": ["a: b"],
                        "recommended_actions": [{"x": 1}],
                    }
                ),
                encoding="utf-8",
            )
            st = self.m._strategy_context(root, "p1")
            self.assertIsNotNone(st)
            assert st is not None
            self.assertTrue(st["skepticism_applied"])
            plx = self.m._planning_context(root, "p1")
            self.assertIsNotNone(plx)
            assert plx is not None
            self.assertEqual(plx["recommended_actions_count"], 1)

    def test_portfolio_rank_for_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pp = root / "runs" / "orchestration" / "latest" / "portfolio_priorities.json"
            pp.parent.mkdir(parents=True)
            pp.write_text(
                json.dumps(
                    {
                        "schema": self.m._SCHEMA_PORTFOLIO_PRIORITIES,
                        "recommended_product_id": "p2",
                        "recommended_next_action": "signals_collect",
                        "products": [
                            {
                                "product_id": "p2",
                                "rank": 1,
                                "priority_score": 100,
                                "priority_reasons": ["next_action is x", "reason two"],
                            },
                            {
                                "product_id": "p1",
                                "rank": 2,
                                "priority_score": 50,
                                "priority_reasons": ["lower"],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ctx = self.m._portfolio_context_for_product(root, "p1")
            self.assertTrue(ctx["present"])
            self.assertEqual(ctx["this_rank"], 2)
            self.assertEqual(ctx["recommended_product_id"], "p2")

    def test_trends_missing_returns_empty_note(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx = self.m._portfolio_trends_context_for_product(root, "p1")
            self.assertFalse(ctx["present"])
            html = self.m._html_portfolio_trends_note("p1", ctx)
            self.assertEqual(html, "")

    def test_trends_malformed_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            p.parent.mkdir(parents=True)
            p.write_text("{ not json", encoding="utf-8")
            ctx = self.m._portfolio_trends_context_for_product(root, "p1")
            self.assertFalse(ctx["present"])

    def test_trends_valid_html_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "schema": self.m._SCHEMA_PORTFOLIO_TRENDS,
                        "portfolio_stability": "shifting",
                        "churn_summary": "Top product changed once.",
                        "operator_recommendations": ["Review top-ranked products."],
                        "products": [
                            {
                                "product_id": "p1",
                                "rising": True,
                                "falling": False,
                                "stable": False,
                                "times_ranked_first": 2,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ctx = self.m._portfolio_trends_context_for_product(root, "p1")
            self.assertTrue(ctx["present"])
            self.assertEqual(ctx["portfolio_stability"], "shifting")
            html = self.m._html_portfolio_trends_note("p1", ctx)
            self.assertIn("Portfolio trend", html)
            self.assertIn("rising", html.lower())
            self.assertIn("Guidance", html)


if __name__ == "__main__":
    unittest.main()
