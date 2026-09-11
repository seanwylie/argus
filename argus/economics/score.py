"""Ranking and economics-layer signal derivation."""

from __future__ import annotations

from argus.economics.models import EconomicsSignal, EconomicsSignalKind, ProductEconomics


def _performance_key(p: ProductEconomics) -> tuple[float, float]:
    """Prefer net monthly margin, then ROI when present."""
    margin = p.estimated_revenue - p.monthly_cost
    roi = p.roi_estimate if p.roi_estimate is not None else float("-inf")
    return (margin, roi)


def rank_performers(products: list[ProductEconomics]) -> tuple[list[str], list[str]]:
    """
    Return (top_performers, worst_performers) by net monthly margin, then ROI.

    Works when declared cost is zero (ROI undefined): margin still ranks products.
    """
    if not products:
        return [], []

    scored = sorted(products, key=_performance_key, reverse=True)
    order = [p.product_id for p in scored]
    worst_order = list(reversed(order))
    top_n = min(5, len(order))
    return order[:top_n], worst_order[:top_n]


def derive_economics_signals(
    products: list[ProductEconomics],
    total_cost: float,
    total_revenue: float,
) -> list[EconomicsSignal]:
    """
    Emit cost risk, underperformance, and high-ROI opportunity signals (rules-based).

    Thresholds are intentionally simple and local-data-driven.
    """
    out: list[EconomicsSignal] = []
    if not products:
        return out

    rois = [p.roi_estimate for p in products if p.roi_estimate is not None]
    median_roi = 0.0
    if rois:
        sr = sorted(rois)
        median_roi = sr[len(sr) // 2]

    for p in products:
        cap = p.metadata.get("max_monthly_cost_usd")
        if isinstance(cap, (int, float)) and cap > 0 and p.monthly_cost >= 0.9 * float(cap):
            out.append(
                EconomicsSignal(
                    kind=EconomicsSignalKind.COST_RISK,
                    product_id=p.product_id,
                    message=(
                        f"Monthly cost ${p.monthly_cost:.2f} is near or above "
                        f"declared cap ${float(cap):.2f}."
                    ),
                    details={"monthly_cost": p.monthly_cost, "cap": float(cap)},
                )
            )
        if p.metadata.get("cost_spike"):
            out.append(
                EconomicsSignal(
                    kind=EconomicsSignalKind.COST_RISK,
                    product_id=p.product_id,
                    message="Cost spike reported in latest ingested business snapshot.",
                    details={},
                )
            )

        if p.monthly_cost > 0 and p.estimated_revenue < p.monthly_cost:
            out.append(
                EconomicsSignal(
                    kind=EconomicsSignalKind.UNDERPERFORMING_PRODUCT,
                    product_id=p.product_id,
                    message=(
                        f"Estimated revenue ${p.estimated_revenue:.2f} is below "
                        f"monthly cost ${p.monthly_cost:.2f} (burn ${p.burn_rate:.2f})."
                    ),
                    details={
                        "burn_rate": p.burn_rate,
                        "estimated_revenue": p.estimated_revenue,
                        "monthly_cost": p.monthly_cost,
                    },
                )
            )

        if (
            p.roi_estimate is not None
            and p.roi_estimate >= 0.5
            and p.growth_signal.value == "up"
        ):
            out.append(
                EconomicsSignal(
                    kind=EconomicsSignalKind.HIGH_ROI_OPPORTUNITY,
                    product_id=p.product_id,
                    message=(
                        f"Strong ROI (~{p.roi_estimate:.2f}) with positive revenue growth "
                        "in local snapshot — candidate to prioritize."
                    ),
                    details={"roi_estimate": p.roi_estimate},
                )
            )
        elif (
            p.roi_estimate is not None
            and p.roi_estimate >= 1.0
            and total_cost > 0
            and p.roi_estimate >= median_roi + 0.25
        ):
            out.append(
                EconomicsSignal(
                    kind=EconomicsSignalKind.HIGH_ROI_OPPORTUNITY,
                    product_id=p.product_id,
                    message=(
                        f"ROI (~{p.roi_estimate:.2f}) stands above portfolio median "
                        f"({median_roi:.2f}); consider reinvestment."
                    ),
                    details={"roi_estimate": p.roi_estimate, "median_roi": median_roi},
                )
            )

    if total_cost > 0 and total_revenue < total_cost * 0.5:
        out.append(
            EconomicsSignal(
                kind=EconomicsSignalKind.COST_RISK,
                product_id="*",
                message=(
                    f"Portfolio estimated revenue (${total_revenue:.2f}) is far below "
                    f"total declared cost (${total_cost:.2f}); burn focus recommended."
                ),
                details={
                    "total_monthly_cost": total_cost,
                    "total_estimated_revenue": total_revenue,
                },
            )
        )

    return out
