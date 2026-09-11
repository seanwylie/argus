"""Local news / headline spike snapshot → temporal signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.signals.snapshots.binding import parse_csv_dicts, resolve_temporal_product_id
from argus.temporal.models import TemporalKind
from argus.temporal.normalize import parse_timestamp, temporal_signal_record, utc_now

ADAPTER_ID = "temporal_news_snapshot"


def ingest_temporal_news_snapshot(snapshot_path: Path, repo_root: Path) -> list:
    text = snapshot_path.read_text(encoding="utf-8")
    fetched = utc_now()
    if snapshot_path.suffix.lower() == ".csv":
        rows = parse_csv_dicts(text)
        if not rows:
            return []
        data = {k: v for k, v in rows[0].items()}
        pid, scope = resolve_temporal_product_id(snapshot_path, repo_root, None, csv_first_row=rows[0])
    else:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            return []
        data = raw
        pid, scope = resolve_temporal_product_id(snapshot_path, repo_root, data)

    src_ts = parse_timestamp(data.get("source_timestamp") or data.get("window_start"))
    fetched_ts = parse_timestamp(data.get("fetched_at")) or fetched
    observed = src_ts or fetched_ts

    headlines = data.get("headlines")
    if isinstance(headlines, list):
        hl = [str(x) for x in headlines[:24]]
    else:
        hl = []
    spike = data.get("headline_spike_score") or data.get("spike_score")
    try:
        spike_f = float(spike) if spike is not None else None
    except (TypeError, ValueError):
        spike_f = None

    norm: dict[str, Any] = {
        "headline_spike_score": spike_f,
        "headlines": hl,
        "topic": str(data.get("topic") or "").strip() or None,
    }
    norm = {k: v for k, v in norm.items() if v is not None and v != "" and v != []}
    conf = 0.78 if spike_f is not None else 0.5

    rec = temporal_signal_record(
        product_id=pid,
        adapter_source=ADAPTER_ID,
        temporal_kind=TemporalKind.NEWS.value,
        scope=scope,
        source_timestamp=src_ts,
        fetched_at=fetched_ts,
        observed_at=observed,
        normalized=norm,
        snapshot_file=snapshot_path.name,
        confidence=min(1.0, conf),
        extra_tags=["news"],
    )
    return [rec]
