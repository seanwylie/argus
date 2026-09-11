"""Mission provenance blocks (per-product and portfolio mix)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.provenance import (
    MISSION_PROVENANCE_SCHEMA,
    PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
    build_mission_context_for_product,
    build_portfolio_mission_provenance,
    build_repository_mission_context,
)


class TestMissionProvenance(unittest.TestCase):
    def test_repository_mission_context_default_registry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            mc = build_repository_mission_context(root)
            self.assertEqual(mc.get("schema"), MISSION_PROVENANCE_SCHEMA)
            self.assertEqual(mc.get("resolved_mission_id"), "revenue")
            self.assertEqual(mc.get("resolution_scope"), "repository")

    def test_repository_mission_context_current_json_override(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            d = root / "runs" / "mission"
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "education"}), encoding="utf-8"
            )
            mc = build_repository_mission_context(root)
            self.assertEqual(mc.get("resolved_mission_id"), "education")

    def test_product_mission_beats_repo_override(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            d = root / "runs" / "mission"
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "education"}), encoding="utf-8"
            )
            pr = root / "products" / "p_edu"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: p_edu
name: X
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
            mc = build_mission_context_for_product(root, "p_edu")
            self.assertEqual(mc.get("resolved_mission_id"), "engagement")
            self.assertEqual(mc.get("resolution_scope"), "product")

    def test_portfolio_mission_mix(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            for pid, mid in (("a", "revenue"), ("b", "education")):
                pr = root / "products" / pid
                pr.mkdir(parents=True)
                (pr / "product.yaml").write_text(
                    f"""
id: {pid}
name: X
owner:
  team: t
lifecycle:
  stage: idea
mission_id: {mid}
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
            pm = build_portfolio_mission_provenance(root, ["a", "b"])
            self.assertEqual(pm.get("schema"), PORTFOLIO_MISSION_PROVENANCE_SCHEMA)
            mix = pm.get("mission_mix_summary") or {}
            self.assertEqual(set(mix.get("distinct_mission_ids") or []), {"education", "revenue"})
