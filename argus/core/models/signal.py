"""Normalized signal records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.enums import SeverityLevel, SignalType


@dataclass
class SignalRecord:
    """
    A single normalized observation from any signal source.

    ``payload`` is intentionally unstructured (JSON-compatible primitives and
    collections) for adapter-specific detail.
    """

    id: str
    product_id: str
    signal_type: SignalType
    source: str
    observed_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)
    severity_hint: SeverityLevel | None = None
    confidence: float | None = None
    tags: list[str] = field(default_factory=list)
    #: Collection-time normalized row (``argus.canonical_signal.v1``); None for legacy bundles.
    canonical: CanonicalSignal | None = None
