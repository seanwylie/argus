"""Tests for signal registry and collection."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters import default_builtin_adapters
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product, product_root_path


class TestSignalRegistry(unittest.TestCase):
    def test_default_adapters_cover_types(self) -> None:
        reg = AdapterRegistry(default_builtin_adapters())
        types = {a.signal_type for a in reg.all_adapters()}
        self.assertIn(SignalType.FILESYSTEM, types)
        self.assertIn(SignalType.METRICS, types)
        self.assertIn(SignalType.EXECUTION, types)


class TestCollectMinimalProduct(unittest.TestCase):
    def test_collect_respects_enabled_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(
                "\n".join(
                    [
                        "id: p1",
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
                        "  - type: filesystem",
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
                ),
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNotNone(res.node)
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry(default_builtin_adapters())
            records = collect_for_product(root, node, reg)
            self.assertTrue(all(r.signal_type == SignalType.FILESYSTEM for r in records))
            self.assertTrue(product_root_path(root, node).is_dir())


if __name__ == "__main__":
    unittest.main()
