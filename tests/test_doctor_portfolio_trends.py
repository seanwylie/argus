"""Doctor visibility for ``portfolio_priority_trends.json`` (read-only)."""

from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from argus.cli.doctor_cmd import cmd_doctor
from argus.orchestrator.portfolio_priority_trends import PORTFOLIO_PRIORITY_TRENDS_SCHEMA


def _args(**kwargs: object) -> SimpleNamespace:
    base = {"products_dir": None, "json": True, "strict": False, "validate_artifacts": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        """
id: p1
name: p1
owner:
  team: t
lifecycle:
  stage: validate
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
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")


class TestDoctorPortfolioTrends(unittest.TestCase):
    def test_missing_silent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            pt = out["portfolio_priority_trends"]
            self.assertFalse(pt["present"])
            self.assertIsNone(pt["error"])
            warns = "\n".join(out.get("warnings") or [])
            self.assertNotIn("portfolio_priority_trends.json", warns)

    def test_valid_json_and_info(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            p = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "schema": PORTFOLIO_PRIORITY_TRENDS_SCHEMA,
                        "window_size": 2,
                        "generations_considered": 2,
                        "churn_summary": "x",
                        "portfolio_stability": "stable",
                        "portfolio_stability_score": 0.0,
                        "top_products_to_inspect": ["p1"],
                        "operator_recommendations": ["Inspect p1"],
                        "products": [],
                    }
                ),
                encoding="utf-8",
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            pt = out["portfolio_priority_trends"]
            self.assertTrue(pt["present"])
            self.assertTrue(pt["readable"])
            self.assertEqual(pt["portfolio_stability"], "stable")
            self.assertEqual(pt["top_inspect_first_product_id"], "p1")
            infos = "\n".join(out.get("info") or [])
            self.assertIn("Portfolio priority trends", infos)

    def test_malformed_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            p = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            p.parent.mkdir(parents=True)
            p.write_text("{", encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertIsNotNone(out["portfolio_priority_trends"]["error"])
            self.assertTrue(any("portfolio_priority_trends.json" in w for w in out.get("warnings") or []))


if __name__ == "__main__":
    unittest.main()
