"""Advisor archetypes and structured responses (LLM-ready scaffold)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AdvisorArchetype(StrEnum):
    FINANCE = "finance"
    INVESTOR = "investor"
    TECHNICAL = "technical"
    MARKETING = "marketing"
    PRODUCT = "product"
    CREATIVE = "creative"


@dataclass(frozen=True)
class Advisor:
    """Registered advisor definition (prompt template filled by runner / future LLM)."""

    id: str
    archetype: AdvisorArchetype
    description: str
    prompt_template: str
    weight: float = 1.0


@dataclass
class AdvisorResponse:
    """One advisor output (deterministic stub or LLM JSON)."""

    advisor_id: str
    archetype: AdvisorArchetype
    recommendation: str
    rationale: str
    risk_assessment: str
    """Legacy single-line risk summary; kept for display compatibility."""
    risks: list[str] = field(default_factory=list)
    """Structured risk bullets (preferred when set)."""
    confidence: float | None = None
    """Advisor-reported confidence in this advice, 0–1 (LLM or heuristic)."""
    temporal_assumptions: list[str] = field(default_factory=list)
    """Explicit assumptions about time-bounded evidence (never invent live state)."""
    freshness_risk: str | None = None
    """Short label: e.g. low / moderate / high — tied to artifact staleness."""
    confidence_adjustment: float | None = None
    """Negative means confidence was discounted for stale or missing evidence."""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdvisorRunResult:
    """All advisors for one product in one run."""

    product_id: str
    repo_root: str
    generated_at_utc: str
    context_summary: str
    responses: list[AdvisorResponse] = field(default_factory=list)
    consultation_log_dir: str | None = None
    """Directory under ``runs/advisors/consultations/`` when local logging ran."""
    temporal_grounding: Any | None = None
    """Structured :class:`~argus.advisors.temporal.TemporalGrounding` when context was loaded."""


@dataclass
class ConsensusResult:
    """Aggregated view across advisors."""

    product_id: str
    repo_root: str
    generated_at_utc: str
    consensus_decision: str
    final_recommendation: str = ""
    """Human-readable summary aligned with ``consensus_decision``."""
    disagreement_signals: list[str] = field(default_factory=list)
    disagreement_summary: str = ""
    """Short prose describing where advisors diverged (if at all)."""
    confidence_score: float = 0.0
    per_advisor_stance: dict[str, float] = field(default_factory=dict)
    source_responses: list[AdvisorResponse] = field(default_factory=list)
    temporal_evidence_summary: str = ""
    """What artifact timestamps / signals grounded this consultation."""
    freshness_adjustment_applied: float = 0.0
    """How much consensus confidence was reduced for staleness (0–1)."""
