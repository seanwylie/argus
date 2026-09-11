"""Stakeholder role text, rubric hints, and default weights (extend by adding entries)."""

from __future__ import annotations

from argus.refinement.models import StakeholderType

ROLE_DESCRIPTION: dict[StakeholderType, str] = {
    StakeholderType.FINANCE: "Unit economics, runway, and capital efficiency.",
    StakeholderType.GROWTH: "Distribution, acquisition, retention, and measurable growth loops.",
    StakeholderType.PRODUCT: "Scope, outcomes, roadmap risk, and user-facing clarity.",
    StakeholderType.UX: "Usability, clarity of flows, and cognitive load.",
    StakeholderType.TECHNICAL: "Feasibility, complexity, and operational burden.",
    StakeholderType.ARCHITECTURE: "System shape, boundaries, coupling, and long-term maintainability.",
    StakeholderType.DOCTRINE: "Alignment with declared product doctrine and constraints.",
    StakeholderType.CREATIVE: "Differentiation, narrative, and memorable positioning.",
    StakeholderType.BONES: "Skeleton plan quality: sequencing, safety, preservation of product intent (bones = structural spine).",
    StakeholderType.INVESTOR: "Funding narrative, investability, and risk/reward framing (no repo authority).",
    StakeholderType.MARKETER: "Positioning, clarity of value prop, and message-market fit pressure (no repo authority).",
    StakeholderType.CUSTOMER_PROXY: "User Jobs-style pressure: why switch, who cares, clarity of promise (no repo authority).",
}

REVIEW_RUBRIC: dict[StakeholderType, str] = {
    StakeholderType.FINANCE: "Score economics plausibility; flag missing cost assumptions.",
    StakeholderType.GROWTH: "Score distribution coherence; require testable next steps.",
    StakeholderType.PRODUCT: "Score scope clarity and outcome hypotheses.",
    StakeholderType.UX: "Score clarity of user value and interaction path.",
    StakeholderType.TECHNICAL: "Score feasibility vs constraints; flag integration risks.",
    StakeholderType.ARCHITECTURE: "Score architectural risks and coupling.",
    StakeholderType.DOCTRINE: "Check explicit doctrine fit; cite conflicts.",
    StakeholderType.CREATIVE: "Score differentiation; avoid blocking on taste alone.",
    StakeholderType.BONES: "Check sequencing, safety checkpoints, and alignment to product intent.",
    StakeholderType.INVESTOR: "Assess pitch strength only; do not assert codebase facts.",
    StakeholderType.MARKETER: "Assess message clarity and differentiation from the pitch; no implementation claims.",
    StakeholderType.CUSTOMER_PROXY: "Assess whether the promise is compelling to a skeptical user; no repo claims.",
}
