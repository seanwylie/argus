"""Doctor visibility for orchestration ``portfolio_priorities.json`` (read-only)."""

from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from argus.cli.doctor_cmd import cmd_doctor
from argus.orchestrator.portfolio_priorities import PORTFOLIO_PRIORITIES_SCHEMA


def _args(**kwargs: object) -> SimpleNamespace:
    base = {"products_dir": None, "json": True, "strict": False, "validate_artifacts": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: {pid}
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


class TestDoctorPortfolioPriorities(unittest.TestCase):
    def test_missing_artifact_no_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            opp = out["orchestration_portfolio_priorities"]
            self.assertFalse(opp["present"])
            self.assertIsNone(opp["error"])
            self.assertFalse(opp["readable"])
            warns = "\n".join(out.get("warnings") or [])
            self.assertNotIn("portfolio_priorities.json", warns)

    def test_valid_artifact_info_and_json_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            _minimal_product(root, "p2")
            orch = root / "runs" / "orchestration" / "latest"
            orch.mkdir(parents=True)
            payload = {
                "schema": PORTFOLIO_PRIORITIES_SCHEMA,
                "generated_at_utc": "2026-04-12T12:00:00+00:00",
                "recommended_product_id": "p2",
                "recommended_next_action": "refresh_planning",
                "products": [
                    {
                        "product_id": "p2",
                        "rank": 1,
                        "priority_score": 99,
                        "orchestration_status": "eligible",
                        "next_action": "refresh_planning",
                        "priority_reasons": ["reason a"],
                        "evidence_summary": "ev",
                    },
                    {
                        "product_id": "p1",
                        "rank": 2,
                        "priority_score": 50,
                        "orchestration_status": "complete",
                        "next_action": "none",
                        "priority_reasons": [],
                        "evidence_summary": "done",
                    },
                ],
            }
            (orch / "portfolio_priorities.json").write_text(json.dumps(payload), encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            opp = out["orchestration_portfolio_priorities"]
            self.assertTrue(opp["present"])
            self.assertTrue(opp["readable"])
            self.assertIsNone(opp["error"])
            self.assertEqual(opp["recommended_product_id"], "p2")
            self.assertEqual(opp["recommended_next_action"], "refresh_planning")
            self.assertEqual(opp["product_count"], 2)
            self.assertEqual(opp["rank_1_product_id"], "p2")
            self.assertEqual(opp["rank_1_priority_reasons"], ["reason a"])
            infos = "\n".join(out.get("info") or [])
            self.assertIn("Orchestration portfolio priorities", infos)
            self.assertIn("'p2'", infos)
            self.assertIn("refresh_planning", infos)

    def test_malformed_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            orch = root / "runs" / "orchestration" / "latest"
            orch.mkdir(parents=True)
            (orch / "portfolio_priorities.json").write_text("{", encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            opp = out["orchestration_portfolio_priorities"]
            self.assertIsNotNone(opp["error"])
            self.assertFalse(opp["present"])
            warns = out.get("warnings") or []
            self.assertTrue(any("portfolio_priorities.json" in w for w in warns))


if __name__ == "__main__":
    unittest.main()
