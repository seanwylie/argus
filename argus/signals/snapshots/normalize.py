"""Build :class:`SignalRecord` instances for business snapshot ingestion."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.ids import new_signal_id
from argus.signals.snapshots.models import BUSINESS_SIGNAL_KEY


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def parse_observed_at(raw: Any, fallback: datetime | None = None) -> datetime:
    if raw is None:
        return fallback or utc_now()
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    s = str(raw).strip()
    if not s:
        return fallback or utc_now()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return fallback or utc_now()


def business_signal_record(
    *,
    product_id: str,
    adapter_source: str,
    business_signal: str,
    observed_at: datetime,
    payload: dict[str, Any],
    confidence: float,
    tags: list[str] | None = None,
) -> SignalRecord:
    """One normalized record: ``signal_type=CUSTOM``, details in ``payload``."""
    base_tags = ["business_snapshot", adapter_source, business_signal]
    if tags:
        base_tags.extend(tags)
    merged: dict[str, Any] = {BUSINESS_SIGNAL_KEY: business_signal, **payload}
    return SignalRecord(
        id=new_signal_id("bsig"),
        product_id=product_id,
        signal_type=SignalType.CUSTOM,
        source=adapter_source,
        observed_at=observed_at,
        payload=merged,
        confidence=max(0.0, min(1.0, confidence)),
        tags=sorted(set(base_tags)),
    )


def pct_change(current: float, prior: float) -> float | None:
    if prior == 0:
        return None
    return (current - prior) / prior
