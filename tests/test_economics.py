"""Tests for portfolio economics (aggregation and edge cases)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.economics.analyze import (
    _rollup,
    analyze_product_economics,
    extract_from_signals,
)
from argus.economics.models import (
    EconomicsSignalKind,
    GrowthSignal,
    ProductEconomics,
)
from argus.economics.score import derive_economics_signals, rank_performers
from argus.signals.snapshots.models import COST_SPIKE


def _node_with_cost(monthly: float | None, cap: float | None = 100.0):
    from argus.core.models.enums import LifecycleStage
    from argus.core.models.product import (
        ActionsMap,
        ConstraintsDefinition,
        CostDefinition,
        MetricsDefinition,
        OwnerInfo,
        ProductLifecycle,
        ProductNode,
    )

    return ProductNode(
        id="test_p",
        name="Test",
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(),
        cost=CostDefinition(monthly_usd=monthly, notes=""),
        signals=[],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=cap, min_activity_threshold=0.0),
        lifecycle=ProductLifecycle(stage=LifecycleStage.GROW),
        product_root="products/test",
        config_path="products/test/product.yaml",
    )


class TestExtractSignals(unittest.TestCase):
    def test_empty_records(self) -> None:
        self.assertEqual(extract_from_signals([])["estimated_revenue"], 0.0)

    def test_mrr_from_payload(self) -> None:
        r = SignalRecord(
            id="1",
            product_id="p",
            signal_type=SignalType.CUSTOM,
            source="stripe_revenue_snapshot",
            observed_at=datetime.now(timezone.utc),
            payload={
                "adapter": "stripe_revenue_snapshot",
                "mrr_current": 500.0,
                "mrr_prior": 400.0,
                "pct_change": 0.25,
            },
        )
        ex = extract_from_signals([r])
        self.assertEqual(ex["estimated_revenue"], 500.0)
        self.assertEqual(ex["growth_signal"], GrowthSignal.UP)

    def test_cost_spike_flag(self) -> None:
        r = SignalRecord(
            id="2",
            product_id="p",
            signal_type=SignalType.CUSTOM,
            source="aws_cost_snapshot",
            observed_at=datetime.now(timezone.utc),
            payload={"business_signal": COST_SPIKE, "cost_current": 99.0},
        )
        ex = extract_from_signals([r])
        self.assertTrue(ex["cost_spike"])


class TestEdgeCases(unittest.TestCase):
    def test_no_revenue_no_cost(self) -> None:
        node = _node_with_cost(0.0, None)
        pe = analyze_product_economics(node, [])
        self.assertEqual(pe.monthly_cost, 0.0)
        self.assertEqual(pe.estimated_revenue, 0.0)
        self.assertIsNone(pe.roi_estimate)
        self.assertEqual(pe.burn_rate, 0.0)

    def test_no_revenue_positive_cost(self) -> None:
        node = _node_with_cost(50.0, 100.0)
        pe = analyze_product_economics(node, [])
        self.assertEqual(pe.estimated_revenue, 0.0)
        self.assertEqual(pe.roi_estimate, -1.0)
        self.assertEqual(pe.burn_rate, 50.0)

    def test_positive_roi(self) -> None:
        node = _node_with_cost(10.0, 100.0)
        r = SignalRecord(
            id="x",
            product_id="test_p",
            signal_type=SignalType.CUSTOM,
            source="stripe_revenue_snapshot",
            observed_at=datetime.now(timezone.utc),
            payload={"mrr_current": 40.0, "pct_change": 0.1},
        )
        pe = analyze_product_economics(node, [r])
        self.assertEqual(pe.estimated_revenue, 40.0)
        self.assertAlmostEqual(pe.roi_estimate, 3.0)
        self.assertEqual(pe.burn_rate, 0.0)


class TestAggregation(unittest.TestCase):
    def test_portfolio_totals(self) -> None:
        a = ProductEconomics(
            product_id="a",
            monthly_cost=10.0,
            estimated_revenue=30.0,
            roi_estimate=2.0,
            burn_rate=0.0,
            growth_signal=GrowthSignal.UNKNOWN,
        )
        b = ProductEconomics(
            product_id="b",
            monthly_cost=20.0,
            estimated_revenue=5.0,
            roi_estimate=-0.75,
            burn_rate=15.0,
            growth_signal=GrowthSignal.UNKNOWN,
        )
        _, port = _rollup([a, b])
        self.assertEqual(port.total_monthly_cost, 30.0)
        self.assertEqual(port.total_estimated_revenue, 35.0)
        self.assertAlmostEqual(port.net_monthly_margin, 5.0)
        self.assertAlmostEqual(port.portfolio_roi, 5.0 / 30.0)

    def test_rank_performers(self) -> None:
        items = [
            ProductEconomics(
                product_id="low",
                monthly_cost=100.0,
                estimated_revenue=10.0,
                roi_estimate=-0.9,
                burn_rate=90.0,
                growth_signal=GrowthSignal.UNKNOWN,
            ),
            ProductEconomics(
                product_id="high",
                monthly_cost=10.0,
                estimated_revenue=100.0,
                roi_estimate=9.0,
                burn_rate=0.0,
                growth_signal=GrowthSignal.UP,
            ),
        ]
        top, worst = rank_performers(items)
        self.assertEqual(top[0], "high")
        self.assertEqual(worst[0], "low")


class TestSignals(unittest.TestCase):
    def test_underperforming(self) -> None:
        p = ProductEconomics(
            product_id="x",
            monthly_cost=50.0,
            estimated_revenue=10.0,
            roi_estimate=-0.8,
            burn_rate=40.0,
            growth_signal=GrowthSignal.UNKNOWN,
        )
        sigs = derive_economics_signals([p], 50.0, 10.0)
        kinds = {s.kind for s in sigs}
        self.assertIn(EconomicsSignalKind.UNDERPERFORMING_PRODUCT, kinds)

    def test_cost_risk_near_cap(self) -> None:
        p = ProductEconomics(
            product_id="y",
            monthly_cost=95.0,
            estimated_revenue=0.0,
            roi_estimate=-1.0,
            burn_rate=95.0,
            growth_signal=GrowthSignal.UNKNOWN,
            metadata={"max_monthly_cost_usd": 100.0},
        )
        sigs = derive_economics_signals([p], 95.0, 0.0)
        self.assertTrue(any(s.kind == EconomicsSignalKind.COST_RISK for s in sigs))
