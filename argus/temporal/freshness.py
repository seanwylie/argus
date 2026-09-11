"""Freshness metadata from source time vs fetch time."""

from __future__ import annotations

from datetime import datetime
from typing import Any


def freshness_metadata(
    source_timestamp: datetime | None,
    fetched_at: datetime,
) -> dict[str, Any]:
    """
    Compare when the data describes the world (``source_timestamp``) to when it was
    ingested locally (``fetched_at``).

    ``bucket`` is a coarse label for dashboards and rules.
    """
    if source_timestamp is None:
        return {
            "bucket": "unknown_source_time",
            "age_seconds": None,
            "source_lag_seconds": None,
        }
    lag = (fetched_at - source_timestamp).total_seconds()
    age_s = max(0.0, lag)
    if age_s < 900:
        bucket = "fresh"
    elif age_s < 86_400:
        bucket = "recent"
    else:
        bucket = "stale"
    return {
        "bucket": bucket,
        "age_seconds": age_s,
        "source_lag_seconds": lag,
    }
