"""Canonical datatypes for artifact refinement sessions (inspectable JSON under ``runs/refinement/``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ArtifactType(StrEnum):
    IDEA = "idea"
    PRODUCT_SPEC = "product_spec"
    IMPLEMENTATION_PLAN = "implementation_plan"


class SessionStatus(StrEnum):
    DRAFT = "draft"
    IN_REVIEW = "in_review"
    REFINING = "refining"
    APPROVED = "approved"
    APPROVED_WITH_RISKS = "approved_with_risks"
    REJECTED = "rejected"
    HUMAN_REVIEW_REQUIRED = "human_review_required"


class ReviewVerdict(StrEnum):
    PASS = "pass"
    CONCERN = "concern"
    FAIL = "fail"


class StakeholderType(StrEnum):
    FINANCE = "finance"
    GROWTH = "growth"
    PRODUCT = "product"
    UX = "ux"
    TECHNICAL = "technical"
    ARCHITECTURE = "architecture"
    DOCTRINE = "doctrine"
    CREATIVE = "creative"
    BONES = "bones"
    # Outsider / market lenses (limited context; not repo truth)
    INVESTOR = "investor"
    MARKETER = "marketer"
    CUSTOMER_PROXY = "customer_proxy"


class ObjectionCategory(StrEnum):
    MONETIZATION = "monetization"
    NOVELTY = "novelty"
    DISTRIBUTION = "distribution"
    SCOPE = "scope"
    DOCTRINE_ALIGNMENT = "doctrine_alignment"
    TECHNICAL_FEASIBILITY = "technical_feasibility"
    ARCHITECTURE_RISK = "architecture_risk"
    IMPLEMENTATION_RISK = "implementation_risk"
    USER_VALUE = "user_value"
    SEQUENCING = "sequencing"
    COST = "cost"
    AMBIGUITY = "ambiguity"


class GeneratedBy(StrEnum):
    DETERMINISTIC = "deterministic"
    LLM = "llm"
    HYBRID = "hybrid"


@dataclass
class ArtifactRefinementSession:
    session_id: str
    artifact_type: ArtifactType
    source_id: str
    product_id: str | None
    current_round: int
    max_rounds: int
    status: SessionStatus
    required_stakeholders: list[StakeholderType]
    optional_stakeholders: list[StakeholderType]
    created_at_utc: str
    updated_at_utc: str
    schema: str = "argus.refinement_session.v1"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RefinementSessionSnap:
    """Lightweight read model from ``runs/refinement/index.json`` + ``<session_id>/session.json`` (orchestration/eligibility)."""

    session_id: str
    artifact_type: str
    product_id: str | None
    status: str
    current_round: int
    max_rounds: int
    updated_at_utc: str


@dataclass
class ArtifactDraft:
    draft_id: str
    session_id: str
    round_number: int
    artifact_type: ArtifactType
    title: str
    content: str
    structured_fields: dict[str, Any]
    created_at_utc: str
    generated_by: GeneratedBy
    schema: str = "argus.artifact_draft.v1"


@dataclass
class StakeholderReview:
    review_id: str
    session_id: str
    draft_id: str
    round_number: int
    stakeholder_type: StakeholderType
    verdict: ReviewVerdict
    blocking: bool
    confidence_score: float
    objection_categories: list[ObjectionCategory]
    objections: list[str]
    suggestions: list[str]
    rationale: str
    created_at_utc: str
    schema: str = "argus.stakeholder_review.v1"
    llm_status: str | None = None
    council_mode: str | None = None
    """grounded | outsider | deterministic — drives convergence weighting."""
    backend_used: str | None = None
    """cursor | openai | deterministic — which backend produced this review."""
    review_dimensions: dict[str, float] | None = None
    """Optional scores for ReviewDimension keys (0..1)."""


@dataclass
class ReviewSynthesis:
    session_id: str
    draft_id: str
    round_number: int
    themes: list[str]
    blocking_issues: list[str]
    non_blocking_issues: list[str]
    required_changes: list[str]
    optional_improvements: list[str]
    overall_signal: str
    created_at_utc: str
    schema: str = "argus.review_synthesis.v1"
    theme_items: list[dict[str, Any]] = field(default_factory=list)
    """Structured themes with source tags (grounded vs outsider)."""


@dataclass
class ConvergenceResult:
    session_id: str
    round_number: int
    converged: bool
    final_status: SessionStatus
    pass_ratio: float
    blocking_count: int
    weighted_confidence: float
    reasons: list[str]
    schema: str = "argus.refinement_convergence.v1"
    narrative: dict[str, Any] | None = None
    """Deterministic display-only summary; omitted when absent in older artifacts."""
