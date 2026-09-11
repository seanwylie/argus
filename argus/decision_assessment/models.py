"""
Structured decision quality under uncertainty (not emotion modeling).

Scores are deterministic, inspectable, and intended for operators and policy hooks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any


class ConfidenceBucket(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EscalationRecommendation(StrEnum):
    PROCEED = "proceed"
    GATHER_DATA = "gather_data"
    DELAY_REVIEW = "delay_review"
    ESCALATE_HUMAN = "escalate_human"


@dataclass
class FactorContribution:
    """One explainable contributor to a composite score."""

    factor_id: str
    label: str
    """Human-readable description."""
    contribution: float
    """Signed or unsigned contribution in score units (0–1 scale for magnitudes)."""
    direction: str
    """e.g. 'increases_confidence', 'increases_uncertainty', 'increases_risk'."""


@dataclass
class DecisionContextAssessment:
    """Canonical assessment for a product decision context (evidence + dynamics, no feelings)."""

    product_id: str
    assessed_at_utc: str
    decision_id: str | None = None
    #: 0 = no trust in evidence quality, 1 = strong support
    confidence_score: float = 0.0
    confidence_bucket: ConfidenceBucket = ConfidenceBucket.MEDIUM
    #: 0 = fully known state, 1 = highly uncertain
    uncertainty_score: float = 0.0
    uncertainty_factors: list[FactorContribution] = field(default_factory=list)
    #: Composite risk of acting given current picture (not P&L VaR)
    risk_score: float = 0.0
    risk_factors: list[FactorContribution] = field(default_factory=list)
    recurrence_risk_score: float = 0.0
    momentum_score: float = 0.0
    friction_score: float = 0.0
    #: 0 = no need to involve operator, 1 = strong pressure to escalate
    escalation_pressure: float = 0.0
    escalation_recommendation: EscalationRecommendation = EscalationRecommendation.GATHER_DATA
    evidence_density_score: float = 0.0
    exploratory_action_recommended: bool = False
    exploratory_action_reason: str = ""
    exploratory_guardrails: list[str] = field(default_factory=list)
    rationale: str = ""
    confidence_factors: list[FactorContribution] = field(default_factory=list)
    momentum_factors: list[FactorContribution] = field(default_factory=list)
    friction_factors: list[FactorContribution] = field(default_factory=list)
    recurrence_factors: list[FactorContribution] = field(default_factory=list)
    #: Optional 0–1 from advisor council / consensus snapshot (interpretation, not authority).
    advisor_alignment_score: float | None = None
    advisor_conflict_flag: bool = False
    advisor_summary: str = ""
    schema: str = "argus.decision_context_assessment.v1"

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "product_id": self.product_id,
            "decision_id": self.decision_id,
            "assessed_at_utc": self.assessed_at_utc,
            "confidence_score": round(self.confidence_score, 4),
            "confidence_bucket": self.confidence_bucket.value,
            "uncertainty_score": round(self.uncertainty_score, 4),
            "uncertainty_factors": [_fc(x) for x in self.uncertainty_factors],
            "risk_score": round(self.risk_score, 4),
            "risk_factors": [_fc(x) for x in self.risk_factors],
            "recurrence_risk_score": round(self.recurrence_risk_score, 4),
            "recurrence_factors": [_fc(x) for x in self.recurrence_factors],
            "momentum_score": round(self.momentum_score, 4),
            "momentum_factors": [_fc(x) for x in self.momentum_factors],
            "friction_score": round(self.friction_score, 4),
            "friction_factors": [_fc(x) for x in self.friction_factors],
            "escalation_pressure": round(self.escalation_pressure, 4),
            "escalation_recommendation": self.escalation_recommendation.value,
            "evidence_density_score": round(self.evidence_density_score, 4),
            "exploratory_action_recommended": self.exploratory_action_recommended,
            "exploratory_action_reason": self.exploratory_action_reason,
            "exploratory_guardrails": list(self.exploratory_guardrails),
            "rationale": self.rationale,
            "confidence_factors": [_fc(x) for x in self.confidence_factors],
            "advisor_alignment_score": None
            if self.advisor_alignment_score is None
            else round(self.advisor_alignment_score, 4),
            "advisor_conflict_flag": self.advisor_conflict_flag,
            "advisor_summary": self.advisor_summary,
        }


def _fc(f: FactorContribution) -> dict[str, Any]:
    return {
        "factor_id": f.factor_id,
        "label": f.label,
        "contribution": round(f.contribution, 4),
        "direction": f.direction,
    }


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
