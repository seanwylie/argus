"""Tests for business snapshot ingestion (local JSON/CSV)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from argus.core.models.enums import LifecycleStage, SignalType
from argus.core.models.product import (
    ActionsMap,
    ConstraintsDefinition,
    CostDefinition,
    MetricsDefinition,
    OwnerInfo,
    ProductLifecycle,
    ProductNode,
    ProductTypeInfo,
    SignalDefinition,
)
from argus.signals.snapshots.aws_cost import ingest_aws_cost_snapshot
from argus.signals.snapshots.binding import BindingError, resolve_product_id
from argus.signals.snapshots.google_analytics import ingest_google_analytics_snapshot
from argus.signals.snapshots.ingest import ingest_snapshots_for_product
from argus.signals.snapshots.mobile_app import ingest_mobile_app_snapshot
from argus.signals.snapshots.models import (
    BUSINESS_SIGNAL_KEY,
    CONVERSION_DOWN,
    COST_SPIKE,
    NO_USAGE,
    TRAFFIC_UP,
)
from argus.signals.snapshots.posthog import ingest_posthog_snapshot
from argus.signals.snapshots.stripe_revenue import ingest_stripe_revenue_snapshot


def _node(pid: str) -> ProductNode:
    return ProductNode(
        id=pid,
        name=pid,
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(local_paths=[], primary=[]),
        cost=CostDefinition(monthly_usd=1.0),
        signals=[SignalDefinition(type="custom", enabled=True)],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=100.0),
        lifecycle=ProductLifecycle(stage=LifecycleStage.BUILD),
        product_root=f"products/{pid}",
        config_path=f"products/{pid}/product.yaml",
        type_info=ProductTypeInfo(type="app", status="active"),
    )


class TestBinding(unittest.TestCase):
    def test_resolve_from_path_products(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "posthog.json"
            p.parent.mkdir(parents=True)
            p.write_text("{}", encoding="utf-8")
            self.assertEqual(resolve_product_id(p, root, {"product_id": "p1"}), "p1")

    def test_mismatch_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "x.json"
            p.parent.mkdir(parents=True)
            p.write_text("{}", encoding="utf-8")
            with self.assertRaises(BindingError):
                resolve_product_id(p, root, {"product_id": "other"})


class TestPosthog(unittest.TestCase):
    def test_traffic_up(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = (
                root / "products" / "p1" / "metrics" / "snapshots" / "posthog.snapshot.json"
            )
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "pageviews": {"current": 200, "prior": 100},
                    }
                ),
                encoding="utf-8",
            )
            recs = ingest_posthog_snapshot(p, root)
            self.assertEqual(len(recs), 1)
            self.assertEqual(recs[0].signal_type, SignalType.CUSTOM)
            self.assertEqual(recs[0].payload.get(BUSINESS_SIGNAL_KEY), TRAFFIC_UP)


class TestGoogleAnalytics(unittest.TestCase):
    def test_conversion_down(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "ga4.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "sessions": {"current": 100, "prior": 100},
                        "conversion_rate": {"current": 0.01, "prior": 0.03},
                    }
                ),
                encoding="utf-8",
            )
            recs = ingest_google_analytics_snapshot(p, root)
            kinds = {r.payload.get(BUSINESS_SIGNAL_KEY) for r in recs}
            self.assertIn(CONVERSION_DOWN, kinds)


class TestAwsStripe(unittest.TestCase):
    def test_cost_spike(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "aws_cost.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps({"cost_usd": {"current_month": 400, "prior_month": 100}}),
                encoding="utf-8",
            )
            recs = ingest_aws_cost_snapshot(p, root)
            self.assertEqual(recs[0].payload.get(BUSINESS_SIGNAL_KEY), COST_SPIKE)

    def test_revenue_flat(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "stripe.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps({"mrr_usd": {"current": 1000, "prior": 1000}}),
                encoding="utf-8",
            )
            recs = ingest_stripe_revenue_snapshot(p, root)
            self.assertEqual(recs[0].payload.get(BUSINESS_SIGNAL_KEY), "revenue_flat")


class TestMobile(unittest.TestCase):
    def test_no_usage_and_retention(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            p = root / "products" / "p1" / "metrics" / "snapshots" / "mobile.json"
            p.parent.mkdir(parents=True)
            p.write_text(
                json.dumps(
                    {
                        "dau": {"current": 2, "prior": 500},
                        "retention_d7": {"current": 0.04, "prior": 0.2},
                    }
                ),
                encoding="utf-8",
            )
            recs = ingest_mobile_app_snapshot(p, root)
            kinds = {r.payload.get(BUSINESS_SIGNAL_KEY) for r in recs}
            self.assertIn(NO_USAGE, kinds)


class TestIngestIntegration(unittest.TestCase):
    def test_ingest_product_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pid = "ingesttest"
            d = root / "products" / pid / "metrics" / "snapshots"
            d.mkdir(parents=True)
            (d / "posthog_x.json").write_text(
                json.dumps({"pageviews": {"current": 110, "prior": 100}}),
                encoding="utf-8",
            )
            node = _node(pid)
            recs = ingest_snapshots_for_product(root, node, include_fixtures=False)
            self.assertTrue(recs)
            self.assertEqual(recs[0].product_id, pid)


if __name__ == "__main__":
    unittest.main()
