"""Trend and drift output models (deterministic, auditable)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TrendFlag(StrEnum):
    """High-level trend classification (may combine multiple)."""

    IMPROVING = "improving"
    STABLE = "stable"
    DRIFTING = "drifting"
    STAGNATING = "stagnating"
    RISK_INCREASING = "risk_increasing"
    ACTION_THRASHING = "action_thrashing"
    LIKELY_ABANDON = "likely_abandon"
    READY_FOR_SCALE_REVIEW = "ready_for_scale_review"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass
class TrendSummary:
    """Per-product trend + drift synthesis for one analysis window."""

    product_id: str
    window_size: int
    trend_flags: list[str]
    summary: str
    confidence: float
    recommended_interpretation: str
    drift_signals: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrendsRunPayload:
    """CLI / JSON artifact for one ``argus trends`` invocation."""

    schema: str = "argus.trends_run.v1"
    generated_at_utc: str = ""
    repo_root: str = ""
    command: str = ""
    summaries: list[TrendSummary] = field(default_factory=list)
