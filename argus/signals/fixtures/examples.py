"""Fixture-style signal records (not tied to a live collect run)."""

from __future__ import annotations

from datetime import datetime, timezone

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_signal_record


def example_signals_demo_content() -> list[SignalRecord]:
    """Illustrative signals for a fictional content-catalog product."""
    ts = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
    records = [
        SignalRecord(
            id="sig-example-scr-1",
            product_id="demo-content",
            signal_type=SignalType.FILESYSTEM,
            source="example",
            observed_at=ts,
            payload={
                "scenario": "no_new_clips",
                "note": "Illustrative: no new clips in 2 days",
                "last_clip_age_hours": 52,
            },
            severity_hint=SeverityLevel.MEDIUM,
            confidence=0.6,
            tags=["example", "content"],
        ),
        SignalRecord(
            id="sig-example-scr-2",
            product_id="demo-content",
            signal_type=SignalType.METRICS,
            source="example",
            observed_at=ts,
            payload={
                "clips_per_day": 0,
                "usable_clips_per_week": 2,
                "avg_clip_score": 0.71,
            },
            severity_hint=SeverityLevel.INFO,
            confidence=0.75,
            tags=["example", "metrics"],
        ),
    ]
    for r in records:
        validate_signal_record(r)
    return records


def example_signals_sample_service() -> list[SignalRecord]:
    """Illustrative signals for a small SaaS API product."""
    ts = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
    records = [
        SignalRecord(
            id="sig-example-sp-1",
            product_id="sample-service",
            signal_type=SignalType.COST,
            source="example",
            observed_at=ts,
            payload={
                "scenario": "aws_cost_spike",
                "monthly_usd": 48,
                "prior_month_usd": 12,
            },
            severity_hint=SeverityLevel.HIGH,
            confidence=0.55,
            tags=["example", "cost"],
        ),
        SignalRecord(
            id="sig-example-sp-2",
            product_id="sample-service",
            signal_type=SignalType.ANALYTICS,
            source="example",
            observed_at=ts,
            payload={
                "scenario": "traffic_drop",
                "views_7d": 420,
                "views_prior_7d": 980,
            },
            severity_hint=SeverityLevel.MEDIUM,
            confidence=0.62,
            tags=["example", "analytics"],
        ),
    ]
    for r in records:
        validate_signal_record(r)
    return records
