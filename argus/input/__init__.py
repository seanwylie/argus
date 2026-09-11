"""Structured human input (portfolio and product scope)."""

from argus.input.apply import (
    apply_planning_priority_nudge,
    apply_to_assessment,
    apply_to_candidates,
    apply_to_findings,
    merged_inputs_for_product,
    planning_suppress_kill_flag,
)
from argus.input.models import HumanInput
from argus.input.store import list_inputs, load_input, remove_input, save_input

__all__ = [
    "HumanInput",
    "apply_to_assessment",
    "apply_to_candidates",
    "apply_to_findings",
    "apply_planning_priority_nudge",
    "merged_inputs_for_product",
    "planning_suppress_kill_flag",
    "list_inputs",
    "load_input",
    "remove_input",
    "save_input",
]
