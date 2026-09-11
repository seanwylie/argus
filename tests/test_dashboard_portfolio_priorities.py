"""Dashboard aggregation for ``runs/orchestration/latest/portfolio_priorities.json`` (read-only)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from argus.dashboard.data import build_dashboard_payload
from argus.orchestrator.portfolio_priorities import PORTFOLIO_PRIORITIES_SCHEMA


def _valid_portfolio_priorities_payload() -> dict:
    return {
        "schema": PORTFOLIO_PRIORITIES_SCHEMA,
        "schema_version": "1",
        "generated_at_utc": "2026-04-12T10:00:00+00:00",
        "repo_root": "/tmp",
        "recommended_product_id": "alpha",
        "recommended_next_action": "emit_orchestration",
        "products": [
            {
                "product_id": "alpha",
                "rank": 1,
                "priority_score": 200,
                "orchestration_status": "eligible",
                "next_action": "emit_orchestration",
                "strategy_posture": "explore",
                "planning_mode": "validate",
                "priority_reasons": ["next_action is emit_orchestration", "orchestration_status is eligible"],
                "evidence_summary": "eligible; emit",
            },
            {
                "product_id": "beta",
                "rank": 2,
                "priority_score": 100,
                "orchestration_status": "complete",
                "next_action": "none",
                "strategy_posture": None,
                "planning_mode": None,
                "priority_reasons": [],
                "evidence_summary": "complete",
            },
        ],
    }


class TestDashboardOrchestrationPortfolioPriorities(unittest.TestCase):
    def test_missing_file_graceful(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            p = build_dashboard_payload(root)
            opp = p["orchestration_portfolio_priorities"]
            self.assertFalse(opp["present"])
            self.assertIsNone(opp.get("load_error"))
            self.assertEqual(opp["top_products"], [])
            codes = [w.get("code") for w in p["diagnostics"]["warnings"]]
            self.assertNotIn("portfolio_priorities_invalid", codes)

    def test_valid_file_surfaces_compact_payload(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            orch = root / "runs" / "orchestration" / "latest"
            orch.mkdir(parents=True)
            (orch / "portfolio_priorities.json").write_text(
                json.dumps(_valid_portfolio_priorities_payload()),
                encoding="utf-8",
            )
            p = build_dashboard_payload(root)
            opp = p["orchestration_portfolio_priorities"]
            self.assertTrue(opp["present"])
            self.assertIsNone(opp.get("load_error"))
            self.assertEqual(opp["generated_at_utc"], "2026-04-12T10:00:00+00:00")
            self.assertEqual(opp["recommended_product_id"], "alpha")
            self.assertEqual(opp["recommended_next_action"], "emit_orchestration")
            self.assertEqual(opp["rank_1_priority_reasons"], ["next_action is emit_orchestration", "orchestration_status is eligible"])
            self.assertEqual(opp["rank_1_evidence_summary"], "eligible; emit")
            tops = opp["top_products"]
            self.assertEqual(len(tops), 2)
            self.assertEqual(tops[0]["product_id"], "alpha")
            self.assertEqual(tops[0]["rank"], 1)
            self.assertEqual(tops[0]["priority_score"], 200)
            self.assertEqual(tops[0]["orchestration_status"], "eligible")
            self.assertEqual(tops[0]["next_action"], "emit_orchestration")
            self.assertEqual(tops[0]["strategy_posture"], "explore")
            self.assertEqual(tops[0]["planning_mode"], "validate")
            self.assertEqual(len(tops[0]["priority_reasons"]), 2)

    def test_malformed_json_warns_not_strict(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            orch = root / "runs" / "orchestration" / "latest"
            orch.mkdir(parents=True)
            (orch / "portfolio_priorities.json").write_text("{ not json", encoding="utf-8")
            p = build_dashboard_payload(root, strict=False)
            opp = p["orchestration_portfolio_priorities"]
            self.assertFalse(opp["present"])
            self.assertIsNotNone(opp.get("load_error"))
            codes = [w.get("code") for w in p["diagnostics"]["warnings"]]
            self.assertIn("portfolio_priorities_invalid", codes)
            self.assertEqual(p["diagnostics"]["errors"], [])

    def test_wrong_schema_strict_records_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            orch = root / "runs" / "orchestration" / "latest"
            orch.mkdir(parents=True)
            (orch / "portfolio_priorities.json").write_text(
                json.dumps({"schema": "other.schema", "products": []}),
                encoding="utf-8",
            )
            p = build_dashboard_payload(root, strict=True)
            opp = p["orchestration_portfolio_priorities"]
            self.assertFalse(opp["present"])
            err_codes = [e.get("code") for e in p["diagnostics"]["errors"]]
            self.assertIn("strict_json", err_codes)


if __name__ == "__main__":
    unittest.main()
