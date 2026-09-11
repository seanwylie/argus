"""Synthetic escalation pressure and coarse recommendation."""

from __future__ import annotations

from argus.decision_assessment.models import EscalationRecommendation, FactorContribution


def score_escalation_pressure(
    *,
    uncertainty_score: float,
    recurrence_risk_score: float,
    friction_score: float,
    confidence_score: float,
    risk_score: float,
) -> tuple[float, EscalationRecommendation, list[FactorContribution]]:
    """
    Combine dynamics into escalation pressure 0..1 and a coarse recommendation.

    Deterministic — tuned for operator visibility, not ML.
    """
    factors: list[FactorContribution] = []
    # High uncertainty + recurrence + low confidence drives pressure
    raw = (
        0.32 * uncertainty_score
        + 0.22 * recurrence_risk_score
        + 0.18 * friction_score
        + 0.15 * (1.0 - confidence_score)
        + 0.13 * risk_score
    )
    p = max(0.0, min(1.0, raw))

    if p >= 0.82:
        rec = EscalationRecommendation.ESCALATE_HUMAN
        factors.append(
            FactorContribution(
                "pressure_high",
                "Combined uncertainty, recurrence, and friction suggest operator review.",
                p,
                "increases_escalation",
            )
        )
    elif p >= 0.58:
        rec = EscalationRecommendation.GATHER_DATA
    elif p >= 0.38:
        rec = EscalationRecommendation.DELAY_REVIEW
    else:
        rec = EscalationRecommendation.PROCEED

    return p, rec, factors
