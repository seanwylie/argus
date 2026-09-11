"""Tests for local snapshot signal adapter (metrics/snapshots/local).

This is **not** the product-level ``argus.product_signal_manifest.v1`` (``signals.yaml`` /
``product.yaml`` ``signal_manifest``). The adapter manifest lists files under
``metrics/snapshots/local/`` only; see ``argus.local_snapshot_manifest.v1``.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters.local_snapshots import LocalSnapshotAdapter
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product


def _minimal_manifest(product_id: str = "p1") -> str:
    return "\n".join(
        [
            f"id: {product_id}",
            "name: P",
            "owner:",
            "  team: t",
            "lifecycle:",
            "  stage: idea",
            "metrics:",
            "  local_paths: []",
            "  primary: []",
            "cost:",
            "  monthly_usd: 0",
            "  notes: ''",
            "signals:",
            "  - type: custom",
            "    enabled: true",
            "actions:",
            '  start: "./scripts/s.sh"',
            '  stop: "./scripts/s.sh"',
            '  analyze: "./scripts/s.sh"',
            "constraints:",
            "  max_monthly_cost_usd: 1",
            "  min_activity_threshold: 0",
            "",
        ]
    )


class TestLocalSnapshotsHappy(unittest.TestCase):
    def test_json_snapshot_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")

            snap_dir = pr / "metrics" / "snapshots" / "local"
            snap_dir.mkdir(parents=True)
            (snap_dir / "summary_metrics.json").write_text(
                '{"observed_at": "2025-06-01T12:00:00Z", "kpi": 42}\n',
                encoding="utf-8",
            )

            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            r = records[0]
            self.assertEqual(r.signal_type, SignalType.CUSTOM)
            self.assertEqual(r.source, "local_snapshots")
            self.assertEqual(r.payload.get("collection_status"), "ok")
            self.assertEqual(r.payload.get("source_kind"), "summary")
            self.assertIn("provenance", r.payload)
            self.assertEqual(r.payload["data"].get("kpi"), 42)

    def test_envelope_schema_v1(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")
            snap_dir = pr / "metrics" / "snapshots" / "local"
            snap_dir.mkdir(parents=True)
            (snap_dir / "x.json").write_text(
                """{
  "schema": "argus.local_snapshot.v1",
  "product_id": "p1",
  "observed_at": "2025-01-02T00:00:00Z",
  "snapshot_kind": "artifact",
  "data": {"note": "hello"}
}
""",
                encoding="utf-8",
            )
            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].payload.get("schema"), "argus.local_snapshot.v1")
            self.assertEqual(records[0].payload["data"].get("snapshot_kind"), "artifact")
            self.assertEqual(records[0].payload["data"].get("note"), "hello")

    def test_csv_snapshot_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")
            snap_dir = pr / "metrics" / "snapshots" / "local"
            snap_dir.mkdir(parents=True)
            (snap_dir / "manual_export.csv").write_text(
                "product_id,metric,value\np1,visits,10\n",
                encoding="utf-8",
            )
            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].payload.get("collection_status"), "ok")
            self.assertEqual(records[0].payload.get("source_kind"), "manual")
            rows = records[0].payload["data"]["rows"]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].get("metric"), "visits")


class TestLocalSnapshotsDegraded(unittest.TestCase):
    def test_invalid_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")
            snap_dir = pr / "metrics" / "snapshots" / "local"
            snap_dir.mkdir(parents=True)
            (snap_dir / "bad.json").write_text("{not json", encoding="utf-8")
            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].payload.get("collection_status"), "invalid_schema")

    def test_missing_required_manifest_entry(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")
            snap_dir = pr / "metrics" / "snapshots" / "local"
            snap_dir.mkdir(parents=True)
            (snap_dir / "manifest.json").write_text(
                """{
  "schema": "argus.local_snapshot_manifest.v1",
  "entries": [
    {"rel_path": "nope.json", "source_kind": "json_snapshot", "required": true}
  ]
}
""",
                encoding="utf-8",
            )
            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].payload.get("collection_status"), "missing")
            self.assertIn("nope", records[0].payload.get("detail", ""))

    def test_runs_signals_drop_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")
            drop = root / "runs" / "signals" / "snapshots" / "products" / "p1"
            drop.mkdir(parents=True)
            (drop / "summary_drop.json").write_text(
                '{"observed_at": "2025-06-01T12:00:00Z", "note": "from runs"}',
                encoding="utf-8",
            )
            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].payload.get("collection_status"), "ok")
            self.assertIn("runs/signals/snapshots", records[0].payload.get("source_ref", ""))

    def test_oversized_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_minimal_manifest(), encoding="utf-8")
            snap_dir = pr / "metrics" / "snapshots" / "local"
            snap_dir.mkdir(parents=True)
            huge = "x" * (512 * 1024 + 10)
            (snap_dir / "huge.json").write_text('{"a":"' + huge + '"}', encoding="utf-8")
            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=pr / "product.yaml")
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry([LocalSnapshotAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0].payload.get("collection_status"), "oversized")


if __name__ == "__main__":
    unittest.main()
