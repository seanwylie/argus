"""Datatypes for per-product and portfolio economics (local data only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GrowthSignal(str, Enum):
    """Qualitative revenue / traction trend when derivable from local snapshots."""

    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


class EconomicsSignalKind(str, Enum):
    """Economics-layer signals derived from cost, revenue, and ROI."""

    COST_RISK = "cost_risk"
    UNDERPERFORMING_PRODUCT = "underperforming_product"
    HIGH_ROI_OPPORTUNITY = "high_roi_opportunity"


@dataclass
class ProductEconomics:
    """Per-product monthly economics snapshot (declared cost + signal-derived revenue)."""

    product_id: str
    monthly_cost: float
    estimated_revenue: float
    roi_estimate: float | None
    """(revenue - cost) / cost for the month when ``monthly_cost > 0``; else ``None``."""
    burn_rate: float
    """Net cash outflow: ``max(0, monthly_cost - estimated_revenue)``."""
    growth_signal: GrowthSignal
    observed_monthly_cost: float | None = None
    """Optional AWS cost snapshot ``cost_current`` when present (for risk context)."""
    revenue_source: str = ""
    """Human-readable note: e.g. ``stripe_revenue_snapshot`` payload, or ``none``."""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class EconomicsSignal:
    """A portfolio-level economics alert tied to one product."""

    kind: EconomicsSignalKind
    product_id: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioEconomics:
    """Aggregated view across valid inventory products."""

    total_monthly_cost: float
    total_estimated_revenue: float
    portfolio_roi: float | None
    """(total revenue - total cost) / total cost when total cost > 0."""
    net_monthly_margin: float
    """total_estimated_revenue - total_monthly_cost."""
    top_performers: list[str]
    """Product ids ranked by ROI then margin (best first)."""
    worst_performers: list[str]
    """Product ids ranked worst-first by ROI then margin."""
    products: list[ProductEconomics] = field(default_factory=list)
    signals: list[EconomicsSignal] = field(default_factory=list)
