"""High-level decision intents (stored in DecisionCandidate.metadata["intent"])."""

from __future__ import annotations

from enum import StrEnum


class DecisionIntent(StrEnum):
    """Human-facing decision labels (not the same as ActionType)."""

    IMPROVE_PRODUCT = "improve_product"
    GATHER_MORE_DATA = "gather_more_data"
    LAUNCH_EXPERIMENT = "launch_experiment"
    REDUCE_COST = "reduce_cost"
    HOLD_STEADY = "hold_steady"
    DEPRECATE_PRODUCT = "deprecate_product"
    KILL_PRODUCT = "kill_product"
    ESCALATE_TO_HUMAN = "escalate_to_human"
