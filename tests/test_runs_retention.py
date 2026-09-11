"""Disk retention for runs/signals/collections and builder work orders."""

from __future__ import annotations

import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.runs_retention import (
    prune_builder_work_orders_stamped,
    prune_signal_collections,
)
from argus.signals.ids import new_signal_id
from argus.signals.persistence import save_collection


class TestSignalCollectionsRetention(unittest.TestCase):
    def test_prune_keeps_newest_per_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "runs" / "signals" / "collections"
            base.mkdir(parents=True)
            for ts in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
                p = base / f"{ts}_alpha.json"
                p.write_text(json.dumps({"x": ts}), encoding="utf-8")

            summary = prune_signal_collections(root, keep_per_product=2, dry_run=False)
            self.assertEqual(summary["deleted_files"], 1)
            remaining = sorted(x.name for x in base.iterdir())
            self.assertEqual(
                remaining,
                ["20260102T000000Z_alpha.json", "20260103T000000Z_alpha.json"],
            )

    def test_prune_dry_run_no_delete(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "runs" / "signals" / "collections"
            base.mkdir(parents=True)
            for ts in ("20260101T000000Z", "20260102T000000Z"):
                (base / f"{ts}_b.json").write_text("{}", encoding="utf-8")
            summary = prune_signal_collections(root, keep_per_product=1, dry_run=True)
            self.assertEqual(summary["would_delete_files"], 1)
            self.assertEqual(len(list(base.iterdir())), 2)

    def test_save_collection_triggers_prune_via_hook(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            os.environ["ARGUS_SIGNALS_COLLECTIONS_KEEP_PER_PRODUCT"] = "2"
            try:
                base = root / "runs" / "signals" / "collections"
                base.mkdir(parents=True)
                for ts in ("19990101T000000Z", "19990102T000000Z", "19990103T000000Z"):
                    (base / f"{ts}_hookpid.json").write_text(
                        json.dumps(
                            {
                                "schema": "argus.signal_collection.v1",
                                "product_id": "hookpid",
                                "collected_at_utc": f"{ts[:4]}-01-01T00:00:00+00:00",
                                "repo_root": str(root),
                                "record_count": 0,
                                "records": [],
                            }
                        ),
                        encoding="utf-8",
                    )
                rec = SignalRecord(
                    id=new_signal_id(),
                    product_id="hookpid",
                    signal_type=SignalType.FILESYSTEM,
                    source="filesystem",
                    observed_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                    payload={"note": "x"},
                )
                save_collection(root, "hookpid", [rec])
                names = sorted(p.name for p in base.glob("*_hookpid.json"))
                self.assertEqual(len(names), 2, names)
                self.assertTrue(any("2026" in n or "202601" in n for n in names), names)
            finally:
                os.environ.pop("ARGUS_SIGNALS_COLLECTIONS_KEEP_PER_PRODUCT", None)


class TestWorkOrdersRetention(unittest.TestCase):
    def test_prune_stamped_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "builder" / "work_orders" / "p1"
            d.mkdir(parents=True)
            (d / "latest.json").write_text("{}", encoding="utf-8")
            (d / "latest.md").write_text("# x", encoding="utf-8")
            for ts in ("20260101T000000Z", "20260102T000000Z", "20260103T000000Z"):
                (d / f"{ts}.json").write_text("{}", encoding="utf-8")

            summary = prune_builder_work_orders_stamped(root, "p1", keep_stamped=2)
            self.assertEqual(summary["deleted_files"], 1)
            json_files = sorted(x.name for x in d.iterdir() if x.suffix == ".json")
            self.assertIn("latest.json", json_files)
            self.assertEqual(len(json_files), 3)


if __name__ == "__main__":
    unittest.main()
