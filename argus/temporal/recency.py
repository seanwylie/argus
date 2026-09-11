"""
Deterministic **collection** recency: compare ``SignalRecord.observed_at`` to a reference time.

Separate from :func:`freshness.freshness_metadata`, which compares source vs fetch time inside
adapter payloads. This module powers ``runs/temporal/latest/*.json``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.temporal.models import FreshnessBucket, FreshnessStatus

REALTIME_MAX_AGE_S = 3_600
RECENT_MAX_AGE_S = 86_400
AGING_MAX_AGE_S = 7 * 86_400
SCORE_MAX_AGE_S = 30 * 86_400


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def observation_age_seconds(observed_at: datetime, reference_time: datetime) -> float:
    o = _ensure_utc(observed_at)
    r = _ensure_utc(reference_time)
    return max(0.0, (r - o).total_seconds())


def compute_freshness_score(age_seconds: float) -> float:
    if age_seconds < 0:
        age_seconds = 0.0
    if age_seconds >= SCORE_MAX_AGE_S:
        return 0.0
    return 1.0 - (age_seconds / SCORE_MAX_AGE_S)


def bucket_for_age(age_seconds: float) -> FreshnessBucket:
    if age_seconds < 0:
        return FreshnessBucket.UNKNOWN
    if age_seconds <= REALTIME_MAX_AGE_S:
        return FreshnessBucket.REALTIME
    if age_seconds <= RECENT_MAX_AGE_S:
        return FreshnessBucket.RECENT
    if age_seconds <= AGING_MAX_AGE_S:
        return FreshnessBucket.AGING
    return FreshnessBucket.STALE


# Serialized temporal rows (``argus.temporal_bundle.v1`` ``signals[]``) use these bucket strings.
_FRESHNESS_WORST_ORDER: tuple[str, ...] = (
    FreshnessBucket.REALTIME.value,
    FreshnessBucket.RECENT.value,
    FreshnessBucket.AGING.value,
    FreshnessBucket.STALE.value,
    FreshnessBucket.UNKNOWN.value,
)

# Worst-first: unknown is most degraded for operators.
_FRESHNESS_STATUS_WORST_ORDER: tuple[str, ...] = (
    FreshnessStatus.FRESH.value,
    FreshnessStatus.AGING.value,
    FreshnessStatus.STALE.value,
    FreshnessStatus.EXPIRED.value,
    FreshnessStatus.UNKNOWN.value,
)

# Synthetic rows from :mod:`argus.signals.manifest_collect` use epoch ``observed_at`` and must not
# dominate aggregate temporal health (they mark manifest/coverage gaps, not runtime staleness).
_MANIFEST_DECLARATION_SOURCE = "manifest_declaration"


def signals_for_worst_freshness_aggregate(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop manifest gap placeholders so worst-case reflects collected observability only."""
    out: list[dict[str, Any]] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        if str(s.get("source") or "").strip() == _MANIFEST_DECLARATION_SOURCE:
            continue
        out.append(s)
    return out


def worst_freshness_bucket_from_signal_dicts(signals: list[dict[str, Any]]) -> str | None:
    """
    Return the **stalest** collection-recency bucket among serialized temporal signal rows.

    Deterministic: if multiple rows exist, picks the maximum index in
    ``realtime → recent → aging → stale → unknown``. Unknown or missing buckets are ignored.

    Manifest declaration placeholders (see :func:`signals_for_worst_freshness_aggregate`) are excluded.
    """
    signals = signals_for_worst_freshness_aggregate(signals)
    worst_i = -1
    worst: str | None = None
    for s in signals:
        if not isinstance(s, dict):
            continue
        b = s.get("freshness_bucket")
        if not isinstance(b, str) or not b.strip():
            continue
        try:
            i = _FRESHNESS_WORST_ORDER.index(b)
        except ValueError:
            continue
        if i > worst_i:
            worst_i = i
            worst = b
    return worst


def worst_freshness_status_from_signal_dicts(signals: list[dict[str, Any]]) -> str | None:
    """
    Return the **worst** :class:`FreshnessStatus` among serialized temporal signal rows.

    Deterministic ordering: ``fresh → aging → stale → expired → unknown``.

    Manifest declaration placeholders are excluded from the aggregate (see module docstring).
    """
    signals = signals_for_worst_freshness_aggregate(signals)
    worst_i = -1
    worst: str | None = None
    for s in signals:
        if not isinstance(s, dict):
            continue
        st = s.get("freshness_status")
        if not isinstance(st, str) or not st.strip():
            continue
        try:
            i = _FRESHNESS_STATUS_WORST_ORDER.index(st)
        except ValueError:
            continue
        if i > worst_i:
            worst_i = i
            worst = st
    return worst


def compute_freshness(
    observed_at: datetime,
    *,
    reference_time: datetime,
) -> tuple[float, FreshnessBucket]:
    try:
        age = observation_age_seconds(observed_at, reference_time)
    except (TypeError, ValueError, OverflowError):
        return 0.0, FreshnessBucket.UNKNOWN
    return compute_freshness_score(age), bucket_for_age(age)
