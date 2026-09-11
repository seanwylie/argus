"""Momentum — improving trends, positive experiment signals, recommendation stability."""

from __future__ import annotations

from pathlib import Path

from argus.decision.history.store import load_product_decision_history
from argus.decision_assessment.models import FactorContribution
from argus.experiments.models import EvaluationVerdict
from argus.experiments.store import list_experiments
from argus.trends.analyze import analyze_product


def score_momentum(repo_root: Path, product_id: str) -> tuple[float, list[FactorContribution]]:
    factors: list[FactorContribution] = []
    m = 0.35  # neutral baseline

    tr = analyze_product(repo_root, product_id)
    pos_flags = ("improving", "traction", "growth")
    neg_flags = ("risk", "abandon", "thrash", "stagnat", "drift")
    flags = [f.lower() for f in (tr.trend_flags or [])]
    hit_pos = sum(1 for x in flags if any(p in x for p in pos_flags))
    hit_neg = sum(1 for x in flags if any(n in x for n in neg_flags))
    if hit_pos:
        m += 0.2 * min(1.0, hit_pos * 0.35)
        factors.append(
            FactorContribution(
                "trend_positive",
                "Positive trend/drift flags from snapshot history.",
                hit_pos * 0.2,
                "increases_momentum",
            )
        )
    if hit_neg:
        m -= 0.18 * min(1.0, hit_neg * 0.35)
        factors.append(
            FactorContribution(
                "trend_negative",
                "Risk/drift trend flags.",
                hit_neg * 0.18,
                "decreases_momentum",
            )
        )

    exps = list_experiments(repo_root, product_id=product_id)
    wins = sum(1 for e in exps if e.last_evaluation_verdict == EvaluationVerdict.SUCCESS.value)
    if wins:
        m += min(0.15, wins * 0.05)
        factors.append(
            FactorContribution(
                "experiment_wins",
                f"{wins} experiment(s) with success verdict.",
                min(0.15, wins * 0.05),
                "increases_momentum",
            )
        )

    hist = load_product_decision_history(repo_root, product_id)
    if len(hist) >= 3:
        stable = sum(
            1
            for i in range(1, len(hist))
            if hist[i].top_recommended_action == hist[i - 1].top_recommended_action
        )
        stab_ratio = stable / max(1, len(hist) - 1)
        m += 0.12 * stab_ratio
        factors.append(
            FactorContribution(
                "recommendation_stability",
                "Consecutive runs with same top recommendation text.",
                stab_ratio * 0.12,
                "increases_momentum",
            )
        )

    return max(0.0, min(1.0, m)), factors
