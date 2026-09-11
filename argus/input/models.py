"""Human-provided portfolio and product guidance."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

InputScope = Literal["global", "product"]
InputType = Literal[
    "priority_override",
    "constraint_update",
    "lifecycle_override",
    "note",
    "strategy",
]


@dataclass
class HumanInput:
    """
    Structured operator input influencing interpretation and prioritization.

    Unless ``structured_fields`` contains ``{"hard_override": true}`` (or ``mode: "hard"``),
    adjustments are blended using ``priority_weight`` (default 1.0).

    Examples (structured_fields):

    - ``focus_products``: list of product ids (e.g. prioritize in planning / decisions).
    - ``no_kill`` / ``suppress_kill``: soften deprecation and kill-oriented scoring.
    - ``prefer_growth_over_cost``: nudge growth findings up and cost findings down (soft).
    - ``target_lifecycle_stage``: e.g. ``grow`` / ``validate`` / ``maintain`` for light score nudges.
    - ``aws_monthly_cap_usd`` / ``total_monthly_cap_usd``: recorded on assessment metadata for audits.
    """

    id: str
    scope: InputScope
    product_id: str | None
    type: InputType
    content: str
    structured_fields: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    expires_at: str | None = None
    priority_weight: float = 1.0

    def __post_init__(self) -> None:
        if self.scope == "global":
            self.product_id = None
        elif not (self.product_id and str(self.product_id).strip()):
            raise ValueError("product scope requires product_id")
