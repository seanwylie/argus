"""Local market movement snapshot (JSON/CSV) → temporal :class:`~argus.core.models.signal.SignalRecord`."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.signals.snapshots.binding import (
    parse_csv_dicts,
    resolve_temporal_product_id,
)
from argus.temporal.models import TemporalKind
from argus.temporal.normalize import parse_timestamp, temporal_signal_record, utc_now

ADAPTER_ID = "temporal_market_snapshot"


def _num(x: Any) -> float | None:
    if x is None or str(x).strip() == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ingest_temporal_market_snapshot(snapshot_path: Path, repo_root: Path) -> list:
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

    src_ts = parse_timestamp(data.get("source_timestamp") or data.get("as_of"))
    fetched_ts = parse_timestamp(data.get("fetched_at") or data.get("fetched_at_utc")) or fetched
    observed = src_ts or fetched_ts

    norm: dict[str, Any] = {
        "symbol": str(data.get("symbol") or data.get("ticker") or "").strip(),
        "change_pct": _num(data.get("change_pct") or data.get("pct_change")),
        "volume_spike": bool(data.get("volume_spike") or data.get("volume_spike_flag")),
        "benchmark_change_pct": _num(data.get("benchmark_change_pct")),
    }
    # Drop empty symbol if other fields present
    conf = 0.82 if norm.get("change_pct") is not None else 0.55

    rec = temporal_signal_record(
        product_id=pid,
        adapter_source=ADAPTER_ID,
        temporal_kind=TemporalKind.MARKET.value,
        scope=scope,
        source_timestamp=src_ts,
        fetched_at=fetched_ts,
        observed_at=observed,
        normalized={k: v for k, v in norm.items() if v is not None and v != ""},
        snapshot_file=snapshot_path.name,
        confidence=conf,
        extra_tags=["market"],
    )
    return [rec]
