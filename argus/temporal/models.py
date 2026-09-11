"""Temporal domain: snapshot kinds, synthetic product ids, and collection-level recency views."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from argus.core.models.signal import SignalRecord

# Synthetic product ids for non-product-bound signals (persisted under runs/signals/latest/)
GLOBAL_PRODUCT_ID = "_global_"
PORTFOLIO_PRODUCT_ID = "_portfolio_"


class TemporalKind(StrEnum):
    """Kinds of time-aware snapshot ingested via temporal snapshot adapters."""

    MARKET = "market"
    NEWS = "news"
    RECENCY = "recency"


class FreshnessBucket(StrEnum):
    """Deterministic recency class from observation age (see ``recency`` module)."""

    REALTIME = "realtime"
    RECENT = "recent"
    AGING = "aging"
    STALE = "stale"
    UNKNOWN = "unknown"


class FreshnessStatus(StrEnum):
    """SLA- or tier-based freshness vs a reference clock (see ``freshness_compute``)."""

    FRESH = "fresh"
    AGING = "aging"
    STALE = "stale"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


@dataclass
class TemporalSignal:
    """
    A :class:`~argus.core.models.signal.SignalRecord` plus **fetched_at** / **collected_at** and
    recency metadata.

    This is a derived view for ``runs/temporal/`` bundles. Canonical rows remain ``SignalRecord`` in
    ``runs/signals/``. ``freshness_score`` / ``freshness_bucket`` use ``observed_at`` vs the
    reference clock (legacy dashboard buckets). ``freshness_age`` / ``freshness_status`` use the
    temporal sidecar anchor rules in ``freshness_compute`` (window end when present).
    """

    signal_id: str
    product_id: str
    signal_type: str
    source: str
    observed_at: datetime
    fetched_at: datetime
    collected_at: datetime
    freshness_score: float
    freshness_bucket: FreshnessBucket
    confidence: float | None
    freshness_age: float = 0.0
    freshness_status: FreshnessStatus = FreshnessStatus.UNKNOWN
    source_window_start: str | None = None
    source_window_end: str | None = None
    freshness_sla: float | None = None
    source_window_span_seconds: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    schema: str = "argus.temporal_signal.v1"

    @classmethod
    def from_signal_record(
        cls,
        record: SignalRecord,
        *,
        fetched_at: datetime,
        collected_at: datetime,
        freshness_score: float,
        freshness_bucket: FreshnessBucket,
        freshness_age: float,
        freshness_status: FreshnessStatus,
        source_window_start: str | None = None,
        source_window_end: str | None = None,
        freshness_sla: float | None = None,
        source_window_span_seconds: float | None = None,
    ) -> TemporalSignal:
        return cls(
            signal_id=record.id,
            product_id=record.product_id,
            signal_type=record.signal_type.value,
            source=record.source,
            observed_at=record.observed_at,
            fetched_at=fetched_at,
            collected_at=collected_at,
            freshness_score=freshness_score,
            freshness_bucket=freshness_bucket,
            confidence=record.confidence,
            freshness_age=freshness_age,
            freshness_status=freshness_status,
            source_window_start=source_window_start,
            source_window_end=source_window_end,
            freshness_sla=freshness_sla,
            source_window_span_seconds=source_window_span_seconds,
            payload=dict(record.payload),
            tags=list(record.tags),
        )
