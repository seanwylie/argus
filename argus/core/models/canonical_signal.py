"""Canonical per-signal shape for persisted collections (deterministic, JSON-stable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class CanonicalSignal:
    """
    Normalized view of a :class:`~argus.core.models.signal.SignalRecord` for downstream consumers.

    Populated at collection write time; does not replace legacy ``SignalRecord`` fields on disk —
    both are stored (legacy row + ``canonical`` object).
    """

    signal_id: str
    product_id: str
    category: str
    value: Any
    value_type: str
    unit: str | None
    source_type: str
    source_ref: str
    provenance: dict[str, Any] = field(default_factory=dict)
    observed_at: str = ""
    collected_at: str = ""
    source_window_start: str | None = None
    source_window_end: str | None = None
    freshness_sla: str | None = None
    freshness_status: str = "unknown"
    trust_level: str = "unverified"
    collection_status: str = "ok"
    schema: str = "argus.canonical_signal.v1"
