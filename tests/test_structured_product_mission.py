"""Structured ``product.yaml`` mission (objective / drivers / guardrails)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import product_node_from_dict
from argus.mission.mission import resolve_product_mission
from argus.mission.product_mission import (
    parse_product_mission_from_yaml,
    validate_product_mission_registry,
)
from argus.mission.provenance import (
    build_mission_context_for_product,
    build_portfolio_mission_provenance,
)
from argus.policy.operator_policy import load_operator_policy


def _minimal_product_dict(pid: str, **extra: object) -> dict[str, object]:
    return {
        "id": pid,
        "name": "n",
        "owner": {"team": "t"},
        "lifecycle": {"stage": "idea"},
        "metrics": {"local_paths": [], "primary": []},
        "cost": {"monthly_usd": 0, "notes": ""},
        "signals": [{"type": "filesystem", "enabled": True}],
        "actions": {"start": "./scripts/s.sh", "stop": "./scripts/s.sh", "analyze": "./scripts/s.sh"},
        "constraints": {"max_monthly_cost_usd": 1, "min_activity_threshold": 0},
        "product_root": f"products/{pid}",
        "config_path": f"products/{pid}/product.yaml",
        **extra,
    }


class TestStructuredProductMission(unittest.TestCase):
    def test_legacy_mission_id_parses_to_objective(self) -> None:
        spec, err = parse_product_mission_from_yaml({"mission_id": "education"})
        self.assertIsNone(err)
        assert spec is not None
        self.assertEqual(spec.objective, "education")
        self.assertEqual(spec.drivers, ())

    def test_mission_block_and_mission_id_rejected(self) -> None:
        _, err = parse_product_mission_from_yaml(
            {"mission_id": "revenue", "mission": {"objective": "revenue"}}
        )
        self.assertIsNotNone(err)

    def test_duplicate_profile_across_roles_rejected(self) -> None:
        _, err = parse_product_mission_from_yaml(
            {
                "mission": {
                    "objective": "revenue",
                    "drivers": ["education"],
                    "guardrails": ["education"],
                }
            }
        )
        self.assertIsNotNone(err)

    def test_invalid_profile_id_in_registry_validation(self) -> None:
        spec2, _ = parse_product_mission_from_yaml({"mission": {"objective": "not_a_real_profile_xyz"}})
        assert spec2 is not None
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            err = validate_product_mission_registry(root, spec2)
            self.assertIsNotNone(err)

    def test_policy_differs_with_driver_overlay(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            p = root / "products" / "pdrv"
            p.mkdir(parents=True)
            (p / "scripts").mkdir()
            (p / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (p / "product.yaml").write_text(
                """
id: pdrv
name: X
owner:
  team: t
lifecycle:
  stage: idea
mission:
  objective: revenue
  drivers: [education]
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
            pol_drv = load_operator_policy(root, product_id="pdrv")
            (p / "product.yaml").write_text(
                (p / "product.yaml").read_text().replace(
                    "mission:\n  objective: revenue\n  drivers: [education]\n",
                    "mission_id: revenue\n",
                ),
                encoding="utf-8",
            )
            pol_plain = load_operator_policy(root, product_id="pdrv")
        self.assertNotEqual(
            pol_drv["confidence"]["low_threshold"],
            pol_plain["confidence"]["low_threshold"],
        )

    def test_mixed_portfolio_provenance_driver_counts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()

            def write_product(pid: str, content: str) -> None:
                pr = root / "products" / pid
                pr.mkdir(parents=True)
                (pr / "scripts").mkdir()
                (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
                (pr / "product.yaml").write_text(content, encoding="utf-8")

            write_product(
                "pa",
                """
id: pa
name: A
owner: { team: t }
lifecycle: { stage: idea }
mission: { objective: revenue, drivers: [education] }
metrics: { local_paths: [], primary: [] }
cost: { monthly_usd: 0, notes: "" }
signals: [ { type: filesystem, enabled: true } ]
actions: { start: "./scripts/s.sh", stop: "./scripts/s.sh", analyze: "./scripts/s.sh" }
constraints: { max_monthly_cost_usd: 1, min_activity_threshold: 0 }
""",
            )
            write_product(
                "pb",
                """
id: pb
name: B
owner: { team: t }
lifecycle: { stage: idea }
mission_id: engagement
metrics: { local_paths: [], primary: [] }
cost: { monthly_usd: 0, notes: "" }
signals: [ { type: filesystem, enabled: true } ]
actions: { start: "./scripts/s.sh", stop: "./scripts/s.sh", analyze: "./scripts/s.sh" }
constraints: { max_monthly_cost_usd: 1, min_activity_threshold: 0 }
""",
            )
            pl = build_portfolio_mission_provenance(root, ["pa", "pb"])
            mix = pl["mission_mix_summary"]
            self.assertEqual(mix["driver_profile_counts"].get("education"), 1)
            mc = build_mission_context_for_product(root, "pa")
            self.assertEqual(mc.get("mission_objective"), "revenue")
            self.assertEqual(mc.get("mission_drivers"), ["education"])

    def test_product_node_structured_mission_field(self) -> None:
        d = _minimal_product_dict(
            "px",
            mission={"objective": "revenue", "drivers": ["education"], "guardrails": ["engagement"]},
        )
        node = product_node_from_dict(d)
        self.assertEqual(node.mission_id, "revenue")
        assert node.mission is not None
        self.assertEqual(node.mission.objective, "revenue")
        self.assertEqual(node.mission.drivers, ("education",))

    def test_product_node_legacy_only_has_mission_none(self) -> None:
        d = _minimal_product_dict("py", mission_id="education")
        node = product_node_from_dict(d)
        self.assertEqual(node.mission_id, "education")
        self.assertIsNone(node.mission)

    def test_resolve_payload_has_structured_mission(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            pr = root / "products" / "pz"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir()
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(
                """
id: pz
name: Z
owner:
  team: t
lifecycle:
  stage: idea
mission:
  objective: revenue
  guardrails: [education]
  risk_posture: conservative
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
            eff = resolve_product_mission(root, "pz")
            sm = eff.get("structured_mission")
            assert isinstance(sm, dict)
            self.assertEqual(sm.get("objective"), "revenue")
            self.assertEqual(sm.get("guardrails"), ["education"])
            self.assertEqual(sm.get("effective_risk_posture"), "conservative")
