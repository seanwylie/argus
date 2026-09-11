"""Recurrence risk — churn, failed experiments, failed executions, escalations."""

from __future__ import annotations

from pathlib import Path

from argus.decision.history.analyze import analyze_churn
from argus.decision.history.store import load_product_decision_history
from argus.decision_assessment.models import FactorContribution
from argus.escalation.packet import list_packets
from argus.experiments.models import EvaluationVerdict, ExperimentStatus
from argus.experiments.store import list_experiments


def score_recurrence(
    repo_root: Path,
    product_id: str,
) -> tuple[float, list[FactorContribution]]:
    factors: list[FactorContribution] = []
    rr = 0.0

    hist = load_product_decision_history(repo_root, product_id)
    if len(hist) >= 2:
        ch = analyze_churn(product_id, hist, repo_root=repo_root).churn_score
        rr += 0.45 * max(0.0, min(1.0, ch))
        if ch > 0.35:
            factors.append(
                FactorContribution(
                    "decision_churn",
                    "Oscillation / churn in decision memory.",
                    ch,
                    "increases_recurrence_risk",
                )
            )

    exps = list_experiments(repo_root, product_id=product_id)
    failed = sum(
        1
        for e in exps
        if e.last_evaluation_verdict == EvaluationVerdict.FAILED.value
        or e.status == ExperimentStatus.FAILED
    )
    if failed:
        fpen = min(1.0, failed * 0.15)
        rr += 0.25 * fpen
        factors.append(
            FactorContribution(
                "failed_experiments",
                f"{failed} failed experiment evaluation(s).",
                fpen,
                "increases_recurrence_risk",
            )
        )

    esc_n = sum(
        1
        for row in list_packets(repo_root, limit=200)
        if str(row.get("product_id", "")) == product_id
    )
    if esc_n >= 3:
        epen = min(1.0, (esc_n - 2) * 0.12)
        rr += 0.2 * epen
        factors.append(
            FactorContribution(
                "escalation_recurrence",
                f"{esc_n} escalation packet(s) indexed for this product.",
                epen,
                "increases_recurrence_risk",
            )
        )

    return max(0.0, min(1.0, rr)), factors
