"""Internal recency / inactivity snapshot → temporal signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.signals.snapshots.binding import parse_csv_dicts, resolve_temporal_product_id
from argus.temporal.models import TemporalKind
from argus.temporal.normalize import parse_timestamp, temporal_signal_record, utc_now

ADAPTER_ID = "temporal_recency_snapshot"


def _num(x: Any) -> float | None:
    if x is None or str(x).strip() == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def ingest_temporal_recency_snapshot(snapshot_path: Path, repo_root: Path) -> list:
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

    src_ts = parse_timestamp(data.get("source_timestamp") or data.get("reference_at"))
    last_act = parse_timestamp(data.get("last_activity_at") or data.get("last_signal_at"))
    fetched_ts = parse_timestamp(data.get("fetched_at")) or fetched
    observed = last_act or src_ts or fetched_ts

    days = data.get("days_since_activity") or data.get("days_inactive")
    try:
        days_f = float(days) if days is not None else None
    except (TypeError, ValueError):
        days_f = None

    norm: dict[str, Any] = {
        "last_activity_at_utc": last_act.isoformat() if last_act else None,
        "days_since_activity": days_f,
        "decay_score": _num(data.get("decay_score") or data.get("inactivity_score")),
        "last_artifact": str(data.get("last_artifact") or "").strip() or None,
    }
    norm = {k: v for k, v in norm.items() if v is not None and v != ""}
    conf = 0.8 if days_f is not None else 0.55

    rec = temporal_signal_record(
        product_id=pid,
        adapter_source=ADAPTER_ID,
        temporal_kind=TemporalKind.RECENCY.value,
        scope=scope,
        source_timestamp=src_ts or last_act,
        fetched_at=fetched_ts,
        observed_at=observed,
        normalized=norm,
        snapshot_file=snapshot_path.name,
        confidence=min(1.0, conf),
        extra_tags=["recency"],
    )
    return [rec]
