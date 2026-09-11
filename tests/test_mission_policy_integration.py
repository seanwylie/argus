"""Mission → operator policy integration (bounded; truth layers unchanged)."""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.policy.mission_mapping import apply_mission_profile_to_operator_policy
from argus.policy.operator_policy import (
    clear_operator_policy_cache,
    default_operator_policy,
    load_operator_policy,
    write_operator_policy_effective_artifact,
)


class TestMissionPolicyIntegration(unittest.TestCase):
    def tearDown(self) -> None:
        clear_operator_policy_cache()

    def test_revenue_moderate_matches_numeric_baseline(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            p = load_operator_policy(root)
            d = default_operator_policy()
            self.assertEqual(
                p["quiescence"]["debt_delta_material"], d["quiescence"]["debt_delta_material"]
            )
            self.assertEqual(p["confidence"]["low_threshold"], d["confidence"]["low_threshold"])
            mi = p.get("mission_integration") or {}
            self.assertEqual(mi.get("resolved_mission_id"), "revenue")
            self.assertEqual(mi.get("risk_posture"), "moderate")
            self.assertEqual(mi.get("adjustments_applied"), [])

    def test_education_mission_changes_policy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            d = mission_runs_dir(root)
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "education"}), encoding="utf-8"
            )
            p = load_operator_policy(root)
            self.assertGreater(p["confidence"]["low_threshold"], 0.45)
            self.assertGreater(p["quiescence"]["debt_delta_material"], 0.08)
            mi = p.get("mission_integration") or {}
            self.assertEqual(mi.get("resolved_mission_id"), "education")
            self.assertTrue(mi.get("adjustments_applied"))

    def test_engagement_mission_changes_queue_and_intervention(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            dr = mission_runs_dir(root)
            dr.mkdir(parents=True)
            (dr / "current.json").write_text(
                json.dumps({"mission_id": "engagement"}), encoding="utf-8"
            )
            p = load_operator_policy(root)
            base = default_operator_policy()
            self.assertGreater(
                p["queue_scoring"]["family_points"]["experiments"],
                base["queue_scoring"]["family_points"]["experiments"],
            )
            self.assertGreater(
                p["intervention"]["progression_runs_window"],
                base["intervention"]["progression_runs_window"],
            )
            self.assertEqual(p["mission_integration"]["resolved_mission_id"], "engagement")

    def test_risk_posture_conservative_tightens(self) -> None:
        base = default_operator_policy()
        mission_prof = {
            "id": "revenue",
            "primary_objective": "x",
            "drivers": ["a"],
            "risk_posture": "conservative",
            "schema": "argus.mission_profile.v1",
        }
        pol, block = apply_mission_profile_to_operator_policy(
            base,
            mission_profile=mission_prof,
            resolved_mission_id="revenue",
            resolution_chain=["test"],
        )
        self.assertGreater(pol["confidence"]["low_threshold"], base["confidence"]["low_threshold"])
        self.assertTrue(block.get("adjustments_applied"))

    def test_risk_posture_aggressive_loosens_vs_conservative(self) -> None:
        base = default_operator_policy()
        m_cons = {
            "id": "revenue",
            "primary_objective": "x",
            "drivers": ["a"],
            "risk_posture": "conservative",
            "schema": "argus.mission_profile.v1",
        }
        m_agg = {**m_cons, "risk_posture": "aggressive"}
        p_cons, _ = apply_mission_profile_to_operator_policy(
            copy.deepcopy(base),
            mission_profile=m_cons,
            resolved_mission_id="revenue",
            resolution_chain=[],
        )
        p_agg, _ = apply_mission_profile_to_operator_policy(
            copy.deepcopy(base),
            mission_profile=m_agg,
            resolved_mission_id="revenue",
            resolution_chain=[],
        )
        self.assertLess(p_agg["confidence"]["low_threshold"], p_cons["confidence"]["low_threshold"])

    def test_load_operator_policy_per_product_mission(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            dr = mission_runs_dir(root)
            dr.mkdir(parents=True)
            (dr / "current.json").write_text(
                json.dumps({"mission_id": "education"}), encoding="utf-8"
            )
            pr = root / "products" / "p_m"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: p_m
name: M
owner:
  team: t
lifecycle:
  stage: idea
mission_id: engagement
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
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
            p_repo = load_operator_policy(root)
            p_prod = load_operator_policy(root, product_id="p_m")
            self.assertEqual(p_repo["mission_integration"]["resolved_mission_id"], "education")
            self.assertEqual(p_prod["mission_integration"]["resolved_mission_id"], "engagement")
            self.assertEqual(p_prod["mission_integration"].get("resolution_scope"), "product")

    def test_effective_artifact_includes_mission_integration(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            path = write_operator_policy_effective_artifact(root)
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "argus.operator_policy.v1")
            self.assertIn("mission_integration", data)
            self.assertEqual(
                data["mission_integration"].get("schema"),
                "argus.operator_policy_mission_context.v1",
            )

    def test_truth_layers_do_not_import_operator_policy(self) -> None:
        """Signals, audit, and findings pipelines must not depend on operator policy."""
        repo = Path(__file__).resolve().parents[1]
        for sub in ("findings", "audit", "signals"):
            for path in (repo / "argus" / sub).rglob("*.py"):
                src = path.read_text(encoding="utf-8")
                self.assertNotIn("load_operator_policy", src, msg=str(path))


def mission_runs_dir(root: Path) -> Path:
    return root / "runs" / "mission"
