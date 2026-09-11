"""Adapter layer registry, pipeline, and CLI smoke."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.adapters.builtins._wrap import SignalAdapterWrapper
from argus.adapters.builtins.temporal import TemporalIntegrationAdapter
from argus.adapters.pipeline import run_adapter_layer
from argus.adapters.registry import registered_adapters, summary_table
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters import default_builtin_adapters
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product

_REPO = Path(__file__).resolve().parents[1]
_FIXTURE_PRODUCT = _REPO / "tests/fixtures/products/fixture_action_product"


def _run_cli(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "argus.cli.main", *argv],
        cwd=_REPO,
        capture_output=True,
        text=True,
        timeout=120,
    )


class TestSignalAdapterWrapper(unittest.TestCase):
    def test_cannot_instantiate_base_wrapper(self) -> None:
        with self.assertRaises(TypeError):
            SignalAdapterWrapper(None)  # type: ignore[arg-type]

    def test_concrete_subclass_instantiates(self) -> None:
        a = TemporalIntegrationAdapter()
        self.assertEqual(a.adapter_id, "temporal")
        self.assertIsNotNone(a.category)


class TestAdapterRegistry(unittest.TestCase):
    def test_builtin_ids_include_metrics_alias(self) -> None:
        reg = registered_adapters()
        self.assertIn("execution", reg)
        self.assertIn("filesystem", reg)
        self.assertIn("metrics", reg)
        self.assertNotIn("metrics_file", reg)

    def test_summary_table_rows(self) -> None:
        rows = summary_table()
        self.assertGreaterEqual(len(rows), 3)
        for aid in ("execution", "filesystem", "metrics"):
            self.assertIn(aid, rows)
            self.assertIn("category", rows[aid])


class TestAdapterLayerPipeline(unittest.TestCase):
    def test_merge_layer_tags_records(self) -> None:
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

            recs = run_adapter_layer(root, node)
            self.assertTrue(all("adapter_layer" in r.tags for r in recs))

    def test_collect_merge_adapter_layer(self) -> None:
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
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry(default_builtin_adapters())
            base = collect_for_product(root, node, reg, merge_adapter_layer=False)
            merged = collect_for_product(root, node, reg, merge_adapter_layer=True)
            self.assertGreaterEqual(len(merged), len(base))
            layer_only = [r for r in merged if "adapter_layer" in r.tags]
            self.assertGreater(len(layer_only), 0)


class TestAdaptersCli(unittest.TestCase):
    def test_adapters_list_exits_zero(self) -> None:
        r = _run_cli(["adapters", "list"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        self.assertIn("execution", r.stdout)

    def test_adapters_list_json(self) -> None:
        r = _run_cli(["adapters", "list", "--json"])
        self.assertEqual(r.returncode, 0, msg=r.stderr)
        data = json.loads(r.stdout)
        self.assertIn("adapters", data)

    def test_adapters_run_filesystem_smoke(self) -> None:
        with TemporaryDirectory(dir=str(_REPO)) as tmp:
            pdir = Path(tmp) / "products"
            pdir.mkdir(parents=True)
            shutil.copytree(_FIXTURE_PRODUCT, pdir / "fixture_action_product")
            r = _run_cli(
                [
                    "adapters",
                    "run",
                    "filesystem",
                    "--product-id",
                    "fixture_action_product",
                    "--products-dir",
                    str(pdir),
                ]
            )
            self.assertEqual(r.returncode, 0, msg=r.stderr)


if __name__ == "__main__":
    unittest.main()
