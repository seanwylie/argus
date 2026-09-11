"""Friction — capability gaps, approvals, autonomy/policy bottlenecks."""

from __future__ import annotations

from pathlib import Path

from argus.approval.models import ApprovalStatus
from argus.approval.store import list_records
from argus.capabilities.evaluate import evaluate_capabilities
from argus.decision_assessment.models import FactorContribution


def score_friction(repo_root: Path, product_id: str) -> tuple[float, list[FactorContribution]]:
    factors: list[FactorContribution] = []
    fscore = 0.0

    ev = evaluate_capabilities(repo_root)
    gap_n = len(ev.missing_capabilities or [])
    if gap_n:
        g = min(1.0, gap_n * 0.08)
        fscore += 0.45 * g
        factors.append(
            FactorContribution(
                "capability_gaps",
                f"{gap_n} missing/partial capabilities in registry evaluation.",
                g,
                "increases_friction",
            )
        )

    try:
        pending = [
            r
            for r in list_records(repo_root)
            if r.status == ApprovalStatus.PENDING and r.product_id == product_id
        ]
        if len(pending) >= 2:
            p = min(1.0, len(pending) * 0.12)
            fscore += 0.35 * p
            factors.append(
                FactorContribution(
                    "pending_approvals",
                    f"{len(pending)} pending approval record(s) for this product.",
                    p,
                    "increases_friction",
                )
            )
    except OSError:
        pass

    return max(0.0, min(1.0, fscore)), factors
