"""Execution feedback adapter (runs/execution → SignalRecord)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters.execution import ExecutionAdapter
from argus.signals.adapters.execution_outcomes import PROVENANCE_EXECUTION_OUTCOME
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product


class TestExecutionAdapter(unittest.TestCase):
    def test_execution_records_from_runs_execution(self) -> None:
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
                        "  monthly_usd: 10",
                        "  notes: ''",
                        "signals:",
                        "  - type: execution",
                        "    enabled: true",
                        "actions:",
                        '  start: "./scripts/s.sh"',
                        '  stop: "./scripts/s.sh"',
                        '  analyze: "./scripts/s.sh"',
                        "constraints:",
                        "  max_monthly_cost_usd: 100",
                        "  min_activity_threshold: 0",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            ex = root / "runs" / "execution" / "p1"
            ex.mkdir(parents=True)
            payload = {
                "schema": "argus.execution_record.v1",
                "product_id": "p1",
                "success": False,
                "duration_seconds": 2.5,
                "error": "command not found",
                "finished_at_utc": "2026-04-12T12:00:00+00:00",
            }
            (ex / "run_a.json").write_text(json.dumps(payload), encoding="utf-8")

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNotNone(res.node)
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry([ExecutionAdapter()])
            records = collect_for_product(root, node, reg)
            self.assertEqual(len(records), 1)
            r = records[0]
            self.assertEqual(r.signal_type, SignalType.EXECUTION)
            self.assertEqual(r.source, "execution")
            self.assertFalse(r.payload["execution_success"])
            self.assertTrue(r.payload["execution_failure"])
            self.assertEqual(r.payload["execution_duration"], 2.5)
            self.assertEqual(r.payload["execution_error"], "command not found")
            self.assertEqual(r.payload.get("provenance"), PROVENANCE_EXECUTION_OUTCOME)
            self.assertIs(r.payload.get("synthetic"), False)
            self.assertEqual(r.payload.get("outcome_direction"), "failure")
            self.assertIn("execution_outcome_id", r.payload)


if __name__ == "__main__":
    unittest.main()
