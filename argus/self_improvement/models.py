"""Data models for Argus self-improvement (analysis / planning only; no auto-execution)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class SelfFindingKind(StrEnum):
    """Category of weakness detected in how Argus is operated or implemented."""

    MISSING_CAPABILITY = "missing_capability"
    WEAK_TEST_COVERAGE = "weak_test_coverage_area"
    REPEATED_ESCALATION_PATTERN = "repeated_escalation_pattern"
    DECISION_CHURN_ARGUS = "decision_churn_argus"
    MISSING_SCHEMA_VALIDATION = "missing_schema_validation"
    MISSING_INTEGRATION_PATH = "missing_integration_path"
    POOR_EXECUTION_FEEDBACK = "poor_execution_feedback_loop"
    OPERATOR_FRICTION = "excessive_operator_friction"


class SelfProposalKind(StrEnum):
    """Recommended class of improvement (planning artifact only)."""

    ADD_ADAPTER = "add_new_adapter"
    STRENGTHEN_ARTIFACT_VALIDATION = "strengthen_artifact_validation"
    IMPROVE_APPROVAL_FLOW = "improve_approval_flow"
    IMPROVE_EXPERIMENT_EVALUATION = "improve_experiment_evaluation"
    ADD_DOCS = "add_missing_docs"
    IMPROVE_RESUME_SEMANTICS = "improve_resume_semantics"
    ADD_LOOP_REGRESSION_HARNESS = "add_loop_regression_harness"
    REDUCE_ESCALATION_NOISE = "reduce_escalation_noise"
    PLATFORM_OTHER = "platform_other"


class SelfChangeRisk(StrEnum):
    """Whether a proposal implies risky changes to Argus itself."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class SelfImprovementFinding:
    id: str
    kind: SelfFindingKind
    title: str
    summary: str
    evidence: dict[str, Any] = field(default_factory=dict)
    suggested_severity: str = "medium"  # info|low|medium|high


@dataclass
class ProposalScores:
    """0.0–1.0 rubric (higher = stronger signal in that dimension)."""

    ecosystem_leverage: float = 0.0
    safety_impact: float = 0.0
    implementation_effort: float = 0.5  # higher = more effort (penalized in total)
    recurrence_of_pain: float = 0.0
    strategy_alignment: float = 0.0


@dataclass
class SelfImprovementProposal:
    id: str
    kind: SelfProposalKind
    title: str
    summary: str
    source_finding_ids: list[str] = field(default_factory=list)
    scores: ProposalScores = field(default_factory=ProposalScores)
    risk: SelfChangeRisk = SelfChangeRisk.MEDIUM
    requires_operator_approval: bool = False
    rationale: str = ""


@dataclass
class RankedProposal:
    proposal: SelfImprovementProposal
    total_score: float


@dataclass
class SelfImprovementPlan:
    """Weekly-style bundle for operators and downstream consumers (dashboard, advisors)."""

    generated_at_utc: str
    repo_root: str
    schema: str = "argus.self_improvement_plan.v1"
    findings: list[SelfImprovementFinding] = field(default_factory=list)
    proposals: list[SelfImprovementProposal] = field(default_factory=list)
    ranked: list[RankedProposal] = field(default_factory=list)
    top_this_week: list[str] = field(default_factory=list)  # proposal ids
    defer: list[str] = field(default_factory=list)
    high_risk_require_approval: list[str] = field(default_factory=list)  # proposal ids
    strategy_mode: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "generated_at_utc": self.generated_at_utc,
            "repo_root": self.repo_root,
            "strategy_mode": self.strategy_mode,
            "findings": [finding_to_jsonable(f) for f in self.findings],
            "proposals": [proposal_to_jsonable(p) for p in self.proposals],
            "ranked": [
                {"proposal_id": r.proposal.id, "total_score": round(r.total_score, 4)}
                for r in self.ranked
            ],
            "top_this_week": list(self.top_this_week),
            "defer": list(self.defer),
            "high_risk_require_approval": list(self.high_risk_require_approval),
            "notes": list(self.notes),
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def finding_to_jsonable(f: SelfImprovementFinding) -> dict[str, Any]:
    return {
        "id": f.id,
        "kind": f.kind.value,
        "title": f.title,
        "summary": f.summary,
        "evidence": f.evidence,
        "suggested_severity": f.suggested_severity,
    }


def proposal_to_jsonable(p: SelfImprovementProposal) -> dict[str, Any]:
    s = p.scores
    return {
        "id": p.id,
        "kind": p.kind.value,
        "title": p.title,
        "summary": p.summary,
        "source_finding_ids": list(p.source_finding_ids),
        "scores": {
            "ecosystem_leverage": s.ecosystem_leverage,
            "safety_impact": s.safety_impact,
            "implementation_effort": s.implementation_effort,
            "recurrence_of_pain": s.recurrence_of_pain,
            "strategy_alignment": s.strategy_alignment,
        },
        "risk": p.risk.value,
        "requires_operator_approval": p.requires_operator_approval,
        "rationale": p.rationale,
    }


def new_finding_id(prefix: str = "sif") -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def new_proposal_id(prefix: str = "sip") -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:12]}"
