"""Product signal manifest schema and validation."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.signal_manifest import PRODUCT_SIGNAL_MANIFEST_SCHEMA
from argus.products.inventory import build_inventory
from argus.products.loader import load_yaml_file
from argus.products.signal_manifest import (
    load_product_signal_manifest,
    validate_signal_manifest_dict,
)
from argus.products.validate import validate_manifest


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_product_yaml(product_id: str = "p1") -> str:
    return f"""
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


_VALID_SIGNALS_YAML = """
schema: {schema}
product_id: p1
signals:
  - id: m1
    category: operational
    source_type: metrics
    path: metrics/
    source_ref: adapter:local
    freshness_sla: 24h
    value_type: gauge
    required_for: pipeline
    trust_level: authoritative
"""

def _product_yaml_with_inline_manifest(schema: str) -> str:
    return f"""
    id: p1
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
    signal_manifest:
      schema: {schema}
      signals:
        - id: m1
          category: operational
          source_type: metrics
          path: metrics/
          source_ref: adapter:local
          freshness_sla: 24h
          value_type: gauge
          required_for: pipeline
          trust_level: authoritative
    """


class TestValidateSignalManifestDict(unittest.TestCase):
    def test_valid_minimal_manifest(self) -> None:
        errs, warns, m = validate_signal_manifest_dict(
            {
                "schema": PRODUCT_SIGNAL_MANIFEST_SCHEMA,
                "signals": [
                    {
                        "id": "x",
                        "category": "quality",
                        "source_type": "filesystem",
                        "path": "src/",
                        "freshness_sla": "best_effort",
                        "value_type": "boolean",
                        "required_for": "audit",
                        "trust_level": "heuristic",
                    },
                ],
            },
        )
        self.assertEqual(errs, [])
        self.assertIsNotNone(m)
        assert m is not None
        self.assertEqual(len(m.signals), 1)
        self.assertEqual(m.signals[0].id, "x")

    def test_rejects_missing_source_ref_and_path(self) -> None:
        errs, _, m = validate_signal_manifest_dict(
            {
                "schema": PRODUCT_SIGNAL_MANIFEST_SCHEMA,
                "signals": [
                    {
                        "id": "x",
                        "category": "quality",
                        "source_type": "filesystem",
                        "freshness_sla": "1h",
                        "value_type": "string",
                        "required_for": "none",
                        "trust_level": "unknown",
                    },
                ],
            },
        )
        self.assertIsNone(m)
        self.assertTrue(any("source_ref or path" in e for e in errs))

    def test_rejects_duplicate_ids(self) -> None:
        raw = {
            "signals": [
                {
                    "id": "dup",
                    "category": "custom",
                    "source_type": "custom",
                    "source_ref": "x",
                    "freshness_sla": "15m",
                    "value_type": "json",
                    "required_for": "findings",
                    "trust_level": "derived",
                },
                {
                    "id": "dup",
                    "category": "custom",
                    "source_type": "custom",
                    "source_ref": "y",
                    "freshness_sla": "15m",
                    "value_type": "json",
                    "required_for": "findings",
                    "trust_level": "derived",
                },
            ],
        }
        errs, _, m = validate_signal_manifest_dict(raw)
        self.assertIsNone(m)
        self.assertTrue(any("duplicate" in e for e in errs))

    def test_rejects_bad_schema(self) -> None:
        errs, _, m = validate_signal_manifest_dict({"schema": "wrong", "signals": []})
        self.assertIsNone(m)
        self.assertTrue(any("schema" in e.lower() for e in errs))


class TestLoadProductSignalManifest(unittest.TestCase):
    def test_signals_yaml_overrides_inline_with_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            cfg = pr / "product.yaml"
            _write(cfg, _minimal_product_yaml("p1"))
            (pr / "metrics").mkdir(parents=True)
            _write(pr / "scripts" / "s.sh", "#!/bin/sh\necho\n" + "x" * 60)
            _write(
                pr / "signals.yaml",
                _VALID_SIGNALS_YAML.format(schema=PRODUCT_SIGNAL_MANIFEST_SCHEMA),
            )
            py_data, err = load_yaml_file(cfg)
            assert err is None and py_data is not None
            py_data["signal_manifest"] = {
                "schema": PRODUCT_SIGNAL_MANIFEST_SCHEMA,
                "signals": [],
            }
            m, errs, warns = load_product_signal_manifest(
                product_root=pr,
                product_yaml=py_data,
                product_id="p1",
            )
            self.assertEqual(errs, [])
            self.assertIsNotNone(m)
            assert m is not None
            self.assertEqual(len(m.signals), 1)
            self.assertTrue(any("ignored" in w for w in warns))

    def test_invalid_signals_yaml_blocks_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "bad"
            _write(pr / "product.yaml", _minimal_product_yaml("bad"))
            _write(pr / "scripts" / "s.sh", "#!/bin/sh\necho\n" + "x" * 60)
            _write(
                pr / "signals.yaml",
                """
                schema: argus.product_signal_manifest.v1
                product_id: bad
                signals:
                  - id: only
                    category: operational
                    source_type: metrics
                    freshness_sla: 1h
                    value_type: gauge
                    required_for: pipeline
                    trust_level: authoritative
                """,
            )
            inv = build_inventory(root)
            self.assertNotIn("bad", inv.valid)


class TestValidateManifestIntegration(unittest.TestCase):
    def test_inline_manifest_on_node(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            _write(pr / "scripts" / "s.sh", "#!/bin/sh\necho\n" + "x" * 60)
            (pr / "metrics").mkdir(parents=True)
            _write(pr / "product.yaml", _product_yaml_with_inline_manifest(PRODUCT_SIGNAL_MANIFEST_SCHEMA))
            inv = build_inventory(root)
            self.assertIn("p1", inv.valid)
            node = inv.valid["p1"].node
            self.assertIsNotNone(node.signal_manifest)
            assert node.signal_manifest is not None
            self.assertEqual(node.signal_manifest.signals[0].id, "m1")

    def test_path_escape_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p1"
            _write(pr / "scripts" / "s.sh", "#!/bin/sh\necho\n" + "x" * 60)
            (pr / "metrics").mkdir(exist_ok=True)
            _write(
                pr / "product.yaml",
                """
                id: p1
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
                signal_manifest:
                  schema: argus.product_signal_manifest.v1
                  signals:
                    - id: bad
                      category: operational
                      source_type: metrics
                      path: "../outside"
                      freshness_sla: 1h
                      value_type: gauge
                      required_for: pipeline
                      trust_level: authoritative
                """,
            )
            raw, err = load_yaml_file(pr / "product.yaml")
            assert raw is not None and err is None
            res = validate_manifest(
                raw,
                repo_root=root,
                product_root=pr,
                config_path=pr / "product.yaml",
            )
            self.assertTrue(any("path" in e for e in res.errors))
