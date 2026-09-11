"""Exploratory / leap-of-faith logic — bounded, explicit, not 'low confidence YOLO'."""

from __future__ import annotations

from argus.core.models.enums import LifecycleStage
from argus.decision_assessment.models import FactorContribution
from argus.strategy.modes import StrategyMode


def evaluate_exploratory_leap(
    *,
    strategy_mode: StrategyMode | None,
    confidence_score: float,
    uncertainty_score: float,
    risk_score: float,
    evidence_density_score: float,
    monthly_cost_usd: float | None,
    max_monthly_cost_usd: float | None,
    lifecycle_stage: LifecycleStage,
) -> tuple[bool, str, list[str], list[FactorContribution]]:
    """
    Return (recommended, reason, guardrails, factors).

    Exploratory action is allowed only when exploration mode, early stage,
    bounded cost, risk/uncertainty within guardrails, and non-trivial upside path.
    """
    factors: list[FactorContribution] = []
    guardrails: list[str] = []

    if strategy_mode != StrategyMode.EXPLORATION:
        return (
            False,
            "Exploratory classification requires strategy mode EXPLORATION (runs/strategy/current.json).",
            [
                "Set `argus strategy set exploration` or equivalent.",
                "Do not treat low confidence as authorization to bypass policy.",
            ],
            [
                FactorContribution(
                    "mode_gate",
                    "Exploratory leaps require exploration strategy mode.",
                    1.0,
                    "blocks_exploratory",
                )
            ],
        )

    if lifecycle_stage not in (LifecycleStage.IDEA, LifecycleStage.BUILD, LifecycleStage.VALIDATE):
        return (
            False,
            "Exploratory leaps are constrained to early lifecycle stages (idea/build/validate).",
            ["Later stages require stronger evidence or explicit portfolio decisions."],
            [
                FactorContribution(
                    "lifecycle_gate",
                    "Lifecycle stage not in exploratory window.",
                    1.0,
                    "blocks_exploratory",
                )
            ],
        )

    if confidence_score > 0.62:
        return (
            False,
            "Confidence is already sufficient — prefer standard prioritized actions over 'leap' framing.",
            [],
            [
                FactorContribution(
                    "high_confidence",
                    "Exploratory leap not indicated when confidence is high.",
                    confidence_score,
                    "blocks_exploratory",
                )
            ],
        )

    if uncertainty_score < 0.28 or evidence_density_score > 0.72:
        return (
            False,
            "Evidence is either too thin to justify a bounded probe or already dense enough for normal planning.",
            [],
            factors,
        )

    if risk_score > 0.62:
        return (
            False,
            "Risk score too high for exploratory classification — reduce exposure or gather data first.",
            ["Review kill posture, cost caps, and findings before probing."],
            [
                FactorContribution(
                    "risk_gate",
                    "Elevated composite risk blocks exploratory framing.",
                    risk_score,
                    "blocks_exploratory",
                )
            ],
        )

    cost_ok = True
    if monthly_cost_usd is not None and max_monthly_cost_usd is not None:
        if float(monthly_cost_usd) > 0.5 * float(max_monthly_cost_usd):
            cost_ok = False
            factors.append(
                FactorContribution(
                    "cost_pressure",
                    "Monthly cost already consumes a large share of cap.",
                    0.4,
                    "blocks_exploratory",
                )
            )

    if not cost_ok:
        return (
            False,
            "Cost posture too tight relative to declared cap for an exploratory probe.",
            ["Lower burn or raise cap explicitly in product.yaml / economics."],
            factors,
        )

    guardrails.extend(
        [
            "Probe must be reversible and small-batch (explicit experiment record).",
            "Doctrine and autonomy policy still gate execution.",
            "Re-evaluate after one evaluation cycle; do not stack probes without signal.",
        ]
    )

    return (
        True,
        "Exploration mode + early stage + bounded uncertainty gap — justified small probe vs reckless bet.",
        guardrails,
        factors
        + [
            FactorContribution(
                "exploration_mode",
                "Strategy mode supports learning-oriented bets.",
                0.25,
                "enables_exploratory",
            ),
            FactorContribution(
                "evidence_gap",
                "Moderate evidence density with tractable uncertainty — candidate for experiment.",
                uncertainty_score,
                "enables_exploratory",
            ),
        ],
    )
