"""
Lightweight signal quality hints for idea generation (no wire-format changes).

Classifies :class:`~argus.core.models.signal.SignalRecord` rows at read time so
downstream consumers can de-emphasize manifest gaps, placeholders, and
sentinel timestamps without dropping records.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from argus.core.models.signal import SignalRecord
from argus.signals.manifest_collect import PLACEHOLDER_SOURCE

# Local adapters whose rows are typically backed by repo files / execution artifacts.
_FILE_BACKED_SOURCES = frozenset(
    {
        "filesystem",
        "metrics_file",
        "analytics_file",
        "cost_file",
        "local_snapshots",
        "heartbeat",
        "execution",
        "temporal_snapshots",
    }
)

# Epoch-ish cutoff: first instant of 1972 UTC (seconds since Unix epoch).
_SENTINEL_TS_MAX = 31_536_000.0

# If this share of JSON leaves are null/empty-string, treat as placeholder-heavy.
_NULL_LEAF_RATIO_PLACEHOLDER = 0.65

_PLACEHOLDER_WORD_RE = re.compile(
    r"\bplaceholder\b|"
    r"\blorem ipsum\b|"
    r"\bstub\b|"
    r"\bt\.?b\.?d\.?\b|"
    r"\bto be determined\b|"
    r"\bnot implemented\b",
    re.IGNORECASE,
)


def _flatten_for_scan(obj: Any) -> str:
    """Stable lowercased string for substring heuristics."""
    try:
        return json.dumps(obj, sort_keys=True, default=str).lower()
    except (TypeError, ValueError):
        return str(obj).lower()


def _leaf_null_stats(obj: Any) -> tuple[int, int]:
    """Return (nullish_leaf_count, total_leaf_count)."""
    if obj is None:
        return (1, 1)
    if isinstance(obj, str):
        if not obj.strip():
            return (1, 1)
        return (0, 1)
    if isinstance(obj, (int, float, bool)):
        return (0, 1)
    if isinstance(obj, dict):
        if not obj:
            return (1, 1)
        nulls = total = 0
        for v in obj.values():
            n, t = _leaf_null_stats(v)
            nulls += n
            total += t
        return (nulls, total)
    if isinstance(obj, (list, tuple)):
        if not obj:
            return (1, 1)
        nulls = total = 0
        for v in obj:
            n, t = _leaf_null_stats(v)
            nulls += n
            total += t
        return (nulls, total)
    return (0, 1)


def _is_sentinel_timestamp(dt: datetime) -> bool:
    if dt.timetuple().tm_year <= 1971:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    try:
        return dt.timestamp() < _SENTINEL_TS_MAX
    except (OSError, OverflowError, ValueError):
        return dt.timetuple().tm_year <= 1971


def _is_placeholder_payload(payload: dict[str, Any], source: str, tags: list[str]) -> bool:
    blob = _flatten_for_scan(payload) + " " + source.lower() + " " + " ".join(tags).lower()
    if _PLACEHOLDER_WORD_RE.search(blob):
        return True
    nulls, total = _leaf_null_stats(payload)
    if total == 0:
        return False
    return (nulls / total) >= _NULL_LEAF_RATIO_PLACEHOLDER


def _is_manifest_declaration(record: SignalRecord) -> bool:
    if record.source == PLACEHOLDER_SOURCE:
        return True
    return "manifest_declaration" in record.tags


def _quality_rank(score: Literal["high", "medium", "low"]) -> int:
    return {"high": 0, "medium": 1, "low": 2}[score]


@dataclass(frozen=True)
class SignalHygiene:
    """Derived metadata for one signal row (additive; not persisted on SignalRecord)."""

    is_manifest_declaration: bool
    is_placeholder: bool
    is_sentinel_timestamp: bool
    signal_quality_score: Literal["high", "medium", "low"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_manifest_declaration": self.is_manifest_declaration,
            "is_placeholder": self.is_placeholder,
            "is_sentinel_timestamp": self.is_sentinel_timestamp,
            "signal_quality_score": self.signal_quality_score,
        }


def classify_signal_record(record: SignalRecord) -> SignalHygiene:
    """
    Classify a normalized signal for idea-generation hygiene.

    Rules:
    - ``is_manifest_declaration``: ``source`` is manifest gap rows or tagged as such.
    - ``is_sentinel_timestamp``: ``observed_at`` in or before 1971 or clearly epoch-derived.
    - ``is_placeholder``: obvious stub wording in payload/tags/source, or mostly-null JSON.
    - ``signal_quality_score``: low if any of the above; high for known file-backed adapters
      with clean payloads; else medium.
    """
    is_manifest = _is_manifest_declaration(record)
    is_sentinel = _is_sentinel_timestamp(record.observed_at)
    is_placeholder = _is_placeholder_payload(record.payload, record.source, record.tags)

    if is_manifest or is_placeholder or is_sentinel:
        score: Literal["high", "medium", "low"] = "low"
    elif record.source in _FILE_BACKED_SOURCES:
        score = "high"
    else:
        score = "medium"

    return SignalHygiene(
        is_manifest_declaration=is_manifest,
        is_placeholder=is_placeholder,
        is_sentinel_timestamp=is_sentinel,
        signal_quality_score=score,
    )


def sort_key_for_idea_generation(record: SignalRecord) -> tuple[int, str]:
    """Prefer higher-quality rows when selecting a bounded slice (stable tie-break on id)."""
    h = classify_signal_record(record)
    return (_quality_rank(h.signal_quality_score), record.id)


def apply_hygiene_score_nudge(idea_confidence: float, idea_ev: float, hygiene: SignalHygiene) -> tuple[float, float]:
    """
    Light nudge to confidence/EV from signal hygiene tier (``signal_quality_score``), after base scoring.

    Complements :func:`hygiene_mechanical_factor` on mechanical rank; does not remove rows.
    """
    q = hygiene.signal_quality_score
    c, e = idea_confidence, idea_ev
    if q == "low":
        return (max(0.0, c * 0.5), max(0.0, e * 0.55))
    if q == "medium":
        return (max(0.0, c * 0.88), max(0.0, e * 0.92))
    return (c, e)
