"""Doctor checks for doctrine, autonomy, approvals, capabilities."""

from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from argus.cli.doctor_cmd import cmd_doctor


def _args(**kwargs: object) -> SimpleNamespace:
    base = {"products_dir": None, "json": False, "strict": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


class TestDoctorDoctrine(unittest.TestCase):
    def test_invalid_doctrine_is_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "pd"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: pd
name: pd
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
            (pr / "doctrine.yaml").write_text("not: valid: schema\n", encoding="utf-8")

            rc = cmd_doctor(root, _args())
            self.assertEqual(rc, 1)


class TestDoctorAutonomyJson(unittest.TestCase):
    def test_bad_autonomy_mode_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            ad = root / "runs" / "autonomy"
            ad.mkdir(parents=True)
            (ad / "autonomy.json").write_text(
                json.dumps({"schema": "argus.autonomy.v1", "mode": "nope"}),
                encoding="utf-8",
            )
            rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)


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


class TestDoctorDecisionAssessmentHints(unittest.TestCase):
    def test_info_when_decisions_without_assessment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            dl = root / "runs" / "decisions" / "latest"
            dl.mkdir(parents=True)
            (dl / "pd.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.decisions_bundle.v1",
                        "product_id": "pd",
                        "generated_at_utc": "2026-01-10T12:00:00+00:00",
                        "repo_root": str(root),
                        "lifecycle": {},
                        "candidates": [],
                    }
                ),
                encoding="utf-8",
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            infos = "\n".join(out.get("info") or [])
            self.assertIn("confidence assess", infos)

    def test_warning_high_escalation_pressure(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            ad = root / "runs" / "decision_assessment" / "latest"
            ad.mkdir(parents=True)
            (ad / "pd.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.decision_context_assessment.v1",
                        "product_id": "pd",
                        "escalation_pressure": 0.9,
                        "assessed_at_utc": "2026-01-10T12:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            warns = " ".join(out.get("warnings") or [])
            self.assertIn("escalation_pressure", warns)

    def test_malformed_ideas_bundle_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            ip = root / "runs" / "ideas" / "latest.json"
            ip.parent.mkdir(parents=True)
            ip.write_text("{not json", encoding="utf-8")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            warns = " ".join(out.get("warnings") or [])
            self.assertIn("ideas/latest.json", warns)


if __name__ == "__main__":
    unittest.main()
