"""Unit tests for product discovery, loading, and validation."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.discovery import discover_product_yaml_files
from argus.products.inventory import build_inventory
from argus.products.loader import load_yaml_file
from argus.products.paths import ensure_posix_relative, join_under_product
from argus.products.validate import validate_manifest


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_valid_yaml(product_id: str = "p1") -> str:
    # Flush-left YAML so tests can safely append keys without breaking dedent.
    return f"""\
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
"""


class TestDiscovery(unittest.TestCase):
    def test_discovers_only_directories_with_product_yaml(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prod = root / "products"
            _write(prod / "a" / "product.yaml", _minimal_valid_yaml("a"))
            (prod / "empty_dir").mkdir(parents=True)
            found = discover_product_yaml_files(prod)
            self.assertEqual(len(found), 1)
            self.assertEqual(found[0].name, "product.yaml")


class TestLoader(unittest.TestCase):
    def test_invalid_yaml_returns_error(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "product.yaml"
            p.write_text("{ not yaml: [[", encoding="utf-8")
            data, err = load_yaml_file(p)
            self.assertIsNone(data)
            self.assertIsNotNone(err)
            assert err is not None
            self.assertIn("Invalid YAML", err)

    def test_loads_mapping(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "product.yaml"
            _write(p, "id: x\nlifecycle:\n  stage: idea\n")
            data, err = load_yaml_file(p)
            self.assertIsNone(err)
            assert data is not None
            self.assertEqual(data.get("id"), "x")


class TestPathNormalization(unittest.TestCase):
    def test_repo_relative(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            sub = root / "products" / "x"
            sub.mkdir(parents=True)
            rel = ensure_posix_relative(root, sub)
            self.assertEqual(rel.replace("\\", "/"), "products/x")

    def test_join_under_product_rejects_parent_escape(self) -> None:
        with TemporaryDirectory() as tmp:
            base = Path(tmp) / "products" / "p"
            base.mkdir(parents=True)
            with self.assertRaises(ValueError):
                join_under_product(base, "../outside")


class TestValidation(unittest.TestCase):
    def test_missing_lifecycle_stage_error(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            cfg = pr / "product.yaml"
            _write(
                cfg,
                """
                id: x
                owner:
                  team: t
                lifecycle: {}
                metrics: { local_paths: [], primary: [] }
                cost: { monthly_usd: 0, notes: "" }
                signals: [ { type: filesystem, enabled: true } ]
                actions: { start: "./scripts/s.sh", stop: "./scripts/s.sh", analyze: "./scripts/s.sh" }
                constraints: { max_monthly_cost_usd: 1, min_activity_threshold: 0 }
                """,
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNone(res.node)
            self.assertTrue(any("lifecycle.stage" in e for e in res.errors))

    def test_unknown_signal_type(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            cfg = pr / "product.yaml"
            body = _minimal_valid_yaml()
            body = body.replace("filesystem", "not_a_real_signal")
            _write(cfg, body)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertTrue(any("unknown signal type" in e for e in res.errors))

    def test_state_conflicts_with_lifecycle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            cfg = pr / "product.yaml"
            y = """
            id: x
            name: Test
            state: validate
            owner:
              team: test
            lifecycle:
              stage: idea
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
            """
            _write(cfg, y)
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err, msg=err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertTrue(any("conflicts" in e for e in res.errors))

    def test_raw_extensions_must_be_mapping(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            cfg = pr / "product.yaml"
            _write(cfg, _minimal_valid_yaml("x") + "raw_extensions: []\n")
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertTrue(any("raw_extensions must be a mapping" in e for e in res.errors))

    def test_import_state_requires_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            cfg = pr / "product.yaml"
            _write(
                cfg,
                _minimal_valid_yaml("x")
                + "raw_extensions:\n  import_state:\n    schema: wrong.schema\n    foo: bar\n",
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertTrue(any("raw_extensions.import_state.schema" in e for e in res.errors))

    def test_unknown_top_level_key_warns(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "x"
            cfg = pr / "product.yaml"
            _write(cfg, _minimal_valid_yaml("x") + "typo_root_key: 1\n")
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertFalse(res.errors)
            self.assertTrue(any("unknown top-level" in w for w in res.warnings))


class TestInventoryBuild(unittest.TestCase):
    def test_end_to_end_valid_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "good"
            cfg = pr / "product.yaml"
            _write(cfg, _minimal_valid_yaml("good"))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            inv = build_inventory(root)
            self.assertIn("good", inv.valid)
            self.assertEqual(inv.summary.valid_count, 1)
            self.assertEqual(inv.valid["good"].node.product_root, "products/good")


if __name__ == "__main__":
    unittest.main()
