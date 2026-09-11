"""Purpose tags for context assembly (selects slices and caps)."""

from __future__ import annotations

from enum import Enum


class ContextPurpose(str, Enum):
    """Why the packet is being built — drives included sections."""

    REFINEMENT_GROUNDED = "refinement_grounded"
    IDEA_GENERATION = "idea_generation"
    PRODUCT_SPEC_DRAFT = "product_spec_draft"
    IMPLEMENTATION_PLAN = "implementation_plan"
    ADVISOR_PORTFOLIO = "advisor_portfolio"
    # Council routing: grounded vs outsider slices
    COUNCIL_IMPLEMENTATION_GROUNDED = "council_implementation_grounded"
    COUNCIL_OUTSIDER_PITCH = "council_outsider_pitch"
    COUNCIL_OUTSIDER_MARKET = "council_outsider_market"
