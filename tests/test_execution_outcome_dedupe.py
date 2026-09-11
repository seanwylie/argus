"""Deterministic dedupe for execution-outcome signals (classic vs adapter-layer overlap)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.adapters import default_builtin_adapters
from argus.signals.adapters.execution_outcomes import (
    PROVENANCE_EXECUTION_OUTCOME,
    dedupe_execution_outcome_signals,
    execution_outcome_signal_identity,
    generate_signals_from_execution_outcomes,
)
from argus.signals.persistence import save_collection
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product, product_root_path


class TestExecutionOutcomeDedupe(unittest.TestCase):
    def test_execution_outcome_signal_identity_normalizes_slashes(self) -> None:
        self.assertEqual(
            execution_outcome_signal_identity("p", "runs\\execution\\p\\x.json"),
            "execution_outcome:p:runs/execution/p/x.json",
        )

    def test_duplicate_rows_same_artifact_collapse(self) -> None:
        rel = "runs/execution/p1/a.json"
        oid = execution_outcome_signal_identity("p1", rel)
        base_payload = {
            "provenance": PROVENANCE_EXECUTION_OUTCOME,
            "synthetic": False,
            "execution_record_relpath": rel,
            "execution_success": True,
            "execution_failure": False,
            "outcome_direction": "success",
            "outcome_magnitude": 1.0,
            "observed_at_utc": "2026-04-12T12:00:00+00:00",
            "execution_outcome_id": oid,
        }
        from datetime import datetime, timezone

        obs = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        classic = SignalRecord(
            id="sig-classic",
            product_id="p1",
            signal_type=SignalType.EXECUTION,
            source="execution",
            observed_at=obs,
            payload=dict(base_payload),
            tags=["execution", "feedback", "execution_outcome"],
        )
        layer_dup = SignalRecord(
            id="sig-layer",
            product_id="p1",
            signal_type=SignalType.EXECUTION,
            source="execution",
            observed_at=obs,
            payload=dict(base_payload),
            tags=["execution", "feedback", "execution_outcome", "adapter_layer"],
        )
        out = dedupe_execution_outcome_signals([classic, layer_dup])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].id, "sig-classic")
        self.assertEqual(out[0].payload.get("execution_outcome_dedupe_collapsed_count"), 1)
        self.assertEqual(out[0].payload.get("execution_outcome_dedupe_superseded_ids"), ["sig-layer"])

    def test_distinct_artifacts_remain_distinct(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dd"
            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("a.json").write_text(
                json.dumps({"product_id": pid, "success": True}),
                encoding="utf-8",
            )
            ex.joinpath("b.json").write_text(
                json.dumps({"product_id": pid, "success": False, "error": "x"}),
                encoding="utf-8",
            )
            recs = generate_signals_from_execution_outcomes(root, pid)
            self.assertEqual(len(recs), 2)
            d = dedupe_execution_outcome_signals(recs)
            self.assertEqual(len(d), 2)

    def test_non_execution_signals_unchanged(self) -> None:
        from datetime import datetime, timezone

        obs = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        fs = SignalRecord(
            id="fs1",
            product_id="p1",
            signal_type=SignalType.FILESYSTEM,
            source="filesystem",
            observed_at=obs,
            payload={"path": "/x"},
        )
        rel = "runs/execution/p1/a.json"
        oid = execution_outcome_signal_identity("p1", rel)
        ex = SignalRecord(
            id="ex1",
            product_id="p1",
            signal_type=SignalType.EXECUTION,
            source="execution",
            observed_at=obs,
            payload={
                "provenance": PROVENANCE_EXECUTION_OUTCOME,
                "execution_record_relpath": rel,
                "execution_success": True,
                "execution_failure": False,
                "outcome_direction": "success",
                "outcome_magnitude": 1.0,
                "observed_at_utc": obs.isoformat(),
                "execution_outcome_id": oid,
            },
            tags=["execution", "execution_outcome"],
        )
        out = dedupe_execution_outcome_signals([fs, ex])
        self.assertEqual(len(out), 2)
        self.assertEqual({r.id for r in out}, {"fs1", "ex1"})

    def test_collect_merge_adapter_layer_no_duplicate_execution_outcomes(self) -> None:
        """Classic + layer both emit execution outcomes; dedupe leaves one per file."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_mrg"
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

            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("one.json").write_text(
                json.dumps({"product_id": pid, "success": True}),
                encoding="utf-8",
            )

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry(default_builtin_adapters())
            without_layer = collect_for_product(root, node, reg, merge_adapter_layer=False)
            with_layer = collect_for_product(root, node, reg, merge_adapter_layer=True)

            def eo_rows(rs: list[SignalRecord]) -> list[SignalRecord]:
                return [
                    r
                    for r in rs
                    if isinstance(r.payload, dict)
                    and r.payload.get("provenance") == PROVENANCE_EXECUTION_OUTCOME
                ]

            self.assertEqual(len(eo_rows(without_layer)), 1)
            self.assertEqual(len(eo_rows(with_layer)), 1)
            self.assertEqual(
                eo_rows(without_layer)[0].payload.get("execution_outcome_id"),
                eo_rows(with_layer)[0].payload.get("execution_outcome_id"),
            )

    def test_save_collection_applies_dedupe(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sv"
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

            ex = root / "runs" / "execution" / pid
            ex.mkdir(parents=True)
            ex.joinpath("one.json").write_text(
                json.dumps({"product_id": pid, "success": True}),
                encoding="utf-8",
            )

            raw, err = load_yaml_file(cfg)
            self.assertIsNone(err)
            assert raw is not None
            res = validate_manifest(raw, repo_root=root, product_root=pr, config_path=cfg)
            assert res.node is not None
            node = res.node

            reg = AdapterRegistry(default_builtin_adapters())
            raw_recs = collect_for_product(root, node, reg, merge_adapter_layer=True)
            _, norm = save_collection(
                root,
                pid,
                raw_recs,
                signal_manifest=node.signal_manifest,
                product_root=product_root_path(root, node),
            )
            eo = [
                r
                for r in norm
                if isinstance(r.payload, dict)
                and r.payload.get("provenance") == PROVENANCE_EXECUTION_OUTCOME
            ]
            self.assertEqual(len(eo), 1)
            self.assertIsNotNone(eo[0].canonical)


if __name__ == "__main__":
    unittest.main()
