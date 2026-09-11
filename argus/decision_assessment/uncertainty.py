"""Uncertainty score — missing/stale/conflicting evidence."""

from __future__ import annotations

from argus.decision_assessment.models import FactorContribution


def score_uncertainty(
    *,
    temporal_freshness_risk: float,
    missing_artifact_sources: list[str],
    advisor_disagreement_count: int,
    conflicting_findings_severity: float,
    thin_signal_count: bool,
    architecture_gap_risk: float = 0.0,
) -> tuple[float, list[FactorContribution]]:
    """Return (uncertainty 0..1, factors). Higher = more uncertain."""
    factors: list[FactorContribution] = []
    u = 0.0
    u += 0.35 * max(0.0, min(1.0, temporal_freshness_risk))
    factors.append(
        FactorContribution(
            "temporal_staleness",
            "Stale or missing operational artifacts (temporal grounding).",
            max(0.0, min(1.0, temporal_freshness_risk)),
            "increases_uncertainty",
        )
    )

    miss_w = min(1.0, len(missing_artifact_sources) * 0.14)
    u += 0.35 * miss_w
    if missing_artifact_sources:
        factors.append(
            FactorContribution(
                "missing_sources",
                f"Missing: {', '.join(missing_artifact_sources[:8])}.",
                miss_w,
                "increases_uncertainty",
            )
        )

    d = min(1.0, advisor_disagreement_count * 0.12)
    u += 0.15 * d
    if advisor_disagreement_count:
        factors.append(
            FactorContribution(
                "advisor_conflict",
                "Cross-advisor stance spread.",
                d,
                "increases_uncertainty",
            )
        )

    u += 0.1 * max(0.0, min(1.0, conflicting_findings_severity))
    if conflicting_findings_severity > 0:
        factors.append(
            FactorContribution(
                "finding_tension",
                "High-severity findings present (cost/reliability tension).",
                conflicting_findings_severity,
                "increases_uncertainty",
            )
        )

    if thin_signal_count:
        u += 0.08
        factors.append(
            FactorContribution(
                "thin_signals",
                "Very few signal records in latest bundle.",
                0.08,
                "increases_uncertainty",
            )
        )

    agr = max(0.0, min(1.0, architecture_gap_risk))
    if agr > 0:
        u += 0.2 * agr
        factors.append(
            FactorContribution(
                "architecture_gaps",
                "Capability gaps or stubs relevant to the assessment scope.",
                agr,
                "increases_uncertainty",
            )
        )

    return max(0.0, min(1.0, u)), factors
