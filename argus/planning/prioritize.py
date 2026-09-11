"""Deterministic prioritization heuristics for weekly planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProductSignals:
    """Normalized inputs for one product."""

    product_id: str
    rank: int
    priority_score: float
    top_intent: str
    summary: str
    lifecycle_stage: str
    kill_candidate: bool
    findings_count: int
    escalation_count: int
    monthly_cost_usd: float | None
    over_budget: bool
    churn_score: float | None
    has_findings_artifact: bool


def risk_score(s: ProductSignals) -> float:
    """Higher = more urgent risk attention (deterministic)."""
    r = 0.0
    if s.kill_candidate:
        r += 100.0
    r += float(s.findings_count) * 4.0
    r += float(s.escalation_count) * 12.0
    if s.over_budget:
        r += 25.0
    if s.churn_score is not None:
        r += float(s.churn_score) * 30.0
    # elevate deprecate-ish intent
    ti = (s.top_intent or "").lower()
    if any(x in ti for x in ("deprecat", "kill", "sunset")):
        r += 15.0
    return r


def pick_focus_top3(products: list[ProductSignals]) -> list[str]:
    """Focus = highest portfolio priority scores (rank order)."""
    if not products:
        return []
    ordered = sorted(
        products,
        key=lambda p: (-p.priority_score, p.product_id),
    )
    n = min(3, len(ordered))
    return [p.product_id for p in ordered[:n]]


def pick_risk_top3(products: list[ProductSignals]) -> list[str]:
    """Top 3 by composite risk (may overlap focus)."""
    if not products:
        return []
    ordered = sorted(
        products,
        key=lambda p: (-risk_score(p), p.product_id),
    )
    n = min(3, len(ordered))
    out: list[str] = []
    for p in ordered[:n]:
        if p.product_id not in out:
            out.append(p.product_id)
    return out


def pick_watchlist(products: list[ProductSignals]) -> list[str]:
    """
    Leave alone: lowest portfolio pressure among quieter products.

    Picks lowest ``priority_score`` among products with no escalations,
    not kill-candidate, and few findings.
    """
    candidates = [
        p
        for p in products
        if p.escalation_count == 0 and not p.kill_candidate and p.findings_count <= 2
    ]
    if not candidates:
        return []
    ordered = sorted(
        candidates,
        key=lambda p: (p.priority_score, p.rank, p.product_id),
    )
    return [p.product_id for p in ordered[:5]]


def pick_hold(products: list[ProductSignals], focus: set[str], risks: set[str]) -> list[str]:
    """Stable products not in focus/risk top sets."""
    out: list[str] = []
    for p in products:
        if p.product_id in focus or p.product_id in risks:
            continue
        if p.escalation_count > 0 or p.kill_candidate:
            continue
        if p.findings_count > 4:
            continue
        if p.churn_score is not None and p.churn_score > 0.45:
            continue
        out.append(p.product_id)
    out.sort()
    return out[:12]


def pick_deprecate_review(products: list[ProductSignals]) -> list[str]:
    out: list[str] = []
    for p in products:
        if p.kill_candidate:
            out.append(p.product_id)
            continue
        ti = (p.top_intent or "").lower()
        sm = (p.summary or "").lower()
        if any(x in ti + " " + sm for x in ("deprecat", "sunset", "kill", "abandon")):
            out.append(p.product_id)
    # dedupe preserve order
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return sorted(deduped)


def pick_incubate(products: list[ProductSignals]) -> list[str]:
    """Early stages, low noise, not scheduled for deprecate review."""
    dep = set(pick_deprecate_review(products))
    out: list[str] = []
    for p in products:
        if p.product_id in dep:
            continue
        if p.lifecycle_stage not in ("idea", "build", "validate"):
            continue
        if p.findings_count > 4 or p.escalation_count > 0:
            continue
        if p.kill_candidate:
            continue
        out.append(p.product_id)
    out.sort()
    return out[:8]
