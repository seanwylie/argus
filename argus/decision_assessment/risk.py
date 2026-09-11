"""Risk of acting — cost posture, kill candidate, lifecycle, doctrine tension (heuristic)."""

from __future__ import annotations

from argus.core.models.enums import LifecycleStage
from argus.decision_assessment.models import FactorContribution


def score_risk(
    *,
    kill_candidate: bool,
    monthly_cost_usd: float | None,
    max_monthly_cost_usd: float | None,
    lifecycle_stage: LifecycleStage,
    high_severity_finding_count: int,
) -> tuple[float, list[FactorContribution]]:
    factors: list[FactorContribution] = []
    r = 0.15
    if kill_candidate:
        r += 0.35
        factors.append(
            FactorContribution(
                "kill_candidate",
                "Lifecycle assessment flagged kill_candidate.",
                0.35,
                "increases_risk",
            )
        )

    over = 0.0
    if monthly_cost_usd is not None and max_monthly_cost_usd is not None:
        if float(monthly_cost_usd) > float(max_monthly_cost_usd):
            over = min(1.0, (float(monthly_cost_usd) - float(max_monthly_cost_usd)) / max(float(max_monthly_cost_usd), 1.0))
            r += 0.15 * over
            factors.append(
                FactorContribution(
                    "over_budget",
                    "Declared monthly cost exceeds product cap.",
                    over,
                    "increases_risk",
                )
            )

    # Early stage = more execution risk
    if lifecycle_stage in (LifecycleStage.IDEA, LifecycleStage.BUILD, LifecycleStage.VALIDATE):
        r += 0.12
        factors.append(
            FactorContribution(
                "early_stage",
                f"Lifecycle {lifecycle_stage.value} implies higher execution uncertainty.",
                0.12,
                "increases_risk",
            )
        )

    if high_severity_finding_count > 0:
        hf = min(1.0, high_severity_finding_count * 0.1)
        r += 0.2 * hf
        factors.append(
            FactorContribution(
                "severity_findings",
                f"{high_severity_finding_count} high/critical findings.",
                hf,
                "increases_risk",
            )
        )

    return max(0.0, min(1.0, r)), factors
