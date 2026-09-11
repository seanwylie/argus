"""Cost resource registry and linkage reports (local JSON only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResourceEntry:
    """A billable line item (cloud resource, vendor SKU, etc.) mapped to a product when known."""

    id: str
    kind: str
    monthly_cost_usd: float
    product_id: str | None = None
    name: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = "registry"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrphanResource:
    """Resource spend not tied to a valid portfolio product."""

    resource_id: str
    kind: str
    monthly_cost_usd: float
    reason: str
    name: str = ""


@dataclass
class HighCostLowValueProduct:
    """Product with elevated spend vs weak revenue/ROI (heuristic)."""

    product_id: str
    monthly_cost: float
    estimated_revenue: float
    roi_estimate: float | None
    burn_rate: float
    reason: str


@dataclass
class ResourceReport:
    """Merged registry + ingest + signal-derived rows and detection results."""

    generated_at_utc: str
    repo_root: str
    resources: list[ResourceEntry]
    orphan_resources: list[OrphanResource]
    high_cost_low_value: list[HighCostLowValueProduct]
    total_mapped_cost_usd: float
    total_orphan_cost_usd: float
    thresholds: dict[str, float]
    schema: str = "argus.resource_report.v1"
