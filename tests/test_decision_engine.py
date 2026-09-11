"""Tests for lifecycle scoring and decision priority."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from argus.core.models.enums import (
    EffortBucket,
    FindingKind,
    LifecycleStage,
    SeverityLevel,
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
from argus.decision.engine import generate_decisions
from argus.decision.intents import DecisionIntent
from argus.decision.priority import compute_priority_score, lifecycle_fit
from argus.lifecycle.scoring import assess_lifecycle


def _product(
    pid: str = "p1",
    *,
    stage: LifecycleStage = LifecycleStage.VALIDATE,
    cap: float = 20.0,
    monthly: float | None = 5.0,
) -> ProductNode:
    return ProductNode(
        id=pid,
        name="P",
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(local_paths=[], primary=[]),
        cost=CostDefinition(monthly_usd=monthly),
        signals=[SignalDefinition(type="filesystem", enabled=True)],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=cap),
        lifecycle=ProductLifecycle(stage=stage),
        product_root=f"products/{pid}",
        config_path=f"products/{pid}/product.yaml",
        type_info=ProductTypeInfo(type="app", status="active"),
    )


def _finding(
    kind: FindingKind,
    *,
    pid: str = "p1",
    sev: SeverityLevel = SeverityLevel.MEDIUM,
) -> Finding:
    return Finding(
        id=f"find-{kind.value}",
        product_id=pid,
        kind=kind,
        severity=sev,
        effort=EffortBucket.SMALL,
        title="t",
        summary="s",
        recommendation="r",
        source_signals=["sig-1"],
        evidence={},
        confidence=0.7,
        created_at=datetime.now(timezone.utc),
    )


class TestLifecycleScoring(unittest.TestCase):
    def test_deprecation_raises_deprecate_and_kill(self) -> None:
        p = _product()
        f = _finding(FindingKind.DEPRECATION_CANDIDATE, sev=SeverityLevel.HIGH)
        a = assess_lifecycle(p, [f])
        self.assertGreater(a.deprecate, 0.2)
        self.assertGreaterEqual(a.kill, 0.05)

    def test_launch_raises_move_forward(self) -> None:
        p = _product()
        f = _finding(FindingKind.LAUNCH_CANDIDATE)
        a = assess_lifecycle(p, [f])
        self.assertGreater(a.move_forward, 0.45)

    def test_structural_raises_move_forward_less_than_launch(self) -> None:
        p = _product()
        a_launch = assess_lifecycle(p, [_finding(FindingKind.LAUNCH_CANDIDATE)])
        a_struct = assess_lifecycle(p, [_finding(FindingKind.STRUCTURAL_READINESS)])
        self.assertGreater(a_launch.move_forward, a_struct.move_forward)


class TestPriorityScore(unittest.TestCase):
    def test_higher_severity_raises_score(self) -> None:
        from argus.core.models.decision import DecisionCandidate
        from argus.core.models.enums import ActionType


        p = _product()
        f = _finding(FindingKind.COST_RISK, sev=SeverityLevel.HIGH)
        a = assess_lifecycle(p, [f])
        c = DecisionCandidate(
            id="d1",
            product_id=p.id,
            action_type=ActionType.INVESTIGATE,
            summary="reduce",
            metadata={"intent": DecisionIntent.REDUCE_COST.value},
            confidence=0.8,
            priority_score=None,
        )
        s1 = compute_priority_score(
            c,
            finding=f,
            assessment=a,
            monthly_spend=5.0,
            spend_cap=20.0,
        )
        f2 = _finding(FindingKind.COST_RISK, sev=SeverityLevel.INFO)
        s2 = compute_priority_score(
            c,
            finding=f2,
            assessment=a,
            monthly_spend=5.0,
            spend_cap=20.0,
        )
        self.assertGreater(s1, s2)


class TestGenerateDecisions(unittest.TestCase):
    def test_produces_ranked_candidates(self) -> None:
        p = _product()
        findings = [
            _finding(FindingKind.LAUNCH_CANDIDATE),
            _finding(FindingKind.QUALITY_ISSUE),
        ]
        a, cands = generate_decisions(p, findings)
        self.assertTrue(len(cands) >= 2)
        self.assertIsNotNone(cands[0].priority_score)
        scores = [c.priority_score or 0 for c in cands]
        self.assertEqual(scores, sorted(scores, reverse=True))


class TestLifecycleFit(unittest.TestCase):
    def test_reduce_cost_matches_improve_axis(self) -> None:
        from argus.lifecycle.model import LifecycleAssessment

        a = LifecycleAssessment(
            product_id="x",
            stage=LifecycleStage.VALIDATE,
            move_forward=0.4,
            hold=0.3,
            improve=0.8,
            deprecate=0.1,
            kill=0.05,
        )
        self.assertGreater(
            lifecycle_fit(DecisionIntent.REDUCE_COST, a),
            lifecycle_fit(DecisionIntent.KILL_PRODUCT, a),
        )


if __name__ == "__main__":
    unittest.main()
