"""Debug report for temporal worst-freshness contributors."""

from __future__ import annotations

import json
from pathlib import Path

from argus.portfolio.temporal_worst_freshness_report import (
    TEMPORAL_WORST_FRESHNESS_DEBUG_SCHEMA,
    build_temporal_worst_freshness_report_payload,
    run_temporal_worst_freshness_report,
    temporal_worst_freshness_debug_dir,
)


def test_report_on_minimal_bundle(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "temporal" / "latest"
    d.mkdir(parents=True)
    bundle = {
        "schema": "argus.temporal_bundle.v1",
        "product_id": "p1",
        "repo_root": str(tmp_path),
        "collected_at_utc": "2026-01-01T00:00:00Z",
        "record_count": 2,
        "worst_freshness_status": "expired",
        "signals": [
            {"source": "manifest_declaration", "freshness_status": "expired", "signal_id": "a", "payload": {"path": "x"}},
            {"source": "metrics", "freshness_status": "fresh", "signal_id": "b"},
        ],
    }
    (d / "p1.json").write_text(json.dumps(bundle), encoding="utf-8")
    pl = build_temporal_worst_freshness_report_payload(tmp_path, product_ids=["p1"])
    assert pl["schema"] == TEMPORAL_WORST_FRESHNESS_DEBUG_SCHEMA
    prod = pl["products"][0]
    assert prod["worst_freshness_status_aggregate_collected_only"] == "fresh"
    assert prod["manifest_declaration_expired_count"] == 1


def test_run_writes_artifact(tmp_path: Path) -> None:
    d = tmp_path / "runs" / "temporal" / "latest"
    d.mkdir(parents=True)
    (d / "p1.json").write_text(
        json.dumps(
            {
                "schema": "argus.temporal_bundle.v1",
                "product_id": "p1",
                "repo_root": str(tmp_path),
                "collected_at_utc": "2026-01-01T00:00:00Z",
                "record_count": 1,
                "worst_freshness_status": "fresh",
                "signals": [{"source": "t", "freshness_status": "fresh", "signal_id": "z"}],
            }
        ),
        encoding="utf-8",
    )
    run_temporal_worst_freshness_report(tmp_path, product_ids=["p1"], write_artifacts=True)
    assert (temporal_worst_freshness_debug_dir(tmp_path) / "latest.json").is_file()
