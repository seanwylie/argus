"""Structured experiments per product (hypothesis, metrics, lifecycle)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ExperimentType(StrEnum):
    GROWTH = "growth"
    COST_REDUCTION = "cost_reduction"
    ENGAGEMENT = "engagement"
    CONTENT = "content"
    INFRASTRUCTURE = "infrastructure"


class ExperimentStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"


class EvaluationVerdict(StrEnum):
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"


@dataclass
class ExperimentEvaluation:
    """Result of deterministic experiment evaluation (signals + history + trends)."""

    experiment_id: str
    product_id: str
    verdict: EvaluationVerdict
    composite_score: float
    summary: str
    evaluated_at_utc: str
    metrics_before: dict[str, object] = field(default_factory=dict)
    metrics_after: dict[str, object] = field(default_factory=dict)
    deltas: dict[str, object] = field(default_factory=dict)
    trend_flags: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    schema: str = "argus.experiment_evaluation.v1"


@dataclass
class ExperimentProposal:
    """Structured suggestion only (not persisted as an Experiment until created)."""

    proposal_id: str
    product_id: str
    hypothesis: str
    type: ExperimentType
    description: str
    expected_outcome: str
    success_metrics: list[str] = field(default_factory=list)
    estimated_effort: str = "small"
    confidence: float = 0.55
    rationale: str = ""
    schema: str = "argus.experiment_proposal.v1"

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if isinstance(self.type, str):
            self.type = ExperimentType(self.type)


@dataclass
class ProposalRun:
    """Batch of proposals for one invocation (one or many products)."""

    generated_at_utc: str
    repo_root: str
    proposals: list[ExperimentProposal] = field(default_factory=list)
    schema: str = "argus.experiment_proposals_run.v1"


@dataclass
class PrioritizedProposal:
    """One proposed experiment with deterministic priority score and subcomponents (0–1)."""

    rank: int
    score: float
    impact: float
    urgency: float
    confidence: float
    lifecycle_fit: float
    cost_penalty: float
    effort_penalty: float
    strategy_alignment_raw: float
    experiment_multiplier: float
    proposal: ExperimentProposal
    schema: str = "argus.prioritized_experiment_proposal.v1"


@dataclass
class PrioritizationRun:
    """Ranked proposals per product plus portfolio-wide top picks."""

    generated_at_utc: str
    repo_root: str
    strategy_mode: str | None
    by_product: dict[str, list[PrioritizedProposal]]
    top_recommendations: list[PrioritizedProposal]
    decision_context_by_product: dict[str, dict[str, Any]] = field(default_factory=dict)
    schema: str = "argus.experiment_prioritization_run.v2"


@dataclass
class Experiment:
    """An experiment tied to one product."""

    id: str
    product_id: str
    hypothesis: str
    type: ExperimentType
    description: str
    expected_outcome: str
    success_metrics: list[str] = field(default_factory=list)
    start_at: str = ""
    end_at: str | None = None
    status: ExperimentStatus = ExperimentStatus.PROPOSED
    confidence: float = 0.5
    created_at: str = ""
    schema: str = "argus.experiment.v1"
    last_evaluation_verdict: str | None = None
    last_evaluation_at: str | None = None
    last_evaluation_summary: str | None = None
    #: Correlates with execution JSON (`action_id`); optional.
    last_execution_action_id: str | None = None
    #: Last execution `run_id` applied to this experiment (from `runs/execution/.../*.json`).
    last_execution_run_id: str | None = None
    last_execution_at_utc: str | None = None
    last_execution_success: bool | None = None
    #: Consecutive failed execution records targeting this experiment (reset on success).
    execution_failure_streak: int = 0
    #: When set, experiment was materialized from this proposal id (orchestration / deterministic create).
    source_proposal_id: str | None = None

    def __post_init__(self) -> None:
        self.confidence = max(0.0, min(1.0, float(self.confidence)))
        if isinstance(self.type, str):
            self.type = ExperimentType(self.type)
        if isinstance(self.status, str):
            self.status = ExperimentStatus(self.status)
        self.execution_failure_streak = max(0, int(self.execution_failure_streak))
