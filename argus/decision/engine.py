"""Orchestrate lifecycle assessment + decision candidate generation."""

from __future__ import annotations

from pathlib import Path

from argus.core.models.decision import DecisionCandidate
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.decision.from_findings import build_candidates
from argus.lifecycle.model import LifecycleAssessment
from argus.lifecycle.scoring import assess_lifecycle
from argus.strategy.apply import get_strategy_profile


def generate_decisions(
    product: ProductNode,
    findings: list[Finding],
    *,
    repo_root: Path | None = None,
) -> tuple[LifecycleAssessment, list[DecisionCandidate]]:
    """Assess lifecycle, build ranked decision candidates."""
    # Local import avoids circular package init (decision ↔ input).
    from argus.input.apply import apply_to_assessment, apply_to_candidates, apply_to_findings

    profile = get_strategy_profile(repo_root)
    findings_use = (
        apply_to_findings(repo_root, product.id, findings) if repo_root is not None else findings
    )
    assessment = assess_lifecycle(
        product,
        findings_use,
        kill_score_min=profile.kill_score_min,
        move_forward_max=profile.move_forward_max,
    )
    if repo_root is not None:
        assessment = apply_to_assessment(
            repo_root,
            product.id,
            assessment,
            kill_thresholds=(profile.kill_score_min, profile.move_forward_max),
        )
    candidates = build_candidates(
        product,
        findings_use,
        assessment,
        strategy_profile=profile,
        repo_root=repo_root,
    )
    if repo_root is not None:
        from argus.doctrine.load import load_doctrine_for_product
        from argus.doctrine.scoring import apply_doctrine_to_decision_candidates

        doc, _ = load_doctrine_for_product(
            repo_root, product.id, product_root=product.product_root
        )
        candidates = apply_doctrine_to_decision_candidates(candidates, doc)
    if repo_root is not None:
        candidates = apply_to_candidates(repo_root, product.id, candidates)
    return assessment, candidates
