"""Optional constraints layered on autonomy policy (freshness, future gates)."""

from argus.autonomy.constraints.freshness import (
    freshness_autonomy_blockers,
    should_escalate_for_freshness,
)

__all__ = [
    "freshness_autonomy_blockers",
    "should_escalate_for_freshness",
]
