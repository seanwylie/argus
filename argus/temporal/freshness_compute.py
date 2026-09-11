"""
Deterministic freshness age and status for normalized signals (no semantics, no LLM).

**SLA seconds precedence** in :func:`compute_signal_freshness_view`:
1. Parse ``canonical.freshness_sla`` when the collection normalized row has a
   :class:`~argus.core.models.canonical_signal.CanonicalSignal` (manifest and payload
   precedence are resolved when building canonical — see ``argus.signals.normalize``).
2. Else numeric/string sidecar ``freshness_sla`` under payload or ``temporal_sidecar``.

Optional sidecar fields may appear in ``SignalRecord.payload`` under ``temporal_sidecar`` or
top-level keys (see ``sidecar_keys``). Interpretation rules (``payload["temporal"]``) are
unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.signal import SignalRecord
from argus.temporal.models import FreshnessStatus
from argus.temporal.recency import (
    AGING_MAX_AGE_S,
    RECENT_MAX_AGE_S,
    SCORE_MAX_AGE_S,
    observation_age_seconds,
)

# --- Optional payload keys (also accepted under payload["temporal_sidecar"]) ---

KEY_TEMPORAL_SIDECAR = "temporal_sidecar"
KEY_SOURCE_WINDOW_START = "source_window_start"
KEY_SOURCE_WINDOW_END = "source_window_end"
KEY_FRESHNESS_SLA = "freshness_sla"


def _ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_iso_datetime(value: Any) -> datetime | None:
    """Parse ISO8601 string or pass through aware datetime; None on failure."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _ensure_utc(value)
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return _ensure_utc(dt)
    except (ValueError, TypeError, OSError):
        return None


_SLA_UNIT_MULT: dict[str, float] = {
    "ms": 0.001,
    "s": 1.0,
    "m": 60.0,
    "h": 3600.0,
    "d": 86400.0,
    "w": 604800.0,
}
_SLA_RE = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)$", re.I)


def parse_freshness_sla_string_to_seconds(raw: str | None) -> float | None:
    """
    Deterministic parse for canonical ``freshness_sla`` strings (manifest or payload).

    Returns ``None`` for unknown / best-effort / unparseable values.
    """
    if raw is None:
        return None
    s = str(raw).strip().lower()
    if not s or s in ("best_effort", "unknown", "none"):
        return None
    m = _SLA_RE.match(s)
    if m:
        val = float(m.group(1))
        unit = m.group(2).lower()
        mult = _SLA_UNIT_MULT.get(unit)
        return None if mult is None else val * mult
    try:
        v = float(s)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


def _coerce_sla_seconds(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def _merge_sidecar_payload(payload: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    nested = payload.get(KEY_TEMPORAL_SIDECAR)
    if isinstance(nested, dict):
        out.update(nested)
    for k in (KEY_SOURCE_WINDOW_START, KEY_SOURCE_WINDOW_END, KEY_FRESHNESS_SLA):
        if k in payload and k not in out:
            out[k] = payload[k]
    return out


@dataclass(frozen=True)
class SignalFreshnessView:
    """Age and status only — no product-quality judgment."""

    freshness_age_seconds: float
    freshness_status: FreshnessStatus
    observed_at_iso: str
    collected_at_iso: str
    source_window_start_iso: str | None
    source_window_end_iso: str | None
    source_window_span_seconds: float | None
    freshness_sla_seconds: float | None


def _status_from_age_no_sla(age_seconds: float) -> FreshnessStatus:
    if age_seconds < 0:
        return FreshnessStatus.UNKNOWN
    if age_seconds <= RECENT_MAX_AGE_S:
        return FreshnessStatus.FRESH
    if age_seconds <= AGING_MAX_AGE_S:
        return FreshnessStatus.AGING
    if age_seconds <= SCORE_MAX_AGE_S:
        return FreshnessStatus.STALE
    return FreshnessStatus.EXPIRED


def _status_from_age_with_sla(age_seconds: float, sla: float) -> FreshnessStatus:
    if age_seconds < 0:
        return FreshnessStatus.UNKNOWN
    if age_seconds <= 0.25 * sla:
        return FreshnessStatus.FRESH
    if age_seconds <= 0.75 * sla:
        return FreshnessStatus.AGING
    if age_seconds <= sla:
        return FreshnessStatus.STALE
    return FreshnessStatus.EXPIRED


def compute_signal_freshness_view(
    record: SignalRecord,
    *,
    collected_at: datetime,
    reference_time: datetime,
    canonical: CanonicalSignal | None = None,
) -> SignalFreshnessView:
    """
    Compute freshness age/status for one record.

    **Anchor** for age (seconds): ``reference_time - anchor``, where ``anchor`` is
    ``source_window_end`` if present and parseable (end of the described validity window), else
    ``record.observed_at``. Age is clamped to be non-negative — not a judgment of product quality.
    """
    ref = _ensure_utc(reference_time)
    coll = _ensure_utc(collected_at)
    side = _merge_sidecar_payload(record.payload if isinstance(record.payload, dict) else {})
    sla = None
    if canonical is not None and canonical.freshness_sla:
        sla = parse_freshness_sla_string_to_seconds(canonical.freshness_sla)
    if sla is None:
        sla = _coerce_sla_seconds(side.get(KEY_FRESHNESS_SLA))

    ws = parse_iso_datetime(side.get(KEY_SOURCE_WINDOW_START))
    we = parse_iso_datetime(side.get(KEY_SOURCE_WINDOW_END))

    span: float | None = None
    if ws is not None and we is not None:
        span = max(0.0, (we - ws).total_seconds())

    obs = _ensure_utc(record.observed_at)
    anchor = we if we is not None else obs

    try:
        age = observation_age_seconds(anchor, ref)
    except (TypeError, ValueError, OverflowError):
        return SignalFreshnessView(
            freshness_age_seconds=0.0,
            freshness_status=FreshnessStatus.UNKNOWN,
            observed_at_iso=obs.isoformat(),
            collected_at_iso=coll.isoformat(),
            source_window_start_iso=ws.isoformat() if ws else None,
            source_window_end_iso=we.isoformat() if we else None,
            source_window_span_seconds=span,
            freshness_sla_seconds=sla,
        )

    if sla is not None:
        st = _status_from_age_with_sla(age, sla)
    else:
        st = _status_from_age_no_sla(age)

    return SignalFreshnessView(
        freshness_age_seconds=age,
        freshness_status=st,
        observed_at_iso=obs.isoformat(),
        collected_at_iso=coll.isoformat(),
        source_window_start_iso=ws.isoformat() if ws else None,
        source_window_end_iso=we.isoformat() if we else None,
        source_window_span_seconds=span,
        freshness_sla_seconds=sla,
    )
