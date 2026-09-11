"""Product signal manifest → collection dispatch, canonical rows, and gap records."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters import default_builtin_adapters
from argus.signals.manifest_collect import PLACEHOLDER_SOURCE, placeholder_signal_id
from argus.signals.normalize import attach_canonical_to_records
from argus.signals.persistence import save_collection
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product


def _base_product_yaml() -> str:
    return "\n".join(
        [
            "id: pm1",
            "name: PM",
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
            "signals: []",
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


def _signals_yaml_metrics_pair() -> str:
    return "\n".join(
        [
            "schema: argus.product_signal_manifest.v1",
            "product_id: pm1",
            "signals:",
            "  - id: m_ok",
            "    category: operational",
            "    source_type: metrics",
            "    path: metrics/present.json",
            "    freshness_sla: 1h",
            "    value_type: json",
            "    required_for: pipeline",
            "    trust_level: authoritative",
            "  - id: m_miss",
            "    category: operational",
            "    source_type: metrics",
            "    path: metrics/absent.json",
            "    freshness_sla: 1h",
            "    value_type: json",
            "    required_for: pipeline",
            "    trust_level: authoritative",
            "",
        ]
    )


def _signals_yaml_logs() -> str:
    return "\n".join(
        [
            "schema: argus.product_signal_manifest.v1",
            "product_id: pm1",
            "signals:",
            "  - id: log_line",
            "    category: operational",
            "    source_type: logs",
            "    path: logs/app.log",
            "    freshness_sla: 1h",
            "    value_type: string",
            "    required_for: audit",
            "    trust_level: unknown",
            "",
        ]
    )


class TestManifestCollectionIntegration(unittest.TestCase):
    def test_manifest_enables_metrics_and_emits_missing_placeholder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "pm1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "metrics").mkdir()
            (pr / "metrics" / "present.json").write_text(json.dumps({"x": 1}), encoding="utf-8")

            (pr / "product.yaml").write_text(_base_product_yaml(), encoding="utf-8")
            (pr / "signals.yaml").write_text(_signals_yaml_metrics_pair(), encoding="utf-8")

            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(
                raw,
                repo_root=root,
                product_root=pr,
                config_path=pr / "product.yaml",
            )
            self.assertIsNotNone(res.node)
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry(default_builtin_adapters())
            records = collect_for_product(root, node, reg)
            ids = {r.id for r in records}
            decl_id = placeholder_signal_id("pm1", "m_miss")
            self.assertIn(decl_id, ids)
            gap = next(r for r in records if r.id == decl_id)
            self.assertEqual(gap.source, PLACEHOLDER_SOURCE)
            self.assertEqual(gap.payload.get("collection_status"), "missing")
            self.assertEqual(gap.signal_type, SignalType.METRICS)

            matched = [
                r
                for r in records
                if r.payload.get("file") == "metrics/present.json"
                or (
                    isinstance(r.payload.get("file"), str)
                    and r.payload["file"].replace("\\\\", "/").endswith("metrics/present.json")
                )
            ]
            self.assertTrue(matched)

    def test_unsupported_source_type_placeholder(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "pm1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            (pr / "product.yaml").write_text(_base_product_yaml(), encoding="utf-8")
            (pr / "signals.yaml").write_text(_signals_yaml_logs(), encoding="utf-8")

            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(
                raw,
                repo_root=root,
                product_root=pr,
                config_path=pr / "product.yaml",
            )
            self.assertIsNotNone(res.node)
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry(default_builtin_adapters())
            records = collect_for_product(root, node, reg)
            decl_id = placeholder_signal_id("pm1", "log_line")
            gap = next(r for r in records if r.id == decl_id)
            self.assertEqual(gap.payload.get("collection_status"), "unsupported_source_type")

    def test_save_collection_canonical_for_gap_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "pm1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            (pr / "product.yaml").write_text(_base_product_yaml(), encoding="utf-8")
            (pr / "signals.yaml").write_text(_signals_yaml_logs(), encoding="utf-8")

            raw, err = load_yaml_file(pr / "product.yaml")
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(
                raw,
                repo_root=root,
                product_root=pr,
                config_path=pr / "product.yaml",
            )
            assert res.node is not None
            node = res.node
            reg = AdapterRegistry(default_builtin_adapters())
            records = collect_for_product(root, node, reg)
            _, normalized = save_collection(
                root,
                "pm1",
                records,
                signal_manifest=node.signal_manifest,
                product_root=pr.resolve(),
            )
            gap = next(r for r in normalized if r.source == PLACEHOLDER_SOURCE)
            self.assertIsNotNone(gap.canonical)
            assert gap.canonical is not None
            self.assertEqual(gap.canonical.collection_status, "unsupported_source_type")
            self.assertEqual(gap.canonical.provenance.get("manifest_signal_id"), "log_line")

    def test_explicit_payload_collection_status_preferred(self) -> None:
        from datetime import datetime, timezone

        from argus.core.models.signal import SignalRecord

        col = datetime(2026, 2, 1, 0, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id="sig-x",
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"collection_status": "missing", "manifest_signal_id": "m1"},
        )
        out = attach_canonical_to_records([r], col)
        self.assertEqual(out[0].canonical.collection_status, "missing")


if __name__ == "__main__":
    unittest.main()
