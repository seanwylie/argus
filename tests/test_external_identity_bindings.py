"""External identity bindings (product.yaml) and collect-time verification."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.products.external_bindings import (
    EXTERNAL_BINDINGS_SCHEMA,
    ExternalBindings,
    parse_external_bindings,
    validate_external_bindings_block,
)
from argus.products.loader import load_yaml_file
from argus.products.validate import validate_manifest
from argus.signals.external_identity import SignalIdentityVerificationError
from argus.signals.ids import new_signal_id
from argus.signals.persistence import save_collection


class TestExternalBindingsValidation(unittest.TestCase):
    def test_schema_required(self) -> None:
        errs, _ = validate_external_bindings_block({"domains": ["a.com"]})
        self.assertTrue(any("schema" in e for e in errs))

    def test_accepts_v1(self) -> None:
        errs, warns = validate_external_bindings_block(
            {
                "schema": EXTERNAL_BINDINGS_SCHEMA,
                "domains": ["demo-product.example"],
                "repos": ["https://github.com/o/r"],
                "analytics": {
                    "google_analytics": ["123"],
                    "google_search_console": [],
                    "other": [],
                },
            }
        )
        self.assertEqual(errs, [])
        self.assertIsInstance(warns, list)

    def test_rejects_empty_string_in_list(self) -> None:
        errs, _ = validate_external_bindings_block(
            {"schema": EXTERNAL_BINDINGS_SCHEMA, "domains": ["ok", "  "]}
        )
        self.assertTrue(errs)

    def test_manifest_integration(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "pbind"
            pr.mkdir(parents=True)
            cfg = pr / "product.yaml"
            cfg.write_text(
                "\n".join(
                    [
                        "id: pbind",
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
                        "raw_extensions:",
                        "  external_bindings:",
                        f"    schema: {EXTERNAL_BINDINGS_SCHEMA}",
                        "    domains:",
                        "      - example.com",
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
            self.assertEqual(res.errors, [])
            assert res.node is not None
            eb = parse_external_bindings(res.node.raw_extensions)
            assert eb is not None
            self.assertEqual(eb.domains, ("example.com",))


class TestCollectVerification(unittest.TestCase):
    def test_no_bindings_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "pnobind"
            rec = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"site_hostname": "anywhere.com"},
            )
            _, norm = save_collection(root, pid, [rec])
            raw = json.loads((root / "runs" / "signals" / "latest" / f"{pid}.json").read_text())
            self.assertEqual(raw["external_identity_verification"]["overall_status"], "skipped_no_bindings")

    def test_match_succeeds(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "pmatch"
            bindings = ExternalBindings(
                domains=("demo-product.example",),
                repos=tuple(),
                analytics_google_analytics=tuple(),
                analytics_google_search_console=tuple(),
                analytics_other=tuple(),
            )
            rec = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.ANALYTICS,
                source="analytics_file",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"site_hostname": "demo-product.example", "ok": True},
            )
            _, norm = save_collection(root, pid, [rec], external_bindings=bindings)
            raw = json.loads((root / "runs" / "signals" / "latest" / f"{pid}.json").read_text())
            self.assertEqual(raw["external_identity_verification"]["overall_status"], "verified")
            self.assertIsNotNone(norm[0].canonical)

    def test_mismatch_raises(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "pfail"
            bindings = ExternalBindings(
                domains=("demo-product.example",),
                repos=tuple(),
                analytics_google_analytics=tuple(),
                analytics_google_search_console=tuple(),
                analytics_other=tuple(),
            )
            rec = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.ANALYTICS,
                source="analytics_file",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"site_hostname": "other.com", "ok": True},
            )
            with self.assertRaises(SignalIdentityVerificationError):
                save_collection(root, pid, [rec], external_bindings=bindings)
            self.assertFalse((root / "runs" / "signals" / "latest" / f"{pid}.json").is_file())

    def test_not_verifiable_when_no_extract(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "pnv"
            bindings = ExternalBindings(
                domains=("demo-product.example",),
                repos=tuple(),
                analytics_google_analytics=tuple(),
                analytics_google_search_console=tuple(),
                analytics_other=tuple(),
            )
            rec = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"file": "metrics/x.json", "data": {"a": 1}},
            )
            _, _ = save_collection(root, pid, [rec], external_bindings=bindings)
            raw = json.loads((root / "runs" / "signals" / "latest" / f"{pid}.json").read_text())
            self.assertEqual(raw["external_identity_verification"]["overall_status"], "not_verifiable")

    def test_ga_property_match(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "pga"
            bindings = ExternalBindings(
                domains=tuple(),
                repos=tuple(),
                analytics_google_analytics=("987654",),
                analytics_google_search_console=tuple(),
                analytics_other=tuple(),
            )
            rec = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.ANALYTICS,
                source="analytics_file",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"ga_property_id": "987654", "ok": True},
            )
            _, _ = save_collection(root, pid, [rec], external_bindings=bindings)
            raw = json.loads((root / "runs" / "signals" / "latest" / f"{pid}.json").read_text())
            self.assertEqual(raw["external_identity_verification"]["overall_status"], "verified")


if __name__ == "__main__":
    unittest.main()
