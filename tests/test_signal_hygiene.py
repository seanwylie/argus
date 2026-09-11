"""Tests for signal hygiene classification for idea generation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.idea_generation.signal_hygiene import (
    SignalHygiene,
    classify_signal_record,
    sort_key_for_idea_generation,
)
from argus.signals.manifest_collect import PLACEHOLDER_SOURCE, placeholder_signal_id


def _record(
    *,
    source: str = "metrics_file",
    observed_at: datetime | None = None,
    payload: dict | None = None,
    tags: list[str] | None = None,
    signal_type: SignalType = SignalType.METRICS,
    rec_id: str = "r1",
) -> SignalRecord:
    return SignalRecord(
        id=rec_id,
        product_id="p1",
        signal_type=signal_type,
        source=source,
        observed_at=observed_at or datetime(2026, 3, 1, tzinfo=timezone.utc),
        payload=payload or {"summary": "ok"},
        tags=tags or [],
    )


class TestClassify(unittest.TestCase):
    def test_manifest_declaration_is_low(self) -> None:
        r = SignalRecord(
            id=placeholder_signal_id("p1", "e1"),
            product_id="p1",
            signal_type=SignalType.METRICS,
            source=PLACEHOLDER_SOURCE,
            observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
            payload={"collection_status": "missing"},
            tags=["manifest_declaration", "missing"],
        )
        h = classify_signal_record(r)
        self.assertTrue(h.is_manifest_declaration)
        self.assertEqual(h.signal_quality_score, "low")

    def test_sentinel_1970_is_low(self) -> None:
        r = _record(
            source="test",
            observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
            payload={"summary": "real"},
        )
        h = classify_signal_record(r)
        self.assertTrue(h.is_sentinel_timestamp)
        self.assertEqual(h.signal_quality_score, "low")

    def test_placeholder_text(self) -> None:
        r = _record(payload={"notes": "stub value placeholder"})
        h = classify_signal_record(r)
        self.assertTrue(h.is_placeholder)
        self.assertEqual(h.signal_quality_score, "low")

    def test_mostly_null_payload(self) -> None:
        r = _record(
            payload={"a": None, "b": None, "c": None, "d": "x"},
        )
        h = classify_signal_record(r)
        self.assertTrue(h.is_placeholder)
        self.assertEqual(h.signal_quality_score, "low")

    def test_filesystem_high(self) -> None:
        r = _record(
            source="filesystem",
            payload={"summary": "metrics present"},
        )
        h = classify_signal_record(r)
        self.assertFalse(h.is_manifest_declaration)
        self.assertFalse(h.is_placeholder)
        self.assertFalse(h.is_sentinel_timestamp)
        self.assertEqual(h.signal_quality_score, "high")

    def test_non_file_source_medium(self) -> None:
        r = _record(source="test_source", payload={"summary": "ok"})
        h = classify_signal_record(r)
        self.assertEqual(h.signal_quality_score, "medium")

    def test_sort_prefers_high_before_low(self) -> None:
        low = _record(
            rec_id="low",
            source=PLACEHOLDER_SOURCE,
            observed_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
            payload={},
            tags=["manifest_declaration"],
        )
        high = _record(rec_id="high", source="filesystem", payload={"summary": "x"})
        keys = sorted([low, high], key=sort_key_for_idea_generation)
        self.assertEqual(keys[0].id, "high")
        self.assertEqual(keys[1].id, "low")


class TestSignalHygieneDict(unittest.TestCase):
    def test_to_dict_keys(self) -> None:
        h = SignalHygiene(
            is_manifest_declaration=False,
            is_placeholder=False,
            is_sentinel_timestamp=False,
            signal_quality_score="high",
        )
        d = h.to_dict()
        self.assertEqual(
            set(d.keys()),
            {
                "is_manifest_declaration",
                "is_placeholder",
                "is_sentinel_timestamp",
                "signal_quality_score",
            },
        )


if __name__ == "__main__":
    unittest.main()
