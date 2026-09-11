"""
Normalize and validate external signal records (strict fields, advisory only).
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

ALLOWED_SOURCES = frozenset(
    {
        "manual_seed",
        "tiktok_metrics",
        "adsense",
        "fixture",
        "google_analytics",
    }
)
ALLOWED_SIGNAL_TYPES = frozenset(
    {
        "engagement",
        "revenue",
        "trend",
        "traffic",
        "other",
        "audience",
        "conversion",
    }
)
ALLOWED_FRESHNESS = frozenset({"fresh", "stale", "unknown"})
ALLOWED_CONFIDENCE = frozenset({"low", "medium", "high"})

# Default: observed older than this is stale (hours).
DEFAULT_STALE_AFTER_HOURS = 168.0


def _parse_observed_at(raw: str | None) -> datetime | None:
    if not raw or not str(raw).strip():
        return None
    s = str(raw).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _compute_freshness(
    observed_at_utc: datetime | None,
    *,
    reference: datetime,
    stale_after_hours: float,
) -> str:
    if observed_at_utc is None:
        return "unknown"
    delta = reference - observed_at_utc
    if delta.total_seconds() < 0:
        return "fresh"
    limit = stale_after_hours * 3600.0
    if delta.total_seconds() <= limit:
        return "fresh"
    return "stale"


_ENTITY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]{0,127}$")


def normalize_signal(
    raw: dict[str, Any],
    *,
    reference_now: datetime | None = None,
    stale_after_hours: float = DEFAULT_STALE_AFTER_HOURS,
) -> tuple[dict[str, Any] | None, str | None]:
    """
    Return ``(normalized, error)`` — error set if validation fails.
    """
    ref = reference_now or datetime.now(timezone.utc)
    src = str(raw.get("source") or "").strip()
    if src not in ALLOWED_SOURCES:
        return None, f"source must be one of {sorted(ALLOWED_SOURCES)}"

    st = str(raw.get("signal_type") or "").strip()
    if st not in ALLOWED_SIGNAL_TYPES:
        return None, f"signal_type must be one of {sorted(ALLOWED_SIGNAL_TYPES)}"

    ent = str(raw.get("entity") or "").strip()
    if not ent or not _ENTITY_RE.match(ent):
        return None, "entity must be a short identifier (1–128 chars, [a-zA-Z0-9_.-])"

    unit = str(raw.get("unit") or "").strip()
    if not unit or len(unit) > 64:
        return None, "unit is required (max 64 chars), e.g. views, revenue_usd"

    val = raw.get("value")
    if val is not None and not isinstance(val, (int, float, str, dict, list)):
        return None, "value must be JSON-serializable (number, string, or small object/array)"

    obs_raw = raw.get("observed_at_utc")
    obs_dt = _parse_observed_at(obs_raw if isinstance(obs_raw, str) else None)
    obs_out = obs_dt.isoformat().replace("+00:00", "Z") if obs_dt else None

    conf = str(raw.get("confidence") or "low").strip().lower()
    if conf not in ALLOWED_CONFIDENCE:
        return None, f"confidence must be one of {sorted(ALLOWED_CONFIDENCE)}"

    freshness_override = str(raw.get("freshness_status") or "").strip().lower()
    if freshness_override and freshness_override in ALLOWED_FRESHNESS:
        fs = freshness_override
    else:
        fs = _compute_freshness(obs_dt, reference=ref, stale_after_hours=stale_after_hours)

    prov = str(raw.get("provenance") or "").strip()
    if not prov:
        return None, "provenance is required (how this signal was obtained)"

    notes = str(raw.get("notes") or "").strip() or None

    out: dict[str, Any] = {
        "source": src,
        "signal_type": st,
        "entity": ent,
        "value": val,
        "unit": unit,
        "observed_at_utc": obs_out,
        "freshness_status": fs,
        "confidence": conf,
        "provenance": prov,
    }
    if notes:
        out["notes"] = notes
    return out, None
