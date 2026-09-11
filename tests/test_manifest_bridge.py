"""Deterministic manifest ↔ record matching."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import (
    ManifestRequiredFor,
    ManifestTrustLevel,
    ManifestValueType,
    ProductSignalManifestEntry,
    SignalManifestCategory,
)
from argus.signals.manifest_bridge import match_manifest_entry


class TestManifestBridge(unittest.TestCase):
    def _entry(
        self,
        *,
        path: str | None = "metrics/a.json",
        source_ref: str | None = None,
        st: SignalType = SignalType.METRICS,
    ) -> ProductSignalManifestEntry:
        return ProductSignalManifestEntry(
            id="e1",
            category=SignalManifestCategory.OPERATIONAL,
            source_type=st,
            path=path,
            source_ref=source_ref,
            freshness_sla="1h",
            value_type=ManifestValueType.GAUGE,
            required_for=ManifestRequiredFor.PIPELINE,
            trust_level=ManifestTrustLevel.DERIVED,
        )

    def test_matches_path_and_type(self) -> None:
        e = self._entry()
        r = SignalRecord(
            id="1",
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=datetime.now(timezone.utc),
            payload={"file": "metrics/a.json"},
        )
        m = match_manifest_entry(r, entries=[e], product_root=None)
        self.assertIsNotNone(m)
        self.assertEqual(m.id, "e1")

    def test_rejects_signal_type_mismatch(self) -> None:
        e = self._entry()
        r = SignalRecord(
            id="1",
            product_id="p",
            signal_type=SignalType.HEALTH,
            source="x",
            observed_at=datetime.now(timezone.utc),
            payload={"file": "metrics/a.json"},
        )
        self.assertIsNone(match_manifest_entry(r, entries=[e], product_root=None))

    def test_source_ref_only(self) -> None:
        e = self._entry(path=None, source_ref="adapter:foo")
        r = SignalRecord(
            id="1",
            product_id="p",
            signal_type=SignalType.METRICS,
            source="metrics_file",
            observed_at=datetime.now(timezone.utc),
            payload={"source_ref": "adapter:foo"},
        )
        m = match_manifest_entry(r, entries=[e], product_root=None)
        self.assertIsNotNone(m)

    def test_resolves_under_product_root(self) -> None:
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "metrics").mkdir()
            pfile = root / "metrics" / "a.json"
            pfile.write_text("{}", encoding="utf-8")
            e = self._entry(path="metrics/a.json")
            r = SignalRecord(
                id="1",
                product_id="p",
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=datetime.now(timezone.utc),
                payload={"file": "metrics/a.json"},
            )
            m = match_manifest_entry(r, entries=[e], product_root=root)
            self.assertIsNotNone(m)
