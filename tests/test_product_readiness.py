"""Tests for :mod:`argus.portfolio.product_readiness`."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.product_readiness import (
    PRODUCT_READINESS_SCHEMA,
    build_product_readiness_payload,
    render_product_readiness_markdown,
)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
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
""",
        encoding="utf-8",
    )


class TestProductReadiness(unittest.TestCase):
    def test_payload_schema_and_keys(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pr1")
            pl = build_product_readiness_payload(root, "pr1")
            self.assertEqual(pl.get("schema"), PRODUCT_READINESS_SCHEMA)
            self.assertEqual(pl.get("product_id"), "pr1")
            self.assertIn("evidence_maturity_hint", pl)
            self.assertIn("expected_bootstrap_artifacts_relative", pl)
            self.assertIn("signal_contract_summary", pl)
            self.assertIn("project_permissions", pl)
            self.assertIn("permission_gate_summary", pl)
            pg = pl.get("permission_gate_summary") or {}
            self.assertEqual(pg.get("schema"), "argus.permission_gate_summary.v1")
            self.assertIn("one_line", pg)
            pp = pl.get("project_permissions") or {}
            self.assertIn("policy", pp)
            self.assertIn("environment_alignment", pp)
            scs = pl.get("signal_contract_summary") or {}
            self.assertIn("operability_status", scs)
            self.assertIn("optimization_status", scs)
            self.assertEqual(scs.get("signals_latest_surface_state"), "missing_file")
            self.assertIn("builder_task_candidates", scs)
            md = render_product_readiness_markdown(pl)
            self.assertIn("Permission gates (triage)", md)


if __name__ == "__main__":
    unittest.main()
