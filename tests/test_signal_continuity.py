"""Signal continuity between collections (deterministic diff, no forecasting)."""

from __future__ import annotations

import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.continuity import compute_signal_continuity, continuity_key
from argus.signals.ids import new_signal_id
from argus.signals.normalize import attach_canonical_to_records
from argus.signals.persistence import load_bundle_file, load_latest_bundle, save_collection


def _rec(
    *,
    pid: str,
    src: str,
    st: SignalType,
    observed: datetime,
    payload: dict | None = None,
) -> SignalRecord:
    return SignalRecord(
        id=new_signal_id(),
        product_id=pid,
        signal_type=st,
        source=src,
        observed_at=observed,
        payload=payload or {"file": "metrics/x.json"},
    )


class TestContinuityKey(unittest.TestCase):
    def test_stable_across_runs_for_same_logical_source(self) -> None:
        t = datetime.now(timezone.utc)
        a = _rec(pid="p1", src="metrics_file", st=SignalType.METRICS, observed=t)
        b = _rec(pid="p1", src="metrics_file", st=SignalType.METRICS, observed=t)
        ca = attach_canonical_to_records([a], t)[0]
        cb = attach_canonical_to_records([b], t)[0]
        self.assertEqual(continuity_key(ca), continuity_key(cb))


class TestComputeContinuity(unittest.TestCase):
    def test_no_prior_not_compared(self) -> None:
        now = datetime.now(timezone.utc)
        cur = [_rec(pid="p1", src="m", st=SignalType.METRICS, observed=now)]
        cur = attach_canonical_to_records(cur, now)
        out = compute_signal_continuity([], cur, prior_collected_at_utc=None, current_collected_at=now)
        self.assertFalse(out["compared"])
        self.assertEqual(out["keys_prior"], 0)
        self.assertEqual(out["keys_current"], 1)

    def test_appeared_and_disappeared(self) -> None:
        now = datetime.now(timezone.utc)
        prior = [
            _rec(
                pid="p1",
                src="a",
                st=SignalType.METRICS,
                observed=now,
                payload={"file": "only_prior.json"},
            ),
        ]
        current = [
            _rec(
                pid="p1",
                src="b",
                st=SignalType.METRICS,
                observed=now,
                payload={"file": "only_current.json"},
            ),
        ]
        prior = attach_canonical_to_records(prior, now)
        current = attach_canonical_to_records(current, now)
        out = compute_signal_continuity(
            prior,
            current,
            prior_collected_at_utc="2020-01-01T00:00:00+00:00",
            current_collected_at=now,
        )
        self.assertTrue(out["compared"])
        self.assertEqual(len(out["appeared"]), 1)
        self.assertEqual(len(out["disappeared"]), 1)

    def test_freshness_regressed(self) -> None:
        collected = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        prior_obs = collected - timedelta(hours=2)
        current_obs = collected - timedelta(days=10)
        p = [
            _rec(pid="p1", src="metrics_file", st=SignalType.METRICS, observed=prior_obs),
        ]
        c = [
            _rec(pid="p1", src="metrics_file", st=SignalType.METRICS, observed=current_obs),
        ]
        p = attach_canonical_to_records(p, collected - timedelta(days=1))
        c = attach_canonical_to_records(c, collected)
        out = compute_signal_continuity(
            p,
            c,
            prior_collected_at_utc=(collected - timedelta(days=1)).isoformat(),
            current_collected_at=collected,
        )
        self.assertTrue(any(x["continuity_key"] for x in out["freshness_regressed"]))

    def test_window_overlap(self) -> None:
        now = datetime.now(timezone.utc)
        p = [
            _rec(
                pid="p1",
                src="s",
                st=SignalType.METRICS,
                observed=now,
                payload={
                    "file": "f.json",
                    "source_window_start": "2025-01-01T00:00:00Z",
                    "source_window_end": "2025-01-31T23:59:59Z",
                },
            ),
        ]
        c = [
            _rec(
                pid="p1",
                src="s",
                st=SignalType.METRICS,
                observed=now,
                payload={
                    "file": "f.json",
                    "source_window_start": "2025-01-15T00:00:00Z",
                    "source_window_end": "2025-02-15T00:00:00Z",
                },
            ),
        ]
        p = attach_canonical_to_records(p, now)
        c = attach_canonical_to_records(c, now)
        out = compute_signal_continuity(
            p,
            c,
            prior_collected_at_utc="2025-01-01T00:00:00+00:00",
            current_collected_at=now,
        )
        kinds = {x.get("kind") for x in out["window_continuity_broken"]}
        self.assertIn("overlap", kinds)

    def test_window_metadata_lost(self) -> None:
        now = datetime.now(timezone.utc)
        p = [
            _rec(
                pid="p1",
                src="s",
                st=SignalType.METRICS,
                observed=now,
                payload={
                    "file": "f.json",
                    "source_window_start": "2025-01-01T00:00:00Z",
                    "source_window_end": "2025-01-31T23:59:59Z",
                },
            ),
        ]
        c = [
            _rec(
                pid="p1",
                src="s",
                st=SignalType.METRICS,
                observed=now,
                payload={"file": "f.json"},
            ),
        ]
        p = attach_canonical_to_records(p, now)
        c = attach_canonical_to_records(c, now)
        out = compute_signal_continuity(
            p,
            c,
            prior_collected_at_utc="2025-01-01T00:00:00+00:00",
            current_collected_at=now,
        )
        kinds = [x.get("kind") for x in out["window_continuity_broken"]]
        self.assertIn("metadata_lost", kinds)


class TestPersistenceContinuity(unittest.TestCase):
    def test_bundle_roundtrip_has_signal_continuity(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            t = datetime.now(timezone.utc)
            r = [
                _rec(pid="p1", src="metrics_file", st=SignalType.METRICS, observed=t),
            ]
            save_collection(root, "p1", r)
            b = load_latest_bundle(root, "p1")
            self.assertIsNotNone(b)
            assert b is not None
            self.assertIsNotNone(b.signal_continuity)
            self.assertFalse(b.signal_continuity.get("compared"))

            time.sleep(1.1)
            save_collection(root, "p1", r)
            b2 = load_latest_bundle(root, "p1")
            assert b2 is not None
            self.assertTrue(b2.signal_continuity.get("compared"))
            path = root / "runs" / "signals" / "collections"
            files = sorted(path.glob("*.json"))
            self.assertGreaterEqual(len(files), 2)
            b3 = load_bundle_file(files[-1])
            self.assertIsNotNone(b3.signal_continuity)


if __name__ == "__main__":
    unittest.main()
