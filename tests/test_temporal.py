"""Tests for temporal signal layer."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.persistence import save_collection
from argus.temporal.enrichment import temporal_signals_from_records
from argus.temporal.models import FreshnessBucket, FreshnessStatus
from argus.temporal.persistence import load_latest_temporal_bundle, temporal_latest_path
from argus.temporal.recency import (
    compute_freshness,
    compute_freshness_score,
    observation_age_seconds,
    worst_freshness_bucket_from_signal_dicts,
    worst_freshness_status_from_signal_dicts,
)


class TestWorstBucket(unittest.TestCase):
    def test_worst_bucket_picks_stalest(self) -> None:
        rows = [
            {"freshness_bucket": "realtime"},
            {"freshness_bucket": "stale"},
            {"freshness_bucket": "recent"},
        ]
        self.assertEqual(worst_freshness_bucket_from_signal_dicts(rows), "stale")

    def test_unknown_is_stalest_in_order(self) -> None:
        rows = [{"freshness_bucket": "realtime"}, {"freshness_bucket": "unknown"}]
        self.assertEqual(worst_freshness_bucket_from_signal_dicts(rows), "unknown")


class TestFreshness(unittest.TestCase):
    def test_score_zero_at_max_age(self) -> None:
        self.assertAlmostEqual(compute_freshness_score(30 * 86400.0), 0.0, places=5)

    def test_score_one_at_zero_age(self) -> None:
        self.assertAlmostEqual(compute_freshness_score(0.0), 1.0, places=5)

    def test_bucket_realtime(self) -> None:
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        obs = now - timedelta(minutes=30)
        score, bucket = compute_freshness(obs, reference_time=now)
        self.assertEqual(bucket, FreshnessBucket.REALTIME)
        self.assertGreater(score, 0.9)

    def test_bucket_stale(self) -> None:
        now = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
        obs = now - timedelta(days=10)
        score, bucket = compute_freshness(obs, reference_time=now)
        self.assertEqual(bucket, FreshnessBucket.STALE)
        self.assertLess(score, 1.0)
        self.assertGreaterEqual(score, 0.0)


class TestTemporalIntegration(unittest.TestCase):
    def test_save_collection_writes_temporal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "t_prod"
            now = datetime.now(timezone.utc)
            rec = SignalRecord(
                id="sig_1",
                product_id=pid,
                signal_type=SignalType.METRICS,
                source="test",
                observed_at=now,
                payload={"k": 1},
                confidence=0.9,
            )
            _, _ = save_collection(root, pid, [rec])
            lp = temporal_latest_path(root, pid)
            self.assertTrue(lp.is_file())
            raw = load_latest_temporal_bundle(root, pid)
            assert raw is not None
            self.assertEqual(raw.get("schema"), "argus.temporal_bundle.v1")
            sigs = raw.get("signals") or []
            self.assertEqual(len(sigs), 1)
            self.assertEqual(sigs[0].get("freshness_bucket"), FreshnessBucket.REALTIME.value)
            self.assertEqual(sigs[0].get("freshness_status"), FreshnessStatus.FRESH.value)
            self.assertIn("worst_freshness_status", raw)
            self.assertEqual(
                raw.get("worst_freshness_status"),
                worst_freshness_status_from_signal_dicts(sigs),
            )

    def test_normalize_deterministic(self) -> None:
        ref = datetime(2026, 6, 1, 0, 0, tzinfo=timezone.utc)
        fetched = ref
        obs = ref - timedelta(hours=2)
        rec = SignalRecord(
            id="a",
            product_id="p",
            signal_type=SignalType.METRICS,
            source="s",
            observed_at=obs,
            payload={},
        )
        ts = temporal_signals_from_records([rec], fetched_at=fetched, reference_time=ref)
        self.assertEqual(len(ts), 1)
        self.assertEqual(ts[0].freshness_bucket, FreshnessBucket.RECENT)
        self.assertEqual(ts[0].freshness_status, FreshnessStatus.FRESH)
        self.assertEqual(ts[0].collected_at, fetched)
        age = observation_age_seconds(obs, ref)
        self.assertAlmostEqual(ts[0].freshness_score, compute_freshness_score(age), places=5)
