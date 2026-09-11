"""Derive :class:`TemporalSignal` rows from existing :class:`SignalRecord` collections."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.core.serialize import to_jsonable
from argus.temporal.freshness_compute import compute_signal_freshness_view
from argus.temporal.models import TemporalSignal
from argus.temporal.recency import compute_freshness, worst_freshness_status_from_signal_dicts


def temporal_signals_from_records(
    records: list[SignalRecord],
    *,
    fetched_at: datetime,
    reference_time: datetime | None = None,
) -> list[TemporalSignal]:
    """
    Enrich signal records with collection-level recency (not adapter payload ``freshness``).
    """
    ref = reference_time if reference_time is not None else datetime.now(timezone.utc)
    out: list[TemporalSignal] = []
    for r in records:
        score, bucket = compute_freshness(r.observed_at, reference_time=ref)
        fv = compute_signal_freshness_view(
            r,
            collected_at=fetched_at,
            reference_time=ref,
            canonical=r.canonical,
        )
        out.append(
            TemporalSignal.from_signal_record(
                r,
                fetched_at=fetched_at,
                collected_at=fetched_at,
                freshness_score=score,
                freshness_bucket=bucket,
                freshness_age=fv.freshness_age_seconds,
                freshness_status=fv.freshness_status,
                source_window_start=fv.source_window_start_iso,
                source_window_end=fv.source_window_end_iso,
                freshness_sla=fv.freshness_sla_seconds,
                source_window_span_seconds=fv.source_window_span_seconds,
            )
        )
    return out


def bundle_payload_dict(
    *,
    product_id: str,
    repo_root: str,
    collected_at_utc: str,
    temporal_signals: list[TemporalSignal],
    source_schema: str = "argus.signal_collection.v1",
    signal_continuity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rows = [to_jsonable(ts) for ts in temporal_signals]
    worst_status = worst_freshness_status_from_signal_dicts(
        [x for x in rows if isinstance(x, dict)]
    )
    out: dict[str, Any] = {
        "schema": "argus.temporal_bundle.v1",
        "product_id": product_id,
        "repo_root": repo_root,
        "collected_at_utc": collected_at_utc,
        "source_signal_schema": source_schema,
        "record_count": len(temporal_signals),
        "worst_freshness_status": worst_status,
        "signals": rows,
    }
    if signal_continuity is not None:
        out["signal_continuity"] = signal_continuity
    return out
