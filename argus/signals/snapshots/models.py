"""Business-facing normalized signal kinds (stored in ``SignalRecord.payload``)."""

from __future__ import annotations

# Payload key for the normalized business interpretation
BUSINESS_SIGNAL_KEY = "business_signal"

# Canonical values for findings / dashboards (deterministic rules map snapshots → these)
TRAFFIC_UP = "traffic_up"
TRAFFIC_DOWN = "traffic_down"
CONVERSION_DOWN = "conversion_down"
COST_SPIKE = "cost_spike"
REVENUE_FLAT = "revenue_flat"
NO_USAGE = "no_usage"
RETENTION_DROP = "retention_drop"
TRACTION_SIGNAL = "traction_signal"

ALL_BUSINESS_SIGNALS: frozenset[str] = frozenset(
    {
        TRAFFIC_UP,
        TRAFFIC_DOWN,
        CONVERSION_DOWN,
        COST_SPIKE,
        REVENUE_FLAT,
        NO_USAGE,
        RETENTION_DROP,
        TRACTION_SIGNAL,
    }
)
