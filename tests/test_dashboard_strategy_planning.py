"""Dashboard + doctor read-only strategy/planning visibility."""

from __future__ import annotations

import json
import tempfile
import textwrap
import unittest
from pathlib import Path

from argus.dashboard.data import build_dashboard_payload
from argus.dashboard.strategy_planning_read import build_doctor_strategy_planning_section
from argus.orchestrator.artifact_paths import orchestration_latest_path
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA, planning_latest_path
from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA, strategy_latest_path


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_valid_yaml(product_id: str = "sp_p") -> str:
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


class TestDashboardStrategyPlanningSlices(unittest.TestCase):
    def test_graceful_when_artifacts_missing(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "sp_p"
            _write(pr / "product.yaml", _minimal_valid_yaml())
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            p = build_dashboard_payload(root)
            prod = next(x for x in p["products"] if x["product_id"] == "sp_p")
            self.assertFalse(prod["strategy_snapshot"]["present"])
            self.assertFalse(prod["planning_snapshot"]["present"])
            self.assertFalse(prod["orchestration_planning_influence"]["present"])

    def test_surfaces_strategy_planning_and_explainability(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pid = "sp_p"
            pr = root / "products" / pid
            _write(pr / "product.yaml", _minimal_valid_yaml(pid))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            st_path = strategy_latest_path(root, pid)
            st_path.parent.mkdir(parents=True, exist_ok=True)
            st_path.write_text(
                json.dumps(
                    {
                        "schema": STRATEGY_SNAPSHOT_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T00:00:00+00:00",
                        "posture": "explore",
                        "posture_raw": "explore",
                        "skepticism_applied": False,
                        "skepticism_reason": None,
                        "recommended_mode": "run_more_targeted_experiments",
                        "summary": "x",
                    }
                ),
                encoding="utf-8",
            )
            pl_path = planning_latest_path(root, pid)
            pl_path.parent.mkdir(parents=True, exist_ok=True)
            pl_path.write_text(
                json.dumps(
                    {
                        "schema": PLANNING_SNAPSHOT_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T00:00:00+00:00",
                        "planning_mode": "validate",
                        "posture": "explore",
                        "priority_workstreams": ["a: b c"],
                        "recommended_actions": [{"action_type": "experiment", "title": "t"}],
                    }
                ),
                encoding="utf-8",
            )
            orch = orchestration_latest_path(root, pid)
            orch.parent.mkdir(parents=True, exist_ok=True)
            orch.write_text(
                json.dumps(
                    {
                        "product_id": pid,
                        "eligibility_facts": {
                            "planning_mode_considered": "validate",
                            "planning_priority_adjustment_applied": True,
                            "next_action_planning_note": "adjusted head",
                        },
                    }
                ),
                encoding="utf-8",
            )

            p = build_dashboard_payload(root)
            prod = next(x for x in p["products"] if x["product_id"] == pid)
            ss = prod["strategy_snapshot"]
            self.assertTrue(ss["present"])
            self.assertTrue(ss["readable"])
            self.assertEqual(ss["posture"], "explore")
            pl = prod["planning_snapshot"]
            self.assertTrue(pl["present"])
            self.assertEqual(pl["recommended_actions_count"], 1)
            self.assertEqual(pl["planning_mode"], "validate")
            opi = prod["orchestration_planning_influence"]
            self.assertTrue(opi["present"])
            self.assertEqual(opi["planning_mode_considered"], "validate")
            self.assertTrue(opi["planning_priority_adjustment_applied"])
            self.assertEqual(opi["next_action_planning_note"], "adjusted head")

    def test_malformed_strategy_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pid = "sp_p"
            pr = root / "products" / pid
            _write(pr / "product.yaml", _minimal_valid_yaml(pid))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            sp = strategy_latest_path(root, pid)
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text("{", encoding="utf-8")

            p = build_dashboard_payload(root)
            prod = next(x for x in p["products"] if x["product_id"] == pid)
            ss = prod["strategy_snapshot"]
            self.assertTrue(ss["present"])
            self.assertTrue(ss.get("parse_error"))


class TestDoctorStrategyPlanningSection(unittest.TestCase):
    def test_counts_and_planning_without_orchestration_note(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pid = "sp_p"
            pr = root / "products" / pid
            _write(pr / "product.yaml", _minimal_valid_yaml(pid))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            plp = planning_latest_path(root, pid)
            plp.parent.mkdir(parents=True, exist_ok=True)
            plp.write_text(
                json.dumps(
                    {
                        "schema": PLANNING_SNAPSHOT_SCHEMA,
                        "product_id": pid,
                        "planning_mode": "validate",
                        "priority_workstreams": [],
                        "recommended_actions": [],
                    }
                ),
                encoding="utf-8",
            )

            rep = build_doctor_strategy_planning_section(root, [pid])
            self.assertEqual(rep["planning"]["files_present"], 1)
            self.assertEqual(rep["orchestration"]["planning_snapshots_missing_orchestration_state"], 1)
            self.assertTrue(any("orchestration state" in n for n in rep["notes"]))


if __name__ == "__main__":
    unittest.main()
