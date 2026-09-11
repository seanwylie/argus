"""Operator visibility metrics and dashboard payload integration."""

from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path

from argus.dashboard.data import build_dashboard_payload
from argus.dashboard.operator_visibility import build_operator_visibility, collect_operator_alerts


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_yaml(product_id: str = "ov_p") -> str:
    return f"""
    id: {product_id}
    name: Test
    owner:
      team: test
    lifecycle:
      stage: idea
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: 10
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: 100
      min_activity_threshold: 0
    """


class TestOperatorVisibilityCore(unittest.TestCase):
    def test_uncertainty_is_one_minus_confidence(self) -> None:
        ov = build_operator_visibility(
            top_confidence=0.7,
            top_intent="improve_product",
            top_metadata={},
            portfolio_freshness_warnings=None,
            findings_by_severity={"medium": 1},
            active_findings_count=1,
            signal_record_count=5,
            temporal_findings_count=0,
            kill_candidate=False,
            escalations=[],
            trend_summary=None,
            temporal_visibility={},
        )
        self.assertEqual(ov["schema"], "argus.dashboard_operator_visibility.v1")
        self.assertAlmostEqual(ov["confidence"], 0.7)
        self.assertAlmostEqual(ov["uncertainty"], 0.3)
        self.assertEqual(ov["uncertainty_basis"], "one_minus_top_candidate_confidence")

    def test_missing_confidence_no_uncertainty_number(self) -> None:
        ov = build_operator_visibility(
            top_confidence=None,
            top_intent=None,
            top_metadata={},
            portfolio_freshness_warnings=None,
            findings_by_severity={},
            active_findings_count=0,
            signal_record_count=0,
            temporal_findings_count=0,
            kill_candidate=False,
            escalations=[],
            trend_summary=None,
            temporal_visibility={},
        )
        self.assertIsNone(ov["uncertainty"])
        self.assertEqual(ov["uncertainty_basis"], "no_top_candidate_confidence")

    def test_collect_alerts_low_confidence_high_risk(self) -> None:
        products = [
            {
                "product_id": "x",
                "operator_visibility": {
                    "confidence": 0.35,
                    "risk_score": 0.62,
                    "escalation_pressure": 0.2,
                    "exploratory": False,
                },
            }
        ]
        alerts = collect_operator_alerts(products)
        codes = [a.get("code") for a in alerts]
        self.assertIn("low_confidence_high_risk", codes)


class TestDashboardPayloadOperatorFields(unittest.TestCase):
    def test_empty_inventory_includes_operator_alerts(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            p = build_dashboard_payload(root)
            self.assertIn("operator_alerts", p)
            self.assertEqual(p["operator_alerts"], [])

    def test_product_has_operator_visibility(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "ov_p"
            _write(pr / "product.yaml", _minimal_yaml())
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            payload = build_dashboard_payload(root)
            prod = next(x for x in payload["products"] if x["product_id"] == "ov_p")
            self.assertIn("operator_visibility", prod)
            self.assertEqual(
                prod["operator_visibility"].get("schema"),
                "argus.dashboard_operator_visibility.v1",
            )
            self.assertIn("evidence_density", prod["operator_visibility"])
            # No decisions file: confidence may be null; UI must tolerate
            self.assertNotIn("top_candidate_metadata", prod)


if __name__ == "__main__":
    unittest.main()
