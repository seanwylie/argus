"""Tests for operator policy loading, validation, and backward-compatible defaults."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from argus.policy.operator_policy import (
    clear_operator_policy_cache,
    default_operator_policy,
    load_operator_policy,
    validate_operator_policy,
    write_operator_policy_effective_artifact,
)
from argus.portfolio.operator_queue import build_operator_queue_payload


class TestOperatorPolicy(unittest.TestCase):
    def tearDown(self) -> None:
        clear_operator_policy_cache()

    def test_default_matches_module_constants(self) -> None:
        d = default_operator_policy()
        self.assertEqual(d["schema"], "argus.operator_policy.v1")
        self.assertEqual(d["confidence"]["low_threshold"], 0.45)
        self.assertEqual(d["quiescence"]["debt_delta_material"], 0.08)

    def test_no_config_file_loads_defaults(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            p = load_operator_policy(root)
            self.assertEqual(p["confidence"]["low_threshold"], 0.45)

    def test_valid_override_changes_value(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config"
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / "operator_policy.yaml").write_text(
                yaml.dump(
                    {
                        "schema": "argus.operator_policy.v1",
                        "confidence": {"low_threshold": 0.5},
                    }
                ),
                encoding="utf-8",
            )
            p = load_operator_policy(root)
            self.assertEqual(p["confidence"]["low_threshold"], 0.5)
            self.assertEqual(p["quiescence"]["debt_delta_material"], 0.08)

    def test_invalid_policy_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config"
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / "operator_policy.yaml").write_text(
                yaml.dump({"schema": "argus.operator_policy.v1", "confidence": {"low_threshold": 1.5}}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_operator_policy(root)

    def test_validate_rejects_bad_schema(self) -> None:
        with self.assertRaises(ValueError):
            validate_operator_policy({"schema": "wrong"})

    def test_write_effective_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            path = write_operator_policy_effective_artifact(root)
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], "argus.operator_policy.v1")

    def test_queue_payload_uses_policy_low_threshold(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "config"
            cfg.mkdir(parents=True, exist_ok=True)
            (cfg / "operator_policy.yaml").write_text(
                yaml.dump(
                    {
                        "schema": "argus.operator_policy.v1",
                        "confidence": {"low_threshold": 0.5},
                    }
                ),
                encoding="utf-8",
            )
            pl = build_operator_queue_payload(root)
            w = pl.get("weight_ledger") or {}
            dec = w.get("decision") or {}
            self.assertEqual(dec.get("low_confidence_below"), 0.5)
