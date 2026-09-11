"""
Shared contract for temporal hints on :class:`~argus.core.models.signal.SignalRecord` payloads.

Adapters set ``payload["temporal"]`` to a mapping; rules in ``rules.temporal`` interpret it.

**Freshness sidecar (separate):** optional ``payload["temporal_sidecar"]`` (or top-level
``source_window_start``, ``source_window_end``, ``freshness_sla``) feeds **age/status only**
in ``argus.temporal.freshness_compute`` — not semantic interpretation.
"""

from __future__ import annotations

# Keys inside payload["temporal"]
KEY_SPIKE_RATIO = "spike_ratio"
KEY_MARKET_SPIKE = "market_spike"
KEY_NEWS_ACCELERATION = "news_acceleration"
KEY_CONTEXT_STALE_SECONDS = "context_stale_seconds"
KEY_STALE_CONTEXT = "stale_context"
KEY_TOPIC = "topic"
KEY_MOMENTUM = "momentum"
KEY_DEADLINE = "deadline"
KEY_HOURS_REMAINING = "hours_remaining"
KEY_RISK_VELOCITY = "risk_velocity"
KEY_CURRENT_RISK = "current_risk"
KEY_NO_RECENT_EVIDENCE = "no_recent_evidence"
KEY_PRIOR_WINDOW_LABEL = "prior_window_label"
