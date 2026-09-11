"""Portfolio attention allocation — inputs and recommendation records."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AllocationBand(StrEnum):
    """How strongly to staff / focus a product next cycle."""

    IGNORE = "ignore"
    MAINTAIN = "maintain"
    PUSH = "push"


@dataclass
class ProductMetricsInput:
    """Lightweight product metrics from signals + economics."""

    estimated_revenue: float
    burn_rate: float
    growth_signal: str
    signal_record_count: int


@dataclass
class ProductPriorityInput:
    """Priority from decision engine (top candidate per product)."""

    priority_score: float
    """0–100 from DecisionCandidate; 0 if no candidate."""
    top_intent: str
    kill_candidate: bool
    lifecycle_stage: str


@dataclass
class ProductTrendInput:
    """Trend / drift slice (from ``runs/trends/latest.json`` when present)."""

    trend_flags: list[str]
    confidence: float
    drift_signal_count: int
    has_trend_data: bool


@dataclass
class ProductCostInput:
    """Declared + observed cost context."""

    monthly_cost_usd: float
    roi_estimate: float | None
    cost_spike: bool


@dataclass
class ProductExperimentsInput:
    """Open experiment pressure per product."""

    active_count: int
    proposed_count: int
    completed_count: int
    failed_count: int


@dataclass
class ProductAllocationInputs:
    """Single product: all drivers for allocation."""

    product_id: str
    metrics: ProductMetricsInput
    priority: ProductPriorityInput
    trend: ProductTrendInput
    cost: ProductCostInput
    experiments: ProductExperimentsInput


@dataclass
class ProductAllocationLine:
    """One row of the recommendation."""

    product_id: str
    focus_pct: float
    band: AllocationBand
    priority_score: float
    raw_weight: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class PortfolioAllocationResult:
    """Full portfolio allocation output (CLI + JSON)."""

    schema: str = "argus.portfolio_allocation.v1"
    generated_at_utc: str = ""
    products: list[ProductAllocationLine] = field(default_factory=list)
    ignore_product_ids: list[str] = field(default_factory=list)
    push_hard_product_ids: list[str] = field(default_factory=list)
    inputs_summary: dict[str, Any] = field(default_factory=dict)
