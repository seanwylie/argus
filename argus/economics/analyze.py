"""Derive economics from product.yaml and persisted signal bundles (no external APIs)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.serialize import to_jsonable
from argus.economics.models import (
    GrowthSignal,
    PortfolioEconomics,
    ProductEconomics,
)
from argus.economics.score import derive_economics_signals, rank_performers
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle
from argus.signals.snapshots.models import COST_SPIKE


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _growth_from_pct(pct: Any) -> GrowthSignal:
    if pct is None:
        return GrowthSignal.UNKNOWN
    try:
        v = float(pct)
    except (TypeError, ValueError):
        return GrowthSignal.UNKNOWN
    if v > 0.02:
        return GrowthSignal.UP
    if v < -0.02:
        return GrowthSignal.DOWN
    return GrowthSignal.FLAT


def _traction_profile(
    records: list[SignalRecord],
    *,
    estimated_revenue: float,
    revenue_source: str,
) -> str:
    """
    ``monetized`` when revenue is visible from signals; ``vanity_traction`` when engagement
    exists without MRR; ``unknown`` otherwise.
    """
    if estimated_revenue > 1e-6:
        return "monetized"
    rs = (revenue_source or "").lower()
    if "mrr" in rs or "stripe" in rs:
        return "monetized"
    for r in records:
        p = r.payload or {}
        if p.get("mrr_current"):
            return "monetized"
        if p.get("pageviews_current") or p.get("engagement_current"):
            return "vanity_traction"
        ad = str(p.get("adapter") or "").lower()
        if "stripe" in ad and p.get("estimated_revenue"):
            return "monetized"
    return "unknown"


def extract_from_signals(records: list[SignalRecord]) -> dict[str, Any]:
    """
    Scan normalized signal records for local economics fields.

    Prefers Stripe ``mrr_current`` for revenue; AWS ``cost_current`` for observed cost;
    growth from ``pct_change`` on revenue-like payloads when adapter hints at MRR.
    """
    out: dict[str, Any] = {
        "estimated_revenue": 0.0,
        "observed_monthly_cost": None,
        "growth_signal": GrowthSignal.UNKNOWN,
        "revenue_source": "",
        "cost_spike": False,
    }
    best_mrr = 0.0
    mrr_growth: GrowthSignal = GrowthSignal.UNKNOWN
    observed_cost: float | None = None

    for r in records:
        p = r.payload or {}
        src = str(p.get("adapter") or r.source or "")
        if "mrr_current" in p:
            m = _num(p.get("mrr_current"))
            if m > best_mrr:
                best_mrr = m
                out["revenue_source"] = "mrr_current"
                if "stripe" in src.lower() or "stripe" in str(p.get("adapter_id", "")).lower():
                    out["revenue_source"] = "stripe_revenue_snapshot"
                pc = p.get("pct_change")
                mrr_growth = _growth_from_pct(pc)
        if "cost_current" in p:
            c = _num(p.get("cost_current"))
            if observed_cost is None or c > observed_cost:
                observed_cost = c
        if p.get("business_signal") == COST_SPIKE:
            out["cost_spike"] = True

    out["estimated_revenue"] = best_mrr
    out["observed_monthly_cost"] = observed_cost
    out["growth_signal"] = mrr_growth
    return out


def analyze_product_economics(
    node: ProductNode,
    records: list[SignalRecord],
) -> ProductEconomics:
    """Combine declared monthly cost with signal-derived revenue and growth."""
    ext = extract_from_signals(records)
    monthly = _num(node.cost.monthly_usd)
    revenue = float(ext["estimated_revenue"])
    observed = ext["observed_monthly_cost"]
    if isinstance(observed, (int, float)):
        observed_f: float | None = float(observed)
    else:
        observed_f = None

    burn = max(0.0, monthly - revenue)
    roi: float | None
    if monthly > 1e-12:
        roi = (revenue - monthly) / monthly
    else:
        roi = None

    tp = _traction_profile(
        records,
        estimated_revenue=revenue,
        revenue_source=str(ext.get("revenue_source") or ""),
    )
    meta: dict[str, Any] = {
        "declared_monthly_cost_usd": node.cost.monthly_usd,
        "max_monthly_cost_usd": node.constraints.max_monthly_cost_usd,
        "cost_spike_signal": ext["cost_spike"],
        "traction_profile": tp,
    }

    return ProductEconomics(
        product_id=node.id,
        monthly_cost=monthly,
        estimated_revenue=revenue,
        roi_estimate=roi,
        burn_rate=burn,
        growth_signal=ext["growth_signal"],
        observed_monthly_cost=observed_f,
        revenue_source=str(ext.get("revenue_source") or ("none" if revenue <= 0 else "signals")),
        metadata=meta,
    )


def analyze_inventory(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> tuple[list[ProductEconomics], PortfolioEconomics]:
    """
    Build per-product economics and a portfolio rollup for all valid inventory products.

    Reads ``product.yaml`` cost fields and ``runs/signals/latest/<id>.json`` when present.
    """
    inv = build_inventory(repo_root, products_dir=products_dir)
    products: list[ProductEconomics] = []

    for pid in sorted(inv.valid.keys()):
        node = inv.valid[pid].node
        bundle = load_latest_bundle(repo_root, pid)
        records = bundle.records if bundle is not None else []
        products.append(analyze_product_economics(node, records))

    return _rollup(products)


def _rollup(
    products: list[ProductEconomics],
) -> tuple[list[ProductEconomics], PortfolioEconomics]:
    total_cost = sum(p.monthly_cost for p in products)
    total_rev = sum(p.estimated_revenue for p in products)
    margin = total_rev - total_cost
    port_roi: float | None
    if total_cost > 1e-12:
        port_roi = margin / total_cost
    else:
        port_roi = None

    top, worst = rank_performers(products)
    signals = derive_economics_signals(products, total_cost, total_rev)

    portfolio = PortfolioEconomics(
        total_monthly_cost=total_cost,
        total_estimated_revenue=total_rev,
        portfolio_roi=port_roi,
        net_monthly_margin=margin,
        top_performers=top,
        worst_performers=worst,
        products=products,
        signals=signals,
    )
    return products, portfolio


def economics_to_jsonable(
    products: list[ProductEconomics],
    portfolio: PortfolioEconomics,
) -> dict[str, Any]:
    """JSON-safe export for CLI and ``runs/economics/`` artifacts."""
    return {
        "schema": "argus.economics.v1",
        "products": [to_jsonable(p) for p in products],
        "portfolio": {
            "total_monthly_cost": portfolio.total_monthly_cost,
            "total_estimated_revenue": portfolio.total_estimated_revenue,
            "portfolio_roi": portfolio.portfolio_roi,
            "net_monthly_margin": portfolio.net_monthly_margin,
            "top_performers": portfolio.top_performers,
            "worst_performers": portfolio.worst_performers,
            "signals": [
                {
                    "kind": s.kind.value,
                    "product_id": s.product_id,
                    "message": s.message,
                    "details": s.details,
                }
                for s in portfolio.signals
            ],
        },
    }
