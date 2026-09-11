"""Tests for ``argus.products.signal_instrumentation``."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.signal_instrumentation import (
    PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA,
    PROPOSED_SIGNAL_CONTRACT_SCHEMA,
    evaluate_product_signal_instrumentation,
    run_product_signal_instrumentation,
    signal_instrumentation_dir,
    write_product_signal_instrumentation_artifacts,
)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: {pid}
type: saas
owner:
  team: test
lifecycle:
  stage: validate
metrics:
  local_paths: [metrics/]
  primary: []
signals:
  - type: filesystem
    enabled: true
actions:
  analyze: "./scripts/a.sh"
constraints:
  max_monthly_cost_usd: 10
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "a.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "one.txt").write_text("x", encoding="utf-8")


def _mission_config(root: Path) -> None:
    cfg = root / "config" / "mission_profiles.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        """schema: argus.mission_registry.v1
default_mission_id: revenue
profiles:
  revenue:
    id: revenue
    primary_objective: Test.
    drivers: []
    risk_posture: moderate
    weights: {}
""",
        encoding="utf-8",
    )


def _strong_product(root: Path, pid: str) -> None:
    _mission_config(root)
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: {pid}
type: saas
mission_id: revenue
owner:
  team: test
lifecycle:
  stage: validate
metrics:
  local_paths: [metrics/]
  primary: [north_star, errors_5m]
signals:
  - type: filesystem
    enabled: true
signal_manifest:
  schema: argus.product_signal_manifest.v1
  signals:
    - id: m_exec
      category: operational
      source_type: execution
      path: scripts/a.sh
      freshness_sla: 1h
      value_type: gauge
      required_for: pipeline
      trust_level: heuristic
    - id: m_fs
      category: operational
      source_type: filesystem
      path: metrics/one.txt
      freshness_sla: 24h
      value_type: gauge
      required_for: pipeline
      trust_level: heuristic
    - id: m_temp
      category: temporal
      source_type: temporal
      path: metrics/one.txt
      freshness_sla: 1h
      value_type: gauge
      required_for: findings
      trust_level: derived
    - id: m_an
      category: business
      source_type: analytics
      path: metrics/one.txt
      freshness_sla: 1h
      value_type: counter
      required_for: findings
      trust_level: authoritative
    - id: m_health
      category: quality
      source_type: health
      path: metrics/one.txt
      freshness_sla: 15m
      value_type: gauge
      required_for: audit
      trust_level: authoritative
    - id: m_met
      category: operational
      source_type: metrics
      path: metrics/one.txt
      freshness_sla: 15m
      value_type: gauge
      required_for: pipeline
      trust_level: authoritative
    - id: m_cost
      category: cost
      source_type: cost
      path: metrics/one.txt
      freshness_sla: 24h
      value_type: gauge
      required_for: portfolio
      trust_level: heuristic
actions:
  analyze: "./scripts/a.sh"
constraints:
  max_monthly_cost_usd: 10
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "a.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    for i in range(4):
        (pr / "metrics" / f"f{i}.txt").write_text("1", encoding="utf-8")


class TestProductSignalInstrumentation(unittest.TestCase):
    def test_minimal_product_sparse_or_missing_coverage(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            pl = evaluate_product_signal_instrumentation(root, "thin")
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl.get("schema"), PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA)
            self.assertIn(pl.get("instrumentation_status"), ("sparse", "missing", "weak"))
            kinds = {s["kind"] for s in pl.get("synthetic_seed_signals", [])}
            self.assertIn("structural", kinds)
            self.assertIn("derived", kinds)
            self.assertIn("synthetic", kinds)

    def test_strong_product_adequate(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _strong_product(root, "strong")
            pl = evaluate_product_signal_instrumentation(root, "strong")
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl.get("instrumentation_status"), "adequate")
            kinds = {s["kind"] for s in pl.get("synthetic_seed_signals", [])}
            self.assertNotIn("synthetic", kinds)

    def test_proposed_contract_schema_and_dimensions(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            pl = evaluate_product_signal_instrumentation(root, "thin")
            c = pl.get("proposed_signal_contract")
            self.assertIsInstance(c, dict)
            self.assertEqual(c.get("schema"), PROPOSED_SIGNAL_CONTRACT_SCHEMA)
            dims = c.get("dimensions")
            self.assertIsInstance(dims, dict)
            self.assertEqual(
                sorted(dims.keys()),
                sorted(
                    [
                        "activity_usage",
                        "growth_trend",
                        "health_quality",
                        "freshness_recency",
                        "mission_relevant_metrics",
                    ]
                ),
            )
            for _k, v in dims.items():
                self.assertIn("coverage_level", v)
                self.assertIn("suggested_signal_types", v)
                self.assertIn("primary_metric_keys_suggested", v)

    def test_deterministic_dimension_levels_twice(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            a = evaluate_product_signal_instrumentation(root, "thin")
            b = evaluate_product_signal_instrumentation(root, "thin")
            self.assertEqual(
                a.get("dimension_coverage_levels"),
                b.get("dimension_coverage_levels"),
            )
            self.assertEqual(
                a.get("missing_signal_dimensions"),
                b.get("missing_signal_dimensions"),
            )

    def test_seed_labeling(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            pl = evaluate_product_signal_instrumentation(root, "thin")
            for s in pl.get("synthetic_seed_signals", []):
                self.assertIn(s.get("kind"), ("synthetic", "structural", "derived"))
                self.assertIn("id", s)
                self.assertNotIn("real_telemetry", str(s).lower())

    def test_write_artifacts(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            pl = evaluate_product_signal_instrumentation(root, "thin")
            write_product_signal_instrumentation_artifacts(root, pl, run_timestamp_utc="FIXEDTS")
            base = signal_instrumentation_dir(root)
            self.assertTrue((base / "latest" / "thin.json").is_file())
            self.assertTrue((base / "latest" / "thin.md").is_file())
            self.assertTrue((base / "thin__FIXEDTS.json").is_file())
            self.assertTrue((base / "thin__FIXEDTS.md").is_file())
            data = json.loads((base / "latest" / "thin.json").read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA)

    def test_no_save_does_not_write_via_runner(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            run_product_signal_instrumentation(
                root, product_id="thin", write_artifacts=False
            )
            base = signal_instrumentation_dir(root)
            self.assertFalse(base.exists())

    def test_invalid_product_not_ok(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "bad"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text("not: valid: [", encoding="utf-8")
            pl = evaluate_product_signal_instrumentation(root, "bad")
            self.assertFalse(pl.get("ok"))


if __name__ == "__main__":
    unittest.main()
