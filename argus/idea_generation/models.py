"""Canonical structured ideas for products, experiments, content, and monetization."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class IdeaType(StrEnum):
    """Strategy class for the idea."""

    EXPLOIT = "exploit"  # same pattern, optimize
    EXPLORE = "explore"  # adjacent variation
    INVENT = "invent"  # new category / unusual combination


class IdeaSource(StrEnum):
    """Where the idea was derived from."""

    SIGNALS = "signals"
    FINDINGS = "findings"
    ADVISORS = "advisors"
    SYNTHESIS = "synthesis"
    MUTATION = "mutation"


def new_idea_id(prefix: str = "idea") -> str:
    """Deterministic-enough id: timestamp + short hash of prefix."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    h = hashlib.sha256(f"{prefix}:{ts}".encode()).hexdigest()[:8]
    return f"{prefix}_{ts}_{h}"


@dataclass
class Idea:
    """
    Structured idea record — canonical type for ``runs/ideas/`` artifacts.

    Scores are in ``[0, 1]`` unless noted; interpretation is documented in ``score.py``.
    """

    idea_id: str
    title: str
    description: str
    type: IdeaType
    source: IdeaSource
    novelty_score: float
    adjacency_score: float
    expected_value_score: float
    confidence_score: float
    cost_estimate: str
    channel_type: str
    monetization_type: str
    rationale: str
    #: How much this idea increases portfolio diversity (channel/monetization/pattern rarity).
    diversity_impact_score: float = 0.0
    product_id: str | None = None
    parent_idea_id: str | None = None
    #: Optional LLM enrichment (title/description/rationale unchanged; scoring uses canonical fields).
    llm_expansion: dict[str, Any] | None = None
    #: Optional deterministic nudge from ``argus.audit`` (explainable; does not replace scores wholesale).
    audit_adjustment: dict[str, Any] | None = None
    #: When ``source`` is ``signals``, hygiene flags + quality tier for the originating :class:`~argus.core.models.signal.SignalRecord`.
    signal_hygiene: dict[str, Any] | None = None
    #: When ``source`` is ``signals``, ``signal_type`` + ``adapter_source`` for mechanical diversity caps.
    signal_provenance: dict[str, Any] | None = None
    #: Decision/findings tone alignment (rank multiplier + ``aligned`` / ``overstated``); no text rewrites.
    tone_alignment: dict[str, Any] | None = None
    #: Synthesis-only: inspectable grounding (sources + strength + kind); see ``synthesis_grounding.py``.
    grounding: dict[str, Any] | None = None
    schema: str = "argus.idea.v2"

    def __post_init__(self) -> None:
        if isinstance(self.type, str):
            self.type = IdeaType(self.type)
        if isinstance(self.source, str):
            self.source = IdeaSource(self.source)
        self.novelty_score = max(0.0, min(1.0, float(self.novelty_score)))
        self.adjacency_score = max(0.0, min(1.0, float(self.adjacency_score)))
        self.expected_value_score = max(0.0, min(1.0, float(self.expected_value_score)))
        self.confidence_score = max(0.0, min(1.0, float(self.confidence_score)))
        self.diversity_impact_score = max(0.0, min(1.0, float(self.diversity_impact_score)))


@dataclass
class IdeasBundle:
    """One generation run persisted to disk."""

    generated_at_utc: str
    repo_root: str
    ideas: list[Idea] = field(default_factory=list)
    product_id: str | None = None  # scope when generate targeted one product
    schema: str = "argus.ideas_bundle.v1"
    meta: dict[str, Any] = field(default_factory=dict)
