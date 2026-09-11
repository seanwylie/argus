"""Temporal snapshot parsers, binding, freshness, and ingest."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.signals.snapshots.binding import resolve_temporal_product_id
from argus.signals.snapshots.temporal_market import ingest_temporal_market_snapshot
from argus.signals.snapshots.temporal_news import ingest_temporal_news_snapshot
from argus.signals.snapshots.temporal_recency import ingest_temporal_recency_snapshot
from argus.temporal.freshness import freshness_metadata
from argus.temporal.ingest import ingest_temporal_snapshots


class TestTemporalBinding(unittest.TestCase):
    def test_global_path(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "temporal" / "snapshots" / "global" / "temporal_market_x.json"
            p.parent.mkdir(parents=True)
            p.write_text("{}", encoding="utf-8")
            pid, scope = resolve_temporal_product_id(
                p,
                root,
                {"symbol": "X", "change_pct": 1.0, "source_timestamp": "2026-01-01T00:00:00Z"},
            )
            self.assertEqual(pid, "_global_")
            self.assertEqual(scope, "global")

    def test_portfolio_scope_in_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "x.json"
            pid, scope = resolve_temporal_product_id(
                p,
                root,
                {"scope": "portfolio", "headline_spike_score": 0.5, "source_timestamp": "2026-01-01T00:00:00Z"},
            )
            self.assertEqual(pid, "_portfolio_")
            self.assertEqual(scope, "portfolio")


class TestTemporalNormalize(unittest.TestCase):
    def test_freshness_bucket(self) -> None:
        src = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
        fetch = datetime(2026, 1, 1, 12, 10, tzinfo=timezone.utc)
        m = freshness_metadata(src, fetch)
        self.assertEqual(m["bucket"], "fresh")
        self.assertGreaterEqual(m["age_seconds"], 0)


class TestTemporalParsing(unittest.TestCase):
    def test_market_json(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products" / "p1" / "metrics" / "snapshots").mkdir(parents=True)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "temporal_market_demo.json"
            p.write_text(
                json.dumps(
                    {
                        "source_timestamp": "2026-04-01T15:00:00Z",
                        "fetched_at": "2026-04-01T15:05:00Z",
                        "symbol": "DEMO",
                        "change_pct": -2.5,
                        "volume_spike": True,
                    }
                ),
                encoding="utf-8",
            )
            recs = ingest_temporal_market_snapshot(p, root)
            self.assertEqual(len(recs), 1)
            r = recs[0]
            self.assertEqual(r.product_id, "p1")
            self.assertEqual(r.signal_type.value, "temporal")
            self.assertIn("freshness", r.payload)
            self.assertEqual(r.payload["normalized"]["symbol"], "DEMO")

    def test_news_and_recency(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs" / "temporal" / "snapshots" / "portfolio").mkdir(parents=True)
            pn = root / "runs" / "temporal" / "snapshots" / "portfolio" / "temporal_news_fixture.json"
            pn.write_text(
                json.dumps(
                    {
                        "scope": "portfolio",
                        "source_timestamp": "2026-04-02T10:00:00Z",
                        "headline_spike_score": 0.91,
                        "headlines": ["Market shifts"],
                    }
                ),
                encoding="utf-8",
            )
            nr = ingest_temporal_news_snapshot(pn, root)
            self.assertEqual(nr[0].product_id, "_portfolio_")

            (root / "runs" / "temporal" / "snapshots" / "products" / "p2").mkdir(parents=True)
            pr = root / "runs" / "temporal" / "snapshots" / "products" / "p2" / "temporal_recency_fixture.json"
            pr.write_text(
                json.dumps(
                    {
                        "source_timestamp": "2026-04-01T00:00:00Z",
                        "last_activity_at": "2026-03-20T00:00:00Z",
                        "days_since_activity": 14,
                        "decay_score": 0.42,
                    }
                ),
                encoding="utf-8",
            )
            rr = ingest_temporal_recency_snapshot(pr, root)
            self.assertEqual(rr[0].product_id, "p2")


class TestTemporalIngestCli(unittest.TestCase):
    def test_ingest_groups_buckets(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs" / "temporal" / "snapshots" / "global").mkdir(parents=True)
            (root / "runs" / "temporal" / "snapshots" / "products" / "px").mkdir(parents=True)
            (root / "runs" / "temporal" / "snapshots" / "global" / "temporal_market_g.json").write_text(
                json.dumps(
                    {
                        "source_timestamp": "2026-01-01T00:00:00Z",
                        "symbol": "G",
                        "change_pct": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            (root / "runs" / "temporal" / "snapshots" / "products" / "px" / "temporal_recency_x.json").write_text(
                json.dumps(
                    {
                        "source_timestamp": "2026-01-01T00:00:00Z",
                        "last_activity_at": "2026-01-01T00:00:00Z",
                        "days_since_activity": 0,
                    }
                ),
                encoding="utf-8",
            )
            res = ingest_temporal_snapshots(root, merge=False, no_save=True)
            self.assertIn("_global_", res["saved_counts"])
            self.assertIn("px", res["saved_counts"])


if __name__ == "__main__":
    unittest.main()
