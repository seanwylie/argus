"""Decision memory and churn analysis models (read-only, local artifacts)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AlternativeCandidate:
    """Non-top candidate snapshot for audit."""

    summary: str
    intent: str
    priority_score: float | None
    confidence: float | None


@dataclass
class DecisionMemoryEntry:
    """One persisted decision generation for a product."""

    source_path: str
    generated_at_utc: str
    product_id: str
    top_recommended_action: str
    top_intent: str
    priority_score: float | None
    confidence: float | None
    alternatives: list[AlternativeCandidate]
    rationale_summary: str
    compared_to_previous: str  # "first" | "unchanged" | "changed"
    lifecycle_stage: str
    kill_candidate: bool


@dataclass
class DecisionChurnReport:
    """Churn and stability metrics for a product's decision history."""

    product_id: str
    run_count: int
    top_action_change_count: int
    max_consecutive_same_top: int
    intent_bucket_flips: int
    confidence_min: float | None
    confidence_max: float | None
    confidence_range: float | None
    churn_score: float
    stability_score: float
    summary_lines: list[str] = field(default_factory=list)
    intent_streaks: dict[str, int] = field(default_factory=dict)
    escalation_theme_hits: list[str] = field(default_factory=list)


@dataclass
class DecisionHistoryPayload:
    """CLI JSON wrapper."""

    schema: str = "argus.decision_history.v1"
    product_id: str | None = None
    entries: list[DecisionMemoryEntry] = field(default_factory=list)
    churn: DecisionChurnReport | None = None
