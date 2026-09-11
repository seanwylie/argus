"""Confidence score from evidence density, freshness, advisor alignment, decision stability."""

from __future__ import annotations

from argus.decision_assessment.models import FactorContribution


def score_confidence(
    *,
    evidence_density: float,
    temporal_freshness_risk: float,
    advisor_consensus_confidence: float | None,
    advisor_disagreement_count: int,
    decision_churn_score: float | None,
    has_decisions_artifact: bool,
    has_findings_artifact: bool,
    has_signals_artifact: bool,
    architecture_stub_risk: float = 0.0,
) -> tuple[float, list[FactorContribution]]:
    """
    Return (confidence_score 0..1, confidence_factors, notes as factors with direction).

    Higher confidence when evidence is dense and fresh, advisors agree, churn is low.
    """
    factors: list[FactorContribution] = []
    # Base from evidence density (0..1)
    base = max(0.0, min(1.0, evidence_density))
    factors.append(
        FactorContribution(
            "evidence_density",
            "Breadth of recent artifacts (signals, findings, decisions, trends, experiments).",
            base,
            "increases_confidence",
        )
    )
    # Penalize stale temporal context (1 = worst)
    fresh_boost = 1.0 - 0.55 * max(0.0, min(1.0, temporal_freshness_risk))
    factors.append(
        FactorContribution(
            "temporal_freshness",
            "Advisor/temporal artifact freshness risk (lower is better).",
            max(0.0, min(1.0, temporal_freshness_risk)),
            "decreases_confidence",
        )
    )

    adv = 0.55 if advisor_consensus_confidence is None else max(0.0, min(1.0, advisor_consensus_confidence))
    if advisor_consensus_confidence is not None:
        factors.append(
            FactorContribution(
                "advisor_consensus",
                "Consensus confidence from last advisor run when present.",
                adv,
                "increases_confidence",
            )
        )
    else:
        factors.append(
            FactorContribution(
                "advisor_missing",
                "No runs/advisors/<id>.latest.json — advisor evidence absent.",
                0.35,
                "decreases_confidence",
            )
        )

    disagree_penalty = min(1.0, advisor_disagreement_count * 0.08)
    adv_adj = adv * (1.0 - disagree_penalty)
    if advisor_disagreement_count:
        factors.append(
            FactorContribution(
                "advisor_disagreement",
                f"{advisor_disagreement_count} advisor stance(s) far from mean.",
                disagree_penalty,
                "decreases_confidence",
            )
        )

    churn = decision_churn_score if decision_churn_score is not None else 0.0
    churn_penalty = 1.0 - 0.4 * max(0.0, min(1.0, churn))
    if decision_churn_score is not None and churn > 0.2:
        factors.append(
            FactorContribution(
                "decision_churn",
                "Historical churn in top recommendations.",
                churn,
                "decreases_confidence",
            )
        )

    asr = max(0.0, min(1.0, architecture_stub_risk))
    stub_blend = 1.0 - 0.4 * asr
    if asr > 0:
        factors.append(
            FactorContribution(
                "architecture_stub_risk",
                "Relevant subsystem stubbed or incomplete (see stub_awareness / capabilities).",
                asr,
                "decreases_confidence",
            )
        )

    # Blend
    conf = (
        0.28 * base
        + 0.22 * fresh_boost
        + 0.28 * adv_adj
        + 0.12 * churn_penalty
        + 0.05 * (1.0 if has_signals_artifact else 0.0)
        + 0.05 * (1.0 if has_findings_artifact else 0.0)
    )
    conf *= stub_blend
    if not has_decisions_artifact:
        conf *= 0.88
        factors.append(
            FactorContribution(
                "decisions_missing",
                "No saved decisions bundle for this product.",
                0.12,
                "decreases_confidence",
            )
        )

    conf = max(0.0, min(1.0, conf))
    return conf, factors
