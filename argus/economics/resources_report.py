"""Build resource linkage report: orphans + high-cost / low-value products."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import to_jsonable
from argus.economics.analyze import analyze_inventory
from argus.economics.models import ProductEconomics
from argus.economics.registry import load_merged_resources
from argus.economics.resource_models import (
    HighCostLowValueProduct,
    OrphanResource,
    ResourceEntry,
    ResourceReport,
)
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle

# Deterministic heuristics (tunable via report.thresholds in output).
DEFAULT_MIN_MONTHLY_COST_HCLV = 15.0
DEFAULT_MAX_ROI_HCLV = 0.15
DEFAULT_MAX_REVENUE_RATIO = 0.35


def _resources_from_signals(repo_root: Path, valid_product_ids: frozenset[str]) -> list[ResourceEntry]:
    """One synthetic row per product when AWS-style cost_current appears in latest bundle."""
    out: list[ResourceEntry] = []
    for pid in sorted(valid_product_ids):
        bundle = load_latest_bundle(repo_root, pid)
        if bundle is None:
            continue
        best: float | None = None
        for r in bundle.records:
            p = r.payload or {}
            if "cost_current" not in p:
                continue
            try:
                c = float(p["cost_current"])
            except (TypeError, ValueError):
                continue
            if best is None or c > best:
                best = c
        if best is None or best <= 0:
            continue
        src = next(
            (str((r.payload or {}).get("adapter") or r.source or "signal") for r in bundle.records),
            "signal",
        )
        out.append(
            ResourceEntry(
                id=f"observed_cost:{pid}",
                kind="observed_snapshot_cost",
                monthly_cost_usd=round(best, 4),
                product_id=pid,
                name="Latest snapshot observed cost",
                tags=["derived_from_signals", "cost_current"],
                source="signal",
                metadata={"signal_source_hint": src},
            )
        )
    return out


def _detect_orphans(
    resources: list[ResourceEntry],
    valid_ids: frozenset[str],
) -> tuple[list[OrphanResource], float]:
    orphans: list[OrphanResource] = []
    orphan_total = 0.0
    for r in resources:
        if r.product_id is None:
            orphans.append(
                OrphanResource(
                    resource_id=r.id,
                    kind=r.kind,
                    monthly_cost_usd=r.monthly_cost_usd,
                    reason="no_product_mapping",
                    name=r.name,
                )
            )
            orphan_total += max(0.0, r.monthly_cost_usd)
            continue
        if r.product_id not in valid_ids:
            orphans.append(
                OrphanResource(
                    resource_id=r.id,
                    kind=r.kind,
                    monthly_cost_usd=r.monthly_cost_usd,
                    reason=f"unknown_product:{r.product_id}",
                    name=r.name,
                )
            )
            orphan_total += max(0.0, r.monthly_cost_usd)
    return orphans, orphan_total


def _detect_high_cost_low_value(
    products: list[ProductEconomics],
    *,
    min_cost: float,
    max_roi: float,
    max_revenue_ratio: float,
) -> list[HighCostLowValueProduct]:
    out: list[HighCostLowValueProduct] = []
    for p in products:
        if p.monthly_cost < min_cost:
            continue
        roi = p.roi_estimate
        rev_ratio = (p.estimated_revenue / p.monthly_cost) if p.monthly_cost > 0 else 0.0
        bad_roi = roi is not None and roi < max_roi
        bad_rev = rev_ratio < max_revenue_ratio
        if not (bad_roi or bad_rev):
            continue
        parts = []
        if bad_roi:
            parts.append(f"ROI {roi:.3f} < {max_roi}")
        if bad_rev:
            parts.append(f"revenue/cost ratio {rev_ratio:.3f} < {max_revenue_ratio}")
        out.append(
            HighCostLowValueProduct(
                product_id=p.product_id,
                monthly_cost=p.monthly_cost,
                estimated_revenue=p.estimated_revenue,
                roi_estimate=roi,
                burn_rate=p.burn_rate,
                reason="; ".join(parts),
            )
        )
    return out


def build_resource_report(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    min_monthly_cost: float = DEFAULT_MIN_MONTHLY_COST_HCLV,
    max_roi: float = DEFAULT_MAX_ROI_HCLV,
    max_revenue_ratio: float = DEFAULT_MAX_REVENUE_RATIO,
) -> ResourceReport:
    repo_root = repo_root.resolve()
    inv = build_inventory(repo_root, products_dir=products_dir)
    valid_ids = frozenset(inv.valid.keys())

    merged = load_merged_resources(repo_root)
    by_id: dict[str, ResourceEntry] = dict(merged)
    for e in _resources_from_signals(repo_root, valid_ids):
        if e.id not in by_id:
            by_id[e.id] = e
    resources = sorted(by_id.values(), key=lambda x: x.id)

    orphans, orphan_total = _detect_orphans(resources, valid_ids)

    products, _portfolio = analyze_inventory(repo_root, products_dir=products_dir)
    hclv = _detect_high_cost_low_value(
        products,
        min_cost=min_monthly_cost,
        max_roi=max_roi,
        max_revenue_ratio=max_revenue_ratio,
    )

    mapped = sum(max(0.0, r.monthly_cost_usd) for r in resources if r.product_id in valid_ids)

    now = datetime.now(timezone.utc).isoformat()
    thresholds = {
        "min_monthly_cost_hclv": min_monthly_cost,
        "max_roi_hclv": max_roi,
        "max_revenue_ratio_hclv": max_revenue_ratio,
    }
    return ResourceReport(
        generated_at_utc=now,
        repo_root=str(repo_root),
        resources=resources,
        orphan_resources=orphans,
        high_cost_low_value=hclv,
        total_mapped_cost_usd=round(mapped, 2),
        total_orphan_cost_usd=round(orphan_total, 2),
        thresholds=thresholds,
    )


def resource_report_to_jsonable(report: ResourceReport) -> dict[str, Any]:
    return {
        "schema": report.schema,
        "generated_at_utc": report.generated_at_utc,
        "repo_root": report.repo_root,
        "thresholds": report.thresholds,
        "total_mapped_cost_usd": report.total_mapped_cost_usd,
        "total_orphan_cost_usd": report.total_orphan_cost_usd,
        "resources": [to_jsonable(r) for r in report.resources],
        "orphan_resources": [to_jsonable(o) for o in report.orphan_resources],
        "high_cost_low_value": [to_jsonable(h) for h in report.high_cost_low_value],
    }
