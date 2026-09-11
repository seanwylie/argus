"""Tests for :mod:`argus.mission.mission`."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.mission import (
    MISSION_EFFECTIVE_SCHEMA,
    MISSION_PROFILE_SCHEMA,
    load_default_mission,
    load_mission_by_id,
    load_mission_registry,
    mission_runs_dir,
    resolve_creation_mission,
    resolve_effective_mission,
    resolve_effective_mission_for_product,
    resolve_effective_mission_id,
    resolve_effective_mission_id_for_product,
    resolve_global_mission,
    resolve_product_mission,
    write_mission_effective_artifact,
)


class TestMission(unittest.TestCase):
    def test_default_mission_loads_without_config_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            m = load_default_mission(root)
            self.assertEqual(m.get("schema"), MISSION_PROFILE_SCHEMA)
            self.assertEqual(m.get("id"), "revenue")

    def test_load_mission_by_id_education(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            m = load_mission_by_id(root, "education")
            self.assertEqual(m["id"], "education")
            self.assertEqual(m["risk_posture"], "conservative")

    def test_override_via_current_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = mission_runs_dir(root)
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "engagement"}), encoding="utf-8"
            )
            mid, chain = resolve_effective_mission_id(root)
            self.assertEqual(mid, "engagement")
            self.assertTrue(any("current.json" in c for c in chain))

    def test_env_override_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = mission_runs_dir(root)
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "education"}), encoding="utf-8"
            )
            old = os.environ.get("ARGUS_MISSION_ID")
            try:
                os.environ["ARGUS_MISSION_ID"] = "engagement"
                mid, _chain = resolve_effective_mission_id(root)
                self.assertEqual(mid, "engagement")
            finally:
                if old is None:
                    del os.environ["ARGUS_MISSION_ID"]
                else:
                    os.environ["ARGUS_MISSION_ID"] = old

    def test_invalid_mission_id_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError) as ctx:
                load_mission_by_id(root, "nonexistent_mission_xyz")
            self.assertIn("unknown mission_id", str(ctx.exception).lower())

    def test_write_mission_effective_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = write_mission_effective_artifact(root)
            self.assertTrue(p.is_file())
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(raw.get("schema"), MISSION_EFFECTIVE_SCHEMA)
            self.assertEqual(raw["mission"]["schema"], MISSION_PROFILE_SCHEMA)

    def test_resolve_effective_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = resolve_effective_mission(root)
            self.assertEqual(pl["schema"], MISSION_EFFECTIVE_SCHEMA)
            self.assertEqual(pl["resolved_mission_id"], "revenue")
            self.assertEqual(pl.get("resolution_scope"), "repository")

    def test_resolve_global_and_product_aliases_match(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            g = resolve_global_mission(root)
            e = resolve_effective_mission(root)
            self.assertEqual(g["resolved_mission_id"], e["resolved_mission_id"])
            c = resolve_creation_mission(root)
            self.assertEqual(c["resolved_mission_id"], g["resolved_mission_id"])
            pr = root / "products" / "p_alias"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: p_alias
name: X
owner:
  team: t
lifecycle:
  stage: idea
mission_id: education
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
            a = resolve_product_mission(root, "p_alias")
            b = resolve_effective_mission_for_product(root, "p_alias")
            self.assertEqual(a["resolved_mission_id"], b["resolved_mission_id"])
            self.assertEqual(a["resolved_mission_id"], "education")

    def test_product_mission_overrides_repo_chain(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            d = mission_runs_dir(root)
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "education"}), encoding="utf-8"
            )
            pr = root / "products" / "px"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: px
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
            mid, ch = resolve_effective_mission_id_for_product(root, "px")
            self.assertEqual(mid, "engagement")
            self.assertTrue(any("product.yaml" in x for x in ch))
            pl = resolve_effective_mission_for_product(root, "px")
            self.assertEqual(pl.get("resolution_scope"), "product")

    def test_registry_from_repo_config_merges(self) -> None:
        root = Path(__file__).resolve().parents[1]
        reg = load_mission_registry(root)
        self.assertEqual(reg.get("schema"), "argus.mission_registry.v1")
        self.assertIn("revenue", reg.get("profiles") or {})

    def test_invalid_override_in_current_json_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = mission_runs_dir(root)
            d.mkdir(parents=True)
            (d / "current.json").write_text(
                json.dumps({"mission_id": "not_a_real_id"}), encoding="utf-8"
            )
            with self.assertRaises(ValueError) as ctx:
                resolve_effective_mission(root)
            self.assertIn("unknown mission_id", str(ctx.exception).lower())
