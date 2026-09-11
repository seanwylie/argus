"""Regression: signals + temporal latest bundles enforce normalized + temporally qualified rows."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.validation.validate import (
    validate_repo_artifacts,
    validate_signals_latest,
    validate_temporal_latest,
)


def _minimal_canonical_record() -> dict:
    return {
        "id": "r1",
        "product_id": "p1",
        "signal_type": "metrics",
        "source": "test",
        "observed_at": "2026-01-01T12:00:00+00:00",
        "payload": {"summary": "up"},
        "tags": [],
        "canonical": {
            "schema": "argus.canonical_signal.v1",
            "signal_id": "r1",
            "product_id": "p1",
            "category": "metrics",
            "value": True,
            "value_type": "boolean",
            "unit": None,
            "source_type": "adapter",
            "source_ref": "test",
            "provenance": {},
            "observed_at": "2026-01-01T12:00:00+00:00",
            "collected_at": "2026-01-01T12:00:00+00:00",
            "freshness_sla": None,
            "freshness_status": "recent",
            "trust_level": "medium",
            "collection_status": "ok",
        },
    }


class TestSignalsTemporalValidation(unittest.TestCase):
    def test_signals_rejects_row_without_canonical(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "signals" / "latest" / "p1.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": "p1",
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "records": [
                            {
                                "id": "r1",
                                "product_id": "p1",
                                "signal_type": "metrics",
                                "source": "test",
                                "observed_at": "2026-01-01T12:00:00+00:00",
                                "payload": {},
                                "tags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            issues = validate_signals_latest(root)
            self.assertTrue(any("missing canonical" in i.message for i in issues))

    def test_signals_accepts_empty_records(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "signals" / "latest" / "empty.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": "empty",
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "records": [],
                    }
                ),
                encoding="utf-8",
            )
            issues = validate_signals_latest(root)
            self.assertEqual(issues, [])

    def test_temporal_rejects_bad_row_freshness(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = root / "runs" / "temporal" / "latest" / "p1.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "schema": "argus.temporal_bundle.v1",
                        "product_id": "p1",
                        "repo_root": str(root),
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "source_signal_schema": "argus.signal_collection.v1",
                        "record_count": 1,
                        "worst_freshness_status": "fresh",
                        "signals": [
                            {
                                "schema": "argus.temporal_signal.v1",
                                "signal_id": "r1",
                                "freshness_status": "",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            issues = validate_temporal_latest(root)
            self.assertTrue(any("freshness_status" in i.message for i in issues))

    def test_validate_repo_ok_with_valid_signal_and_temporal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir(parents=True)
            sp = root / "runs" / "signals" / "latest" / "p1.json"
            sp.parent.mkdir(parents=True)
            sp.write_text(
                json.dumps(
                    {
                        "schema": "argus.signal_collection.v1",
                        "product_id": "p1",
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "records": [_minimal_canonical_record()],
                    }
                ),
                encoding="utf-8",
            )
            tp = root / "runs" / "temporal" / "latest" / "p1.json"
            tp.parent.mkdir(parents=True)
            tp.write_text(
                json.dumps(
                    {
                        "schema": "argus.temporal_bundle.v1",
                        "product_id": "p1",
                        "repo_root": str(root.resolve()),
                        "collected_at_utc": "2026-01-01T00:00:00+00:00",
                        "source_signal_schema": "argus.signal_collection.v1",
                        "record_count": 1,
                        "worst_freshness_status": "fresh",
                        "signals": [
                            {
                                "schema": "argus.temporal_signal.v1",
                                "signal_id": "r1",
                                "product_id": "p1",
                                "signal_type": "metrics",
                                "source": "test",
                                "observed_at": "2026-01-01T12:00:00+00:00",
                                "fetched_at": "2026-01-01T12:00:00+00:00",
                                "collected_at": "2026-01-01T12:00:00+00:00",
                                "freshness_score": 1.0,
                                "freshness_bucket": "realtime",
                                "confidence": 0.9,
                                "freshness_age": 0.0,
                                "freshness_status": "fresh",
                                "payload": {},
                                "tags": [],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            rep = validate_repo_artifacts(root)
            self.assertTrue(rep.ok, msg=[(i.path, i.message) for i in rep.issues])


if __name__ == "__main__":
    unittest.main()
