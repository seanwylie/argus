"""Wire-into-reality seams: snapshots, doctrine→ideas, bootstrap, shutdown archive plan."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from argus.autonomy.shutdown import build_archive_plan, gather_shutdown_signals
from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.doctrine.models import DoctrineConstraints, DoctrineScoring, ProductDoctrine
from argus.doctrine.scoring import apply_doctrine_to_ideas
from argus.economics.analyze import _traction_profile
from argus.idea_generation.models import Idea, IdeaSource, IdeaType
from argus.lifecycle.kill import compute_kill_score_for_product
from argus.products.inventory import build_inventory
from argus.products.reality_bootstrap import write_reality_bootstrap
from argus.signals.snapshots.content_platform import ingest_content_platform_snapshot


class TestContentPlatformSnapshot(unittest.TestCase):
    def test_ingest_json_subscribers(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            snap = root / "content_platform_weekly.json"
            snap.write_text(
                json.dumps(
                    {
                        "product_id": "x",
                        "platform": "substack",
                        "subscribers": {"current": 1100, "prior": 1000},
                    }
                ),
                encoding="utf-8",
            )
            recs = ingest_content_platform_snapshot(snap, root)
            self.assertTrue(recs)
            self.assertIn("content_platform_snapshot", (recs[0].payload or {}).get("adapter", ""))


class TestTractionProfile(unittest.TestCase):
    def test_monetized_vs_vanity(self) -> None:
        r_mrr = SignalRecord(
            id="r1",
            product_id="p",
            source="t",
            signal_type=SignalType.CUSTOM,
            observed_at=datetime.now(timezone.utc),
            payload={"adapter": "stripe_snapshot", "mrr_current": 50.0},
            confidence=0.9,
        )
        self.assertEqual(
            _traction_profile([r_mrr], estimated_revenue=50.0, revenue_source="stripe"),
            "monetized",
        )
        r_pv = SignalRecord(
            id="r2",
            product_id="p",
            source="t",
            signal_type=SignalType.CUSTOM,
            observed_at=datetime.now(timezone.utc),
            payload={"adapter": "posthog_snapshot", "pageviews_current": 100.0},
            confidence=0.8,
        )
        self.assertEqual(
            _traction_profile([r_pv], estimated_revenue=0.0, revenue_source="none"),
            "vanity_traction",
        )


class TestDoctrineIdeas(unittest.TestCase):
    def test_boost_explore_invent(self) -> None:
        doc = ProductDoctrine(
            schema_id="argus.doctrine.v1",
            summary=None,
            principles=(),
            constraints=DoctrineConstraints(),
            scoring=DoctrineScoring(experiment_score_boost=0.2),
        )
        ideas = [
            Idea(
                idea_id="a",
                title="A",
                description="d",
                type=IdeaType.EXPLORE,
                source=IdeaSource.SYNTHESIS,
                novelty_score=0.5,
                adjacency_score=0.5,
                expected_value_score=0.5,
                confidence_score=0.5,
                cost_estimate="",
                channel_type="hybrid",
                monetization_type="hybrid",
                rationale="",
            ),
            Idea(
                idea_id="b",
                title="B",
                description="d",
                type=IdeaType.EXPLOIT,
                source=IdeaSource.SYNTHESIS,
                novelty_score=0.5,
                adjacency_score=0.5,
                expected_value_score=0.5,
                confidence_score=0.5,
                cost_estimate="",
                channel_type="hybrid",
                monetization_type="hybrid",
                rationale="",
            ),
        ]
        before = ideas[0].expected_value_score
        meta = apply_doctrine_to_ideas(ideas, doc)
        self.assertTrue(meta.get("applied"))
        self.assertGreater(ideas[0].expected_value_score, before)
        self.assertEqual(ideas[1].expected_value_score, 0.5)


class TestRealityBootstrap(unittest.TestCase):
    def test_writes_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "nb"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: nb
name: Nb
owner:
  team: t
lifecycle:
  stage: build
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 0
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
                encoding="utf-8",
            )
            code, msg, detail = write_reality_bootstrap(root, "nb")
            self.assertEqual(code, 0, msg=msg)
            self.assertTrue((pr / "doctrine.yaml").is_file())
            self.assertTrue((pr / "experiments" / "seed.json").is_file())
            self.assertTrue((pr / "metrics" / "analytics_placeholders.md").is_file())


class TestShutdownArchivePlan(unittest.TestCase):
    def test_archive_plan_has_steps(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "sd"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(exist_ok=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (pr / "product.yaml").write_text(
                """
id: sd
name: Sd
owner:
  team: t
lifecycle:
  stage: validate
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 5
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
                encoding="utf-8",
            )
            inv = build_inventory(root)
            node = inv.valid["sd"].node
            ap = build_archive_plan(root, "sd", node)
            self.assertEqual(ap.schema, "argus.archive_plan.v1")
            self.assertGreaterEqual(len(ap.steps), 2)
            self.assertTrue(ap.provider_cleanup_plan.get("stub"))

    def test_gather_signals_has_orphan_fields(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "k1"
            pr.mkdir(parents=True)
            (pr / "scripts").mkdir(exist_ok=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (pr / "product.yaml").write_text(
                """
id: k1
name: K1
owner:
  team: t
lifecycle:
  stage: grow
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 50
  min_activity_threshold: 0
""",
                encoding="utf-8",
            )
            inv = build_inventory(root)
            ks = compute_kill_score_for_product(root, "k1", inv.valid["k1"].node)
            sig = gather_shutdown_signals(root, "k1", ks)
            self.assertIsNotNone(sig.orphan_resource_count)
            self.assertGreaterEqual(sig.orphan_spend_usd_monthly, 0.0)


if __name__ == "__main__":
    unittest.main()
