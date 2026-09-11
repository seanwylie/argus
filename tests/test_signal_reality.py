"""Signal reality classification (deterministic, manifest + canonical)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
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
from argus.signals.ids import new_signal_id
from argus.signals.normalize import attach_canonical_to_records
from argus.signals.persistence import SignalCollectionBundle, save_collection
from argus.signals.reality import (
    SignalRealityStatus,
    build_signal_reality_report,
    classify_signal_reality,
    manifest_gaps,
)


def _entry(
    eid: str,
    *,
    st: SignalType = SignalType.METRICS,
    enabled: bool = True,
    path: str | None = "metrics/k.json",
) -> ProductSignalManifestEntry:
    return ProductSignalManifestEntry(
        id=eid,
        category=SignalManifestCategory.OPERATIONAL,
        source_type=st,
        freshness_sla="24h",
        value_type=ManifestValueType.JSON,
        required_for=ManifestRequiredFor.PIPELINE,
        trust_level=ManifestTrustLevel.AUTHORITATIVE,
        path=path,
        enabled=enabled,
    )


class TestClassifyRules(unittest.TestCase):
    def test_invalid_no_canonical(self) -> None:
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p",
            signal_type=SignalType.METRICS,
            source="m",
            observed_at=datetime.now(timezone.utc),
            payload={},
        )
        st, rs = classify_signal_reality(r, manifest_entry=None)
        self.assertEqual(st, SignalRealityStatus.INVALID)
        self.assertIn("no_canonical", rs)

    def test_invalid_collection_error(self) -> None:
        col = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"error": "e"},
            tags=["io_error"],
        )
        out = attach_canonical_to_records([r], col)[0]
        st, rs = classify_signal_reality(out, manifest_entry=None)
        self.assertEqual(st, SignalRealityStatus.INVALID)
        self.assertIn("canonical_collection_or_value_error", rs[0])

    def test_real_ok_fresh_trust(self) -> None:
        obs = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        col = datetime(2026, 6, 1, 12, 15, 0, tzinfo=timezone.utc)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=obs,
            payload={"file": "x.json", "data": {"a": 1}},
            confidence=0.9,
        )
        out = attach_canonical_to_records([r], col)[0]
        st, _ = classify_signal_reality(out, manifest_entry=None)
        self.assertEqual(st, SignalRealityStatus.REAL)

    def test_stale_old_observation(self) -> None:
        col = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        obs = col - timedelta(days=10)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=obs,
            payload={"file": "x.json", "data": {"a": 1}},
            confidence=0.9,
        )
        out = attach_canonical_to_records([r], col)[0]
        st, rs = classify_signal_reality(out, manifest_entry=None)
        self.assertEqual(st, SignalRealityStatus.STALE)
        self.assertIn("canonical_freshness_bucket_stale_or_aging", rs[0])

    def test_deprecated_manifest_disabled(self) -> None:
        col = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ment = _entry("sig1", enabled=False)
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"file": "metrics/k.json", "data": {}},
        )
        out = attach_canonical_to_records(
            [r], col, signal_manifest=ProductSignalManifest(signals=[ment]), product_root=None
        )[0]
        st, rs = classify_signal_reality(out, manifest_entry=ment)
        self.assertEqual(st, SignalRealityStatus.DEPRECATED)
        self.assertIn("manifest_entry_disabled", rs[0])

    def test_manifest_gap(self) -> None:
        col = datetime(2026, 1, 1, tzinfo=timezone.utc)
        m = ProductSignalManifest(
            signals=[
                _entry("only_in_manifest", path="metrics/none.json"),
            ]
        )
        r = SignalRecord(
            id=new_signal_id(),
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=col,
            payload={"file": "other.json", "data": {}},
        )
        out = attach_canonical_to_records([r], col, signal_manifest=m, product_root=None)[0]
        gaps = manifest_gaps(manifest=m, records=[out], product_root=None)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].manifest_signal_id, "only_in_manifest")
        self.assertEqual(gaps[0].reality_status, SignalRealityStatus.DECLARED_MISSING)


class TestRealityReport(unittest.TestCase):
    def test_writes_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "prod_x"
            # Align observation with collection time (save_collection uses wall-clock "now").
            obs = datetime.now(timezone.utc) - timedelta(minutes=5)
            r = SignalRecord(
                id=new_signal_id(),
                product_id=pid,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=obs,
                payload={"file": "z.json", "data": {"n": 1}},
                confidence=0.92,
            )
            _, _ = save_collection(root, pid, [r])
            from argus.signals.persistence import load_latest_bundle

            b = load_latest_bundle(root, pid)
            assert b is not None
            rep = build_signal_reality_report(
                root, b, manifest=None, product_root=None
            )
            self.assertEqual(rep["schema"], "argus.signal_reality.v1")
            self.assertEqual(len(rep["record_classifications"]), 1)
            from argus.signals.reality import write_signal_reality_report

            p = write_signal_reality_report(root, rep)
            self.assertTrue(p.is_file())
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["summary"]["real"], 1)

    def test_deprecated_via_disabled_manifest_match(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            col = datetime(2026, 1, 2, tzinfo=timezone.utc)
            ment = _entry("gone", enabled=False, path="metrics/k.json")
            m = ProductSignalManifest(signals=[ment])
            r = SignalRecord(
                id=new_signal_id(),
                product_id="p",
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=col,
                payload={"file": "metrics/k.json", "data": {"x": 1}},
                confidence=0.9,
            )
            out = attach_canonical_to_records([r], col, signal_manifest=m, product_root=None)[0]
            rep = build_signal_reality_report(
                root,
                SignalCollectionBundle(
                    product_id="p",
                    collected_at_utc=col.isoformat(),
                    repo_root=str(root),
                    records=[out],
                ),
                manifest=m,
                product_root=None,
            )
            self.assertEqual(
                rep["record_classifications"][0]["reality_status"],
                SignalRealityStatus.DEPRECATED.value,
            )
