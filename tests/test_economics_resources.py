"""Resource registry, ingest, and linkage report."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.economics.registry import (
    CONFIG_REGISTRY,
    COST_INGEST,
    RUNS_REGISTRY,
    load_merged_resources,
)
from argus.economics.resources_report import build_resource_report


def _minimal_product_yaml(pid: str) -> str:
    return f"""
id: {pid}
name: T
owner:
  team: t
lifecycle:
  stage: validate
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 50
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 200
  min_activity_threshold: 0
"""


class TestEconomicsResources(unittest.TestCase):
    def test_load_merged_registry_and_ingest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / CONFIG_REGISTRY
            cfg.parent.mkdir(parents=True)
            cfg.write_text(
                json.dumps(
                    {
                        "schema": "argus.resource_registry.v1",
                        "resources": [
                            {
                                "id": "r1",
                                "kind": "lambda",
                                "monthly_cost_usd": 10,
                                "product_id": "p1",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            ing = root / COST_INGEST
            ing.parent.mkdir(parents=True)
            ing.write_text(
                json.dumps(
                    {
                        "resources": [
                            {
                                "id": "r2",
                                "kind": "orphan_bucket",
                                "monthly_cost_usd": 99,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )
            m = load_merged_resources(root)
            self.assertEqual(len(m), 2)
            self.assertEqual(m["r2"].monthly_cost_usd, 99.0)
            self.assertIsNone(m["r2"].product_id)

    def test_ingest_overrides_registry_same_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / CONFIG_REGISTRY).parent.mkdir(parents=True)
            (root / CONFIG_REGISTRY).write_text(
                json.dumps({"resources": [{"id": "r1", "kind": "x", "monthly_cost_usd": 1, "product_id": "p1"}]}),
                encoding="utf-8",
            )
            (root / RUNS_REGISTRY).parent.mkdir(parents=True)
            (root / RUNS_REGISTRY).write_text(
                json.dumps({"resources": [{"id": "r1", "kind": "x", "monthly_cost_usd": 50, "product_id": "p1"}]}),
                encoding="utf-8",
            )
            m = load_merged_resources(root)
            self.assertEqual(m["r1"].monthly_cost_usd, 50.0)
            self.assertEqual(m["r1"].source, "registry")

    def test_report_detects_orphan_and_unknown_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(_minimal_product_yaml("p1"), encoding="utf-8")
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            ing = root / COST_INGEST
            ing.parent.mkdir(parents=True)
            ing.write_text(
                json.dumps(
                    {
                        "resources": [
                            {"id": "orph1", "kind": "s3", "monthly_cost_usd": 5},
                            {"id": "bad", "kind": "x", "monthly_cost_usd": 3, "product_id": "nosuch"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            rep = build_resource_report(root)
            reasons = {o.resource_id: o.reason for o in rep.orphan_resources}
            self.assertIn("orph1", reasons)
            self.assertEqual(reasons["orph1"], "no_product_mapping")
            self.assertIn("bad", reasons)


if __name__ == "__main__":
    unittest.main()
