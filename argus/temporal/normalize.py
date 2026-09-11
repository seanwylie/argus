"""Build :class:`~argus.core.models.signal.SignalRecord` for temporal snapshot adapters."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.temporal.freshness import freshness_metadata


def _new_signal_id(prefix: str = "sig") -> str:
    """Same shape as :func:`argus.signals.ids.new_signal_id` but local to avoid import cycles."""
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(raw: Any, fallback: datetime | None = None) -> datetime | None:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return fallback


def temporal_signal_record(
    *,
    product_id: str,
    adapter_source: str,
    temporal_kind: str,
    scope: str,
    source_timestamp: datetime | None,
    fetched_at: datetime,
    observed_at: datetime,
    normalized: dict[str, Any],
    snapshot_file: str,
    confidence: float,
    extra_tags: list[str] | None = None,
) -> SignalRecord:
    """
    ``observed_at`` is the primary event time for the record (usually ``source_timestamp``).

    ``payload`` nests temporal metadata and the normalized domain snapshot.
    """
    fresh = freshness_metadata(source_timestamp, fetched_at)
    payload: dict[str, Any] = {
        "temporal": True,
        "temporal_kind": temporal_kind,
        "scope": scope,
        "source_timestamp_utc": source_timestamp.isoformat() if source_timestamp else None,
        "fetched_at_utc": fetched_at.isoformat(),
        "freshness": fresh,
        "snapshot_file": snapshot_file,
        "normalized": normalized,
    }
    tags = ["temporal_snapshot", temporal_kind, scope, adapter_source]
    if extra_tags:
        tags.extend(extra_tags)
    return SignalRecord(
        id=_new_signal_id("tsig"),
        product_id=product_id,
        signal_type=SignalType.TEMPORAL,
        source=adapter_source,
        observed_at=observed_at,
        payload=payload,
        confidence=max(0.0, min(1.0, confidence)),
        tags=sorted(set(tags)),
    )
