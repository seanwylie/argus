"""Deterministic freshness age/status from normalized signals + optional sidecar."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.temporal.freshness_compute import (
    KEY_FRESHNESS_SLA,
    KEY_SOURCE_WINDOW_END,
    KEY_TEMPORAL_SIDECAR,
    compute_signal_freshness_view,
    parse_freshness_sla_string_to_seconds,
    parse_iso_datetime,
)
from argus.temporal.models import FreshnessStatus
from argus.temporal.recency import (
    RECENT_MAX_AGE_S,
    SCORE_MAX_AGE_S,
    worst_freshness_status_from_signal_dicts,
)


class TestParseSlaString(unittest.TestCase):
    def test_common_units(self) -> None:
        self.assertAlmostEqual(parse_freshness_sla_string_to_seconds("1h") or 0.0, 3600.0)
        self.assertAlmostEqual(parse_freshness_sla_string_to_seconds("15m") or 0.0, 900.0)
        self.assertIsNone(parse_freshness_sla_string_to_seconds("best_effort"))
        self.assertIsNone(parse_freshness_sla_string_to_seconds(None))


class TestParseIso(unittest.TestCase):
    def test_z_suffix(self) -> None:
        dt = parse_iso_datetime("2026-01-01T00:00:00Z")
        self.assertIsNotNone(dt)
        self.assertEqual(dt.tzinfo, timezone.utc)

    def test_garbage_returns_none(self) -> None:
        self.assertIsNone(parse_iso_datetime("not-a-date"))


class TestFreshnessTransitions(unittest.TestCase):
    def setUp(self) -> None:
        self.ref = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        self.coll = self.ref

    def _rec(
        self,
        *,
        observed_at: datetime,
        payload: dict | None = None,
    ) -> SignalRecord:
        return SignalRecord(
            id="s1",
            product_id="p",
            signal_type=SignalType.METRICS,
            source="t",
            observed_at=observed_at,
            payload=payload or {},
        )

    def test_no_sidecar_fresh_within_24h(self) -> None:
        obs = self.ref - timedelta(hours=2)
        v = compute_signal_freshness_view(
            self._rec(observed_at=obs), collected_at=self.coll, reference_time=self.ref
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.FRESH)
        self.assertLessEqual(v.freshness_age_seconds, RECENT_MAX_AGE_S)

    def test_no_sidecar_aging(self) -> None:
        obs = self.ref - timedelta(days=3)
        v = compute_signal_freshness_view(
            self._rec(observed_at=obs), collected_at=self.coll, reference_time=self.ref
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.AGING)

    def test_no_sidecar_stale_before_30d_cap(self) -> None:
        obs = self.ref - timedelta(days=20)
        v = compute_signal_freshness_view(
            self._rec(observed_at=obs), collected_at=self.coll, reference_time=self.ref
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.STALE)
        self.assertLessEqual(v.freshness_age_seconds, SCORE_MAX_AGE_S)

    def test_no_sidecar_expired_beyond_30d(self) -> None:
        obs = self.ref - timedelta(days=45)
        v = compute_signal_freshness_view(
            self._rec(observed_at=obs), collected_at=self.coll, reference_time=self.ref
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.EXPIRED)
        self.assertGreater(v.freshness_age_seconds, SCORE_MAX_AGE_S)

    def test_sla_tiers(self) -> None:
        sla = 100.0
        obs = self.ref - timedelta(seconds=10)
        v = compute_signal_freshness_view(
            self._rec(
                observed_at=obs,
                payload={KEY_FRESHNESS_SLA: sla},
            ),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.FRESH)

        obs2 = self.ref - timedelta(seconds=50)
        v2 = compute_signal_freshness_view(
            self._rec(observed_at=obs2, payload={KEY_FRESHNESS_SLA: sla}),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertEqual(v2.freshness_status, FreshnessStatus.AGING)

        obs3 = self.ref - timedelta(seconds=90)
        v3 = compute_signal_freshness_view(
            self._rec(observed_at=obs3, payload={KEY_FRESHNESS_SLA: sla}),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertEqual(v3.freshness_status, FreshnessStatus.STALE)

        obs4 = self.ref - timedelta(seconds=101)
        v4 = compute_signal_freshness_view(
            self._rec(observed_at=obs4, payload={KEY_FRESHNESS_SLA: sla}),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertEqual(v4.freshness_status, FreshnessStatus.EXPIRED)

    def test_window_end_anchor_older_than_observed(self) -> None:
        obs = self.ref - timedelta(hours=1)
        we = self.ref - timedelta(days=5)
        v = compute_signal_freshness_view(
            self._rec(
                observed_at=obs,
                payload={KEY_SOURCE_WINDOW_END: we.isoformat()},
            ),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertAlmostEqual(v.freshness_age_seconds, 5 * 86400.0, delta=1.0)
        self.assertEqual(v.freshness_status, FreshnessStatus.AGING)

    def test_invalid_window_end_ignored_use_observed(self) -> None:
        obs = self.ref - timedelta(hours=1)
        v = compute_signal_freshness_view(
            self._rec(
                observed_at=obs,
                payload={KEY_SOURCE_WINDOW_END: "not-valid-iso"},
            ),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.FRESH)

    def test_nested_temporal_sidecar(self) -> None:
        obs = self.ref - timedelta(seconds=100)
        v = compute_signal_freshness_view(
            self._rec(
                observed_at=obs,
                payload={
                    KEY_TEMPORAL_SIDECAR: {
                        KEY_FRESHNESS_SLA: 10_000.0,
                    }
                },
            ),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertEqual(v.freshness_status, FreshnessStatus.FRESH)
        self.assertEqual(v.freshness_sla_seconds, 10_000.0)

    def test_canonical_freshness_sla_overrides_sidecar_seconds(self) -> None:
        obs = self.ref - timedelta(seconds=50)
        r = self._rec(
            observed_at=obs,
            payload={KEY_FRESHNESS_SLA: 30.0},
        )
        canon = CanonicalSignal(
            signal_id="s1",
            product_id="p",
            category="metrics",
            value=None,
            value_type="object",
            unit=None,
            source_type="adapter",
            source_ref="x",
            provenance={},
            observed_at=obs.isoformat(),
            collected_at=self.coll.isoformat(),
            freshness_sla="100s",
            freshness_status="realtime",
            trust_level="medium",
            collection_status="ok",
        )
        v = compute_signal_freshness_view(
            r,
            collected_at=self.coll,
            reference_time=self.ref,
            canonical=canon,
        )
        self.assertEqual(v.freshness_sla_seconds, 100.0)

    def test_window_span_continuity_only(self) -> None:
        start = self.ref - timedelta(days=10)
        end = self.ref - timedelta(days=7)
        v = compute_signal_freshness_view(
            self._rec(
                observed_at=start,
                payload={
                    "source_window_start": start.isoformat(),
                    "source_window_end": end.isoformat(),
                },
            ),
            collected_at=self.coll,
            reference_time=self.ref,
        )
        self.assertIsNotNone(v.source_window_span_seconds)
        self.assertAlmostEqual(v.source_window_span_seconds or 0.0, 3 * 86400.0, delta=1.0)


class TestWorstStatus(unittest.TestCase):
    def test_order(self) -> None:
        rows = [
            {"freshness_status": "fresh"},
            {"freshness_status": "expired"},
            {"freshness_status": "aging"},
        ]
        self.assertEqual(worst_freshness_status_from_signal_dicts(rows), "expired")

    def test_unknown_worst(self) -> None:
        rows = [{"freshness_status": "fresh"}, {"freshness_status": "unknown"}]
        self.assertEqual(worst_freshness_status_from_signal_dicts(rows), "unknown")

    def test_manifest_declaration_excluded_from_worst(self) -> None:
        """Synthetic manifest gap rows use epoch observed_at — must not dominate aggregate."""
        rows = [
            {"source": "manifest_declaration", "freshness_status": "expired"},
            {"source": "filesystem", "freshness_status": "fresh"},
        ]
        self.assertEqual(worst_freshness_status_from_signal_dicts(rows), "fresh")


if __name__ == "__main__":
    unittest.main()
