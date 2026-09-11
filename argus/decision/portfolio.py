"""Cross-product ranked view of recommended next actions."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from argus.core.models.decision import DecisionCandidate
from argus.decision.engine import generate_decisions
from argus.findings.persistence import load_latest_findings
from argus.lifecycle.model import LifecycleAssessment
from argus.products.inventory import ProductInventory, build_inventory


@dataclass(frozen=True)
class PortfolioRow:
    """One ranked row: top recommendation for a product."""

    rank: int
    product_id: str
    lifecycle_stage: str
    top_intent: str
    priority_score: float
    summary: str
    assessment: LifecycleAssessment
    freshness_warnings: tuple[str, ...] = ()


def rank_portfolio_from_decisions(
    inv: ProductInventory,
    decisions_by_product: dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]],
) -> tuple[list[PortfolioRow], dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]]]:
    """
    Rank products by top candidate score from precomputed decision outputs (same pass as refresh).

    Returns ranked rows and the same ``decisions_by_product`` map (for portfolio persistence).
    """
    scratch: list[tuple[float, str, str, str, str, LifecycleAssessment, tuple[str, ...]]] = []

    for pid, (a, cands) in decisions_by_product.items():
        rec = inv.valid.get(pid)
        if rec is None:
            continue
        if not cands:
            continue
        top = cands[0]
        intent = (top.metadata or {}).get("intent", "unknown")
        md = top.metadata or {}
        fw = md.get("freshness_warnings") or []
        if isinstance(fw, list):
            fw_t = tuple(str(x) for x in fw if str(x).strip())
        else:
            fw_t = ()
        scratch.append(
            (
                float(top.priority_score or 0),
                pid,
                rec.node.lifecycle.stage.value,
                str(intent),
                top.summary,
                a,
                fw_t,
            )
        )

    scratch.sort(key=lambda t: t[0], reverse=True)
    ranked: list[PortfolioRow] = []
    for i, (score, pid, stage, intent, summary, a, fw) in enumerate(scratch):
        ranked.append(
            PortfolioRow(
                rank=i + 1,
                product_id=pid,
                lifecycle_stage=stage,
                top_intent=intent,
                priority_score=score,
                summary=summary,
                assessment=a,
                freshness_warnings=fw,
            )
        )

    return ranked, decisions_by_product


def build_portfolio(
    repo_root: Path,
    inv: ProductInventory,
) -> tuple[list[PortfolioRow], dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]]]:
    """
    For each valid product with findings on disk, generate decisions.

    Returns ranked rows (by top candidate score) and full per-product detail.
    """
    per_product: dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]] = {}

    for pid, rec in inv.valid.items():
        bundle = load_latest_findings(repo_root, pid)
        if bundle is None:
            continue
        a, cands = generate_decisions(rec.node, bundle.findings, repo_root=repo_root)
        per_product[pid] = (a, cands)

    return rank_portfolio_from_decisions(inv, per_product)


def build_portfolio_from_inventory(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> tuple[list[PortfolioRow], dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]]]:
    inv = build_inventory(repo_root, products_dir=products_dir)
    return build_portfolio(repo_root, inv)
