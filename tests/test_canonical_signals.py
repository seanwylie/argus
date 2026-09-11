"""Canonical signal normalization (deterministic, collection-time)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import (
    ManifestRequiredFor,
    ManifestTrustLevel,
    ManifestValueType,
    ProductSignalManifest,
    ProductSignalManifestEntry,
    SignalManifestCategory,
)
from argus.core.models.validation import validate_signal_record
from argus.core.serialize import dumps_json, loads_json, signal_record_from_dict
from argus.signals.category_mapping import CATEGORY_SOURCE_SIGNAL_TYPE
from argus.signals.ids import new_signal_id
from argus.signals.normalize import (
    SLA_SOURCE_MANIFEST,
    SLA_SOURCE_PAYLOAD_METADATA,
    SLA_SOURCE_PAYLOAD_TOP_STRING,
    attach_canonical_to_records,
    build_canonical_signal,
    resolve_freshness_sla,
)
from argus.signals.persistence import load_latest_bundle, save_collection


class TestCanonicalNormalization(unittest.TestCase):
    def test_ok_metrics_payload(self) -> None:
        rid = new_signal_id()
        obs = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=rid,
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=obs,
            payload={"file": "metrics/x.json", "format": "json", "data": {"mrr": 120.0}},
            confidence=0.75,
            tags=["metrics", "file"],
        )
        c = build_canonical_signal(r, collected_at=col)
        self.assertEqual(c.signal_id, rid)
        self.assertEqual(c.product_id, "p1")
        self.assertEqual(c.category, "metrics")
        self.assertEqual(c.collection_status, "ok")
        self.assertEqual(c.value_type, "object")
        self.assertEqual(c.source_type, "adapter")
        self.assertEqual(c.source_ref, "metrics/x.json")
        self.assertEqual(c.trust_level, "medium")
        self.assertEqual(c.freshness_status, "realtime")

    def test_error_io_tags(self) -> None:
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"error": "permission denied", "file": "metrics/a.json"},
            tags=["metrics", "io_error"],
        )
        c = build_canonical_signal(r, collected_at=col)
        self.assertEqual(c.collection_status, "error")
        self.assertEqual(c.value_type, "error")
        self.assertIsNone(c.value)

    def test_missing_empty_dir_note(self) -> None:
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={
                "check": "metrics_files",
                "ok": True,
                "note": "no JSON/JSONL metrics files found under metrics/",
            },
        )
        c = build_canonical_signal(r, collected_at=col)
        self.assertEqual(c.collection_status, "missing")
        self.assertEqual(c.value_type, "missing")

    def test_attach_and_validate_roundtrip(self) -> None:
        col = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p9",
            signal_type=SignalType.HEALTH,
            source="heartbeat",
            observed_at=col,
            payload={"k": 1},
            confidence=0.9,
        )
        out = attach_canonical_to_records([r], col)
        self.assertEqual(len(out), 1)
        validate_signal_record(out[0])
        blob = loads_json(dumps_json(out[0]))
        r2 = signal_record_from_dict(blob)
        validate_signal_record(r2)
        self.assertIsNotNone(r2.canonical)
        self.assertEqual(r2.canonical.signal_id, r.id)

    def test_save_collection_persists_canonical(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "prod_a"
            rec = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                payload={"data": {"x": 1}},
            )
            _, norm = save_collection(root, pid, [rec])
            self.assertIsNotNone(norm[0].canonical)
            b = load_latest_bundle(root, pid)
            assert b is not None
            self.assertIsNotNone(b.records[0].canonical)
            raw = json.loads((root / "runs" / "signals" / "latest" / f"{pid}.json").read_text())
            self.assertEqual(raw["records"][0]["canonical"]["schema"], "argus.canonical_signal.v1")


class TestManifestDrivenCanonical(unittest.TestCase):
    def test_manifest_entry_sets_category_sla_trust(self) -> None:
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        entry = ProductSignalManifestEntry(
            id="m1",
            category=SignalManifestCategory.BUSINESS,
            source_type=SignalType.METRICS,
            path="metrics/x.json",
            freshness_sla="24h",
            value_type=ManifestValueType.GAUGE,
            required_for=ManifestRequiredFor.PIPELINE,
            trust_level=ManifestTrustLevel.AUTHORITATIVE,
        )
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"file": "metrics/x.json", "data": {"a": 1}},
        )
        c = build_canonical_signal(r, collected_at=col, manifest_entry=entry)
        self.assertEqual(c.category, "business")
        self.assertEqual(c.freshness_sla, "24h")
        self.assertEqual(c.provenance.get("freshness_sla_source"), SLA_SOURCE_MANIFEST)
        self.assertEqual(c.trust_level, "high")
        self.assertEqual(c.provenance.get("manifest_signal_id"), "m1")
        self.assertEqual(c.provenance.get("category_source"), "product_signal_manifest")

    def test_manifest_freshness_sla_overrides_payload(self) -> None:
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        entry = ProductSignalManifestEntry(
            id="m1",
            category=SignalManifestCategory.BUSINESS,
            source_type=SignalType.METRICS,
            path="metrics/x.json",
            freshness_sla="24h",
            value_type=ManifestValueType.GAUGE,
            required_for=ManifestRequiredFor.PIPELINE,
            trust_level=ManifestTrustLevel.AUTHORITATIVE,
        )
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={
                "file": "metrics/x.json",
                "data": {"a": 1},
                "freshness_sla": "5m",
                "freshness_metadata": {"max_age_seconds": 99},
            },
        )
        c = build_canonical_signal(r, collected_at=col, manifest_entry=entry)
        self.assertEqual(c.freshness_sla, "24h")
        self.assertEqual(c.provenance.get("freshness_sla_source"), SLA_SOURCE_MANIFEST)

    def test_category_fallback_signal_type(self) -> None:
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"file": "z.json", "data": {}},
        )
        c = build_canonical_signal(r, collected_at=col, manifest_entry=None)
        self.assertEqual(c.category, "metrics")
        self.assertEqual(c.provenance.get("category_source"), CATEGORY_SOURCE_SIGNAL_TYPE)

    def test_resolve_freshness_sla_payload_only(self) -> None:
        p = {"freshness_metadata": {"max_age_seconds": 120}}
        s, src = resolve_freshness_sla(p, None)
        self.assertEqual(s, "120s")
        self.assertEqual(src, SLA_SOURCE_PAYLOAD_METADATA)

    def test_resolve_freshness_sla_top_level_string(self) -> None:
        p = {"freshness_sla": "10m"}
        s, src = resolve_freshness_sla(p, None)
        self.assertEqual(s, "10m")
        self.assertEqual(src, SLA_SOURCE_PAYLOAD_TOP_STRING)

    def test_attach_with_manifest_matches(self) -> None:
        col = datetime(2026, 1, 2, 4, 0, 0, tzinfo=timezone.utc)
        entry = ProductSignalManifestEntry(
            id="m1",
            category=SignalManifestCategory.COST,
            source_type=SignalType.METRICS,
            path="metrics/a.json",
            freshness_sla="15m",
            value_type=ManifestValueType.GAUGE,
            required_for=ManifestRequiredFor.FINDINGS,
            trust_level=ManifestTrustLevel.HEURISTIC,
        )
        m = ProductSignalManifest(signals=[entry])
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"file": "metrics/a.json"},
        )
        out = attach_canonical_to_records([r], col, signal_manifest=m, product_root=None)
        self.assertEqual(out[0].canonical.category, "cost")
        self.assertEqual(out[0].canonical.freshness_sla, "15m")
