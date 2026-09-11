"""Temporal layer: snapshot adapters, collection recency, and operator visibility."""

from argus.temporal.freshness import freshness_metadata
from argus.temporal.models import (
    GLOBAL_PRODUCT_ID,
    PORTFOLIO_PRODUCT_ID,
    FreshnessBucket,
    FreshnessStatus,
    TemporalKind,
    TemporalSignal,
)
from argus.temporal.normalize import temporal_signal_record
from argus.temporal.recency import (
    compute_freshness,
    compute_freshness_score,
    signals_for_worst_freshness_aggregate,
    worst_freshness_bucket_from_signal_dicts,
    worst_freshness_status_from_signal_dicts,
)

__all__ = [
    "GLOBAL_PRODUCT_ID",
    "PORTFOLIO_PRODUCT_ID",
    "TemporalKind",
    "FreshnessBucket",
    "FreshnessStatus",
    "TemporalSignal",
    "compute_freshness",
    "compute_freshness_score",
    "signals_for_worst_freshness_aggregate",
    "worst_freshness_bucket_from_signal_dicts",
    "worst_freshness_status_from_signal_dicts",
    "freshness_metadata",
    "temporal_signal_record",
]
