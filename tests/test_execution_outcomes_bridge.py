"""Execution outcomes → canonical signals bridge (deterministic; no separate learning fork)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.findings.engine import generate_findings
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters.execution_outcomes import (
    PROVENANCE_EXECUTION_OUTCOME,
    execution_outcome_signal_identity,
    generate_signals_from_execution_outcomes,
)
from argus.signals.persistence import save_collection
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product


class TestExecutionOutcomesBridge(unittest.TestCase):
    def test_generate_signals_maps_outcome_to_signal_record(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eob"
            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("run_a.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.execution_record.v1",
                        "product_id": pid,
                        "success": True,
                        "duration_seconds": 3.0,
                        "finished_at_utc": "2026-04-12T12:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            recs = generate_signals_from_execution_outcomes(root, pid)
            self.assertEqual(len(recs), 1)
            r = recs[0]
            self.assertEqual(r.signal_type, SignalType.EXECUTION)
            self.assertEqual(r.source, "execution")
            self.assertTrue(r.payload.get("execution_success"))
            self.assertEqual(r.payload.get("provenance"), PROVENANCE_EXECUTION_OUTCOME)
            self.assertIs(r.payload.get("synthetic"), False)
            self.assertEqual(r.payload.get("outcome_direction"), "success")
            self.assertEqual(r.payload.get("outcome_magnitude"), 3.0)
            self.assertIn("observed_at_utc", r.payload)
            self.assertIn("execution_outcome", r.tags)
            rel = r.payload.get("execution_record_relpath")
            self.assertEqual(
                r.payload.get("execution_outcome_id"),
                execution_outcome_signal_identity(pid, str(rel)),
            )
            self.assertEqual(r.payload.get("signal_id_hint"), r.payload.get("execution_outcome_id"))

    def test_orchestration_artifact_provenance_preserved_as_artifact_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eob2"
            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("orch.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.orchestration_execution_feedback.v1",
                        "product_id": pid,
                        "action_id": "signals_collect",
                        "success": True,
                        "provenance": {"source": "orchestration_step_executor", "source_ref": "x"},
                    }
                ),
                encoding="utf-8",
            )
            recs = generate_signals_from_execution_outcomes(root, pid)
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0].payload.get("provenance"), PROVENANCE_EXECUTION_OUTCOME)
            self.assertIsInstance(recs[0].payload.get("artifact_provenance"), dict)

    def test_collect_for_product_includes_execution_outcomes_without_yaml_gate(self) -> None:
        """Execution outcomes are merged via :class:`ExecutionAdapter` (always enabled)."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eob3"
            pr = root / "products" / pid
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(
                "\n".join(
                    [
                        f"id: {pid}",
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
                        "  - type: filesystem",
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

            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("x.json").write_text(
                json.dumps({"product_id": pid, "success": True}),
                encoding="utf-8",
            )

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            self.assertIsNotNone(res.node)
            assert res.node is not None
            node = res.node

            from argus.signals.adapters import default_builtin_adapters

            reg = AdapterRegistry(default_builtin_adapters())
            rows = collect_for_product(root, node, reg)
            ex_rows = [r for r in rows if r.payload.get("provenance") == PROVENANCE_EXECUTION_OUTCOME]
            self.assertEqual(len(ex_rows), 1)

    def test_save_collection_end_to_end_preserves_execution_outcome_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eob4"
            pr = root / "products" / pid
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(
                "\n".join(
                    [
                        f"id: {pid}",
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
                        "  - type: filesystem",
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

            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("x.json").write_text(
                json.dumps({"product_id": pid, "success": False, "error": "oops"}),
                encoding="utf-8",
            )

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            assert res.node is not None
            node = res.node

            from argus.signals.adapters import default_builtin_adapters
            from argus.signals.runner import product_root_path

            reg = AdapterRegistry(default_builtin_adapters())
            records = collect_for_product(root, node, reg)
            _, norm = save_collection(
                root,
                pid,
                records,
                signal_manifest=node.signal_manifest,
                product_root=product_root_path(root, node),
            )
            hit = [r for r in norm if r.payload.get("provenance") == PROVENANCE_EXECUTION_OUTCOME]
            self.assertEqual(len(hit), 1)
            c = hit[0].canonical
            self.assertIsNotNone(c)
            assert c is not None
            self.assertEqual(c.product_id, pid)

    def test_findings_engine_accepts_execution_outcome_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_eob5"
            pr = root / "products" / pid
            cfg = pr / "product.yaml"
            pr.mkdir(parents=True)
            cfg.write_text(
                "\n".join(
                    [
                        f"id: {pid}",
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
                        "  - type: filesystem",
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

            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("x.json").write_text(
                json.dumps({"product_id": pid, "success": True}),
                encoding="utf-8",
            )
            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            assert res.node is not None
            node = res.node

            recs = generate_signals_from_execution_outcomes(root, pid)
            fs = generate_findings(node, recs, repo_root=root)
            self.assertIsInstance(fs, list)


if __name__ == "__main__":
    unittest.main()
