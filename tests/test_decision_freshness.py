"""Freshness gating on decisions (temporal signal age vs thresholds)."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.actions.models import ActionContract
from argus.autonomy.safe_execution import evaluate_safe_autonomy
from argus.core.models.enums import (
    EffortBucket,
    FindingKind,
    LifecycleStage,
    SeverityLevel,
    SignalType,
)
from argus.core.models.finding import Finding
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
from argus.core.models.signal import SignalRecord
from argus.decision.engine import generate_decisions
from argus.decision.intents import DecisionIntent
from argus.signals.ids import new_signal_id
from argus.signals.persistence import save_collection


def _product(pid: str = "p1") -> ProductNode:
    return ProductNode(
        id=pid,
        name="P",
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(local_paths=[], primary=[]),
        cost=CostDefinition(monthly_usd=5.0),
        signals=[SignalDefinition(type="filesystem", enabled=True)],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=20.0),
        lifecycle=ProductLifecycle(stage=LifecycleStage.VALIDATE),
        product_root=f"products/{pid}",
        config_path=f"products/{pid}/product.yaml",
        type_info=ProductTypeInfo(type="app", status="active"),
    )


def _finding(kind: FindingKind, *, pid: str = "p1") -> Finding:
    return Finding(
        id=f"find-{kind.value}",
        product_id=pid,
        kind=kind,
        severity=SeverityLevel.MEDIUM,
        effort=EffortBucket.SMALL,
        title="t",
        summary="s",
        recommendation="r",
        source_signals=["sig-1"],
        evidence={},
        confidence=0.75,
        created_at=datetime.now(timezone.utc),
    )


def _metric_signal(pid: str, observed_at: datetime) -> SignalRecord:
    return SignalRecord(
        id=new_signal_id(),
        product_id=pid,
        signal_type=SignalType.METRICS,
        source="test",
        observed_at=observed_at,
        payload={"n": 1},
        confidence=0.9,
    )


class TestDecisionFreshness(unittest.TestCase):
    def setUp(self) -> None:
        self._fixed = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc)

    def test_recent_temporal_signals_no_stale_penalty(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            obs = self._fixed - timedelta(hours=24)
            _, _ = save_collection(root, pid, [_metric_signal(pid, obs)])
            p = _product(pid)
            f = _finding(FindingKind.GROWTH_OPPORTUNITY, pid=pid)
            with patch("argus.decision.freshness._utc_now", return_value=self._fixed):
                _a, cands = generate_decisions(p, [f], repo_root=root)
            growth = [c for c in cands if c.metadata.get("intent") == DecisionIntent.LAUNCH_EXPERIMENT.value]
            self.assertTrue(growth)
            g = growth[0]
            self.assertFalse(g.metadata.get("stale_data_affected_confidence", False))
            self.assertNotIn("stale", " ".join(g.metadata.get("freshness_warnings") or []).lower())

    def test_stale_temporal_reduces_confidence_and_escalates(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            obs = self._fixed - timedelta(hours=200)
            _, _ = save_collection(root, pid, [_metric_signal(pid, obs)])
            p = _product(pid)
            f = _finding(FindingKind.GROWTH_OPPORTUNITY, pid=pid)
            base_conf = f.confidence
            with patch("argus.decision.freshness._utc_now", return_value=self._fixed):
                _a, cands = generate_decisions(p, [f], repo_root=root)
            growth = [c for c in cands if c.metadata.get("intent") == DecisionIntent.LAUNCH_EXPERIMENT.value]
            self.assertTrue(growth)
            g = growth[0]
            self.assertTrue(g.metadata.get("stale_data_affected_confidence"))
            self.assertTrue(g.metadata.get("freshness_escalation"))
            self.assertLess((g.confidence or 0), base_conf or 1.0)
            self.assertTrue(any("stale" in w.lower() for w in (g.metadata.get("freshness_warnings") or [])))

    def test_missing_temporal_recommends_gather(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _, _ = save_collection(root, pid, [])
            p = _product(pid)
            f = _finding(FindingKind.GROWTH_OPPORTUNITY, pid=pid)
            with patch("argus.decision.freshness._utc_now", return_value=self._fixed):
                _a, cands = generate_decisions(p, [f], repo_root=root)
            growth = [c for c in cands if c.metadata.get("intent") == DecisionIntent.LAUNCH_EXPERIMENT.value]
            self.assertTrue(growth)
            g = growth[0]
            self.assertTrue(g.metadata.get("freshness_recommend_gather_data"))
            self.assertTrue(g.metadata.get("stale_data_affected_confidence"))

    def test_freshness_escalation_blocks_autonomous_experiment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products" / "p").mkdir(parents=True)
            c = ActionContract(
                action_id="a1",
                product_id="p",
                action_type="analyze",
                command="echo ok",
                working_directory="products/p",
                experiment_id="exp_test_1",
            )
            md = {
                "freshness_escalation": True,
                "intent": DecisionIntent.LAUNCH_EXPERIMENT.value,
            }
            (root / "runs" / "experiments").mkdir(parents=True)
            (root / "runs" / "experiments" / "exp_test_1.json").write_text(
                '{"schema":"argus.experiment.v1","id":"exp_test_1","product_id":"p",'
                '"type":"growth","hypothesis":"h","status":"proposed"}\n',
                encoding="utf-8",
            )
            ev = evaluate_safe_autonomy(c, repo_root=root, decision_metadata=md)
            self.assertFalse(ev.autonomous)


if __name__ == "__main__":
    unittest.main()
