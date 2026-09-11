"""Tests for finding rules and consolidation."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from argus.core.models.enums import FindingKind, SignalType
from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.findings.consolidate import merge_candidates
from argus.findings.engine import generate_findings
from argus.findings.rules.builtins import (
    CostRiskRule,
    InactivityRule,
    LaunchCandidateRule,
    LowSignalRule,
    QualityGapRule,
    StructuralReadinessRule,
    ValidationEvidenceGapRule,
    ValidationReadinessRule,
)


def _minimal_product(product_id: str = "p1") -> ProductNode:
    from argus.core.models.enums import LifecycleStage
    from argus.core.models.product import (
        ActionsMap,
        ConstraintsDefinition,
        CostDefinition,
        MetricsDefinition,
        OwnerInfo,
        ProductLifecycle,
        ProductTypeInfo,
        SignalDefinition,
    )

    return ProductNode(
        id=product_id,
        name="P",
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(local_paths=[], primary=[]),
        cost=CostDefinition(),
        signals=[
            SignalDefinition(type="filesystem", enabled=True),
            SignalDefinition(type="cost", enabled=True),
        ],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=20.0),
        lifecycle=ProductLifecycle(stage=LifecycleStage.VALIDATE),
        product_root=f"products/{product_id}",
        config_path=f"products/{product_id}/product.yaml",
        type_info=ProductTypeInfo(type="app", status="experimental"),
    )


class TestCostRisk(unittest.TestCase):
    def test_fires_when_spend_exceeds_cap(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = [
            SignalRecord(
                id="s1",
                product_id=p.id,
                signal_type=SignalType.COST,
                source="cost_file",
                observed_at=ts,
                payload={"ok": True, "monthly_usd": 45.0, "file": "metrics/cost.json"},
            )
        ]
        f = generate_findings(p, sigs, rules=[CostRiskRule()])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, FindingKind.COST_RISK)
        self.assertIn("s1", f[0].source_signals)


class TestInactivity(unittest.TestCase):
    def test_fires_on_health_inactive(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = [
            SignalRecord(
                id="h1",
                product_id=p.id,
                signal_type=SignalType.HEALTH,
                source="heartbeat",
                observed_at=ts,
                payload={"active": False, "check": "activity"},
            )
        ]
        f = generate_findings(p, sigs, rules=[InactivityRule()])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, FindingKind.INACTIVITY)


class TestLowSignal(unittest.TestCase):
    def test_fires_when_few_signals(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = [
            SignalRecord(
                id="a",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="m",
                observed_at=ts,
                payload={"check": "metrics_files", "note": "no JSON"},
            ),
            SignalRecord(
                id="b",
                product_id=p.id,
                signal_type=SignalType.FILESYSTEM,
                source="f",
                observed_at=ts,
                payload={"ok": True},
            ),
        ]
        f = generate_findings(p, sigs, rules=[LowSignalRule()])
        self.assertTrue(any(x.kind == FindingKind.RELIABILITY_PROBLEM for x in f))

    def test_manifest_inflation_uses_non_manifest_count(self) -> None:
        """Many manifest_declaration rows must not mask sparse real observations."""
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs: list[SignalRecord] = []
        for i in range(8):
            sigs.append(
                SignalRecord(
                    id=f"m{i}",
                    product_id=p.id,
                    signal_type=SignalType.FILESYSTEM,
                    source="manifest_declaration",
                    observed_at=ts,
                    tags=["manifest_declaration"],
                    payload={"ok": True},
                )
            )
        sigs.extend(
            [
                SignalRecord(
                    id="a",
                    product_id=p.id,
                    signal_type=SignalType.METRICS,
                    source="m",
                    observed_at=ts,
                    payload={"check": "metrics_files", "note": "no JSON"},
                ),
                SignalRecord(
                    id="b",
                    product_id=p.id,
                    signal_type=SignalType.FILESYSTEM,
                    source="f",
                    observed_at=ts,
                    payload={"ok": True},
                ),
            ]
        )
        f = generate_findings(p, sigs, rules=[LowSignalRule()])
        rel = [x for x in f if x.kind == FindingKind.RELIABILITY_PROBLEM]
        self.assertEqual(len(rel), 1)
        self.assertIn("manifest", rel[0].title.lower())
        self.assertIn("manifest_declaration_count", rel[0].evidence or {})

    def test_suppressed_when_enough_non_manifest_signals(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = []
        for i in range(6):
            sigs.append(
                SignalRecord(
                    id=f"s{i}",
                    product_id=p.id,
                    signal_type=SignalType.FILESYSTEM,
                    source="fs",
                    observed_at=ts,
                    payload={"ok": True},
                )
            )
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics",
                observed_at=ts,
                payload={"check": "ok", "note": "has data"},
            )
        )
        sigs.append(
            SignalRecord(
                id="m2",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics2",
                observed_at=ts,
                payload={"check": "ok", "note": "has data"},
            )
        )
        f = generate_findings(p, sigs, rules=[LowSignalRule()])
        self.assertEqual(
            [x for x in f if x.kind == FindingKind.RELIABILITY_PROBLEM],
            [],
        )


class TestQualityGap(unittest.TestCase):
    def test_missing_path(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = [
            SignalRecord(
                id="f1",
                product_id=p.id,
                signal_type=SignalType.FILESYSTEM,
                source="filesystem",
                observed_at=ts,
                payload={
                    "check": "path_exists",
                    "ok": False,
                    "relative_path": "metrics",
                },
            )
        ]
        f = generate_findings(p, sigs, rules=[QualityGapRule()])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, FindingKind.QUALITY_ISSUE)


class TestLaunchVsStructuralReadiness(unittest.TestCase):
    """Bootstrap metrics, validation contract axes, and lifecycle.stage interact per builtins."""

    def _sig_base(self, pid: str, ts):
        return [
            SignalRecord(
                id="h1",
                product_id=pid,
                signal_type=SignalType.HEALTH,
                source="heartbeat",
                observed_at=ts,
                payload={"active": True, "stale": False},
            ),
            SignalRecord(
                id="fs1",
                product_id=pid,
                signal_type=SignalType.FILESYSTEM,
                source="filesystem",
                observed_at=ts,
                payload={"check": "metrics_path", "ok": True},
            ),
        ]

    def test_bootstrap_only_build_stage_emits_structural_not_launch(self) -> None:
        from argus.core.models.enums import LifecycleStage

        p = _minimal_product()
        p.lifecycle.stage = LifecycleStage.BUILD
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={
                    "format": "json",
                    "data": {"kind": "bootstrap_seed", "schema": "x.local_seed_metrics.v1"},
                },
            )
        )
        launch = generate_findings(p, sigs, rules=[LaunchCandidateRule()])
        struct = generate_findings(p, sigs, rules=[StructuralReadinessRule()])
        self.assertEqual(launch, [])
        self.assertEqual(len(struct), 1)
        self.assertEqual(struct[0].kind, FindingKind.STRUCTURAL_READINESS)
        self.assertTrue(struct[0].evidence.get("metrics_bootstrap_only"))

    def test_validate_operational_metrics_emits_launch_and_validation_readiness(self) -> None:
        """Operational KPI alone is not enough — need a second axis (here: analytics views > 0)."""
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={"format": "json", "data": {"pageviews": 120, "schema": "product.kpi.v1"}},
            )
        )
        sigs.append(
            SignalRecord(
                id="a1",
                product_id=p.id,
                signal_type=SignalType.ANALYTICS,
                source="analytics_file",
                observed_at=ts,
                payload={"file": "metrics/analytics.snapshot.json", "ok": True, "views": 10.0},
            )
        )
        launch = generate_findings(p, sigs, rules=[LaunchCandidateRule()])
        vr = generate_findings(p, sigs, rules=[ValidationReadinessRule()])
        struct = generate_findings(p, sigs, rules=[StructuralReadinessRule()])
        self.assertEqual(len(launch), 1)
        self.assertEqual(launch[0].kind, FindingKind.LAUNCH_CANDIDATE)
        self.assertEqual(len(vr), 1)
        self.assertEqual(vr[0].kind, FindingKind.VALIDATION_READINESS)
        self.assertEqual(struct, [])

    def test_validate_operational_only_without_second_axis_emits_evidence_gap(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={"format": "json", "data": {"pageviews": 120, "schema": "product.kpi.v1"}},
            )
        )
        self.assertEqual(generate_findings(p, sigs, rules=[LaunchCandidateRule()]), [])
        self.assertEqual(generate_findings(p, sigs, rules=[ValidationReadinessRule()]), [])
        gap = generate_findings(p, sigs, rules=[ValidationEvidenceGapRule()])
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap[0].kind, FindingKind.VALIDATION_EVIDENCE_GAP)

    def test_manual_validation_complete_can_satisfy_contract_without_traffic(self) -> None:
        from argus.core.models.enums import LifecycleStage

        p = _minimal_product()
        p.lifecycle.stage = LifecycleStage.BUILD
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={
                    "format": "json",
                    "data": {
                        "argus_validation_evidence": True,
                        "validation_status": "complete",
                        "assessed_at_utc": "2026-01-01T00:00:00Z",
                    },
                },
            )
        )
        vr = generate_findings(p, sigs, rules=[ValidationReadinessRule()])
        self.assertEqual(len(vr), 1)
        self.assertEqual(vr[0].kind, FindingKind.VALIDATION_READINESS)
        self.assertEqual(generate_findings(p, sigs, rules=[LaunchCandidateRule()]), [])

    def test_content_evidence_single_node_not_enough_with_operational(self) -> None:
        """One node without publish — content_evidence axis does not fire; contract fails on validate."""
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={"format": "json", "data": {"pageviews": 5, "schema": "product.kpi.v1"}},
            )
        )
        sigs.append(
            SignalRecord(
                id="m2",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={
                    "format": "json",
                    "data": {
                        "schema": "argus.content_evidence.v1",
                        "node_count": 1,
                        "published_slot_count": 0,
                    },
                },
            )
        )
        self.assertEqual(generate_findings(p, sigs, rules=[ValidationReadinessRule()]), [])
        gap = generate_findings(p, sigs, rules=[ValidationEvidenceGapRule()])
        self.assertEqual(len(gap), 1)

    def test_content_evidence_published_plus_operational_satisfies_contract(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={"format": "json", "data": {"pageviews": 5, "schema": "product.kpi.v1"}},
            )
        )
        sigs.append(
            SignalRecord(
                id="m2",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={
                    "format": "json",
                    "data": {
                        "schema": "argus.content_evidence.v1",
                        "published_slot_count": 1,
                    },
                },
            )
        )
        vr = generate_findings(p, sigs, rules=[ValidationReadinessRule()])
        self.assertEqual(len(vr), 1)

    def test_validate_bootstrap_only_emits_evidence_gap_not_structural(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = self._sig_base(p.id, ts)
        sigs.append(
            SignalRecord(
                id="m1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="metrics_file",
                observed_at=ts,
                payload={
                    "format": "json",
                    "data": {"kind": "bootstrap_seed", "disclaimer": "seed-stage local only"},
                },
            )
        )
        launch = generate_findings(p, sigs, rules=[LaunchCandidateRule()])
        struct = generate_findings(p, sigs, rules=[StructuralReadinessRule()])
        gap = generate_findings(p, sigs, rules=[ValidationEvidenceGapRule()])
        self.assertEqual(launch, [])
        self.assertEqual(struct, [])
        self.assertEqual(len(gap), 1)
        self.assertEqual(gap[0].kind, FindingKind.VALIDATION_EVIDENCE_GAP)


class TestConsolidation(unittest.TestCase):
    def test_merges_same_issue_key(self) -> None:
        from argus.findings.candidates import FindingCandidate

        c1 = FindingCandidate(
            rule_id="inactivity",
            issue_key="no_recent_activity",
            kind=FindingKind.INACTIVITY,
            title="t",
            summary="a",
            recommendation="r",
            source_signal_ids=["s1"],
            evidence={},
        )
        c2 = FindingCandidate(
            rule_id="inactivity",
            issue_key="no_recent_activity",
            kind=FindingKind.INACTIVITY,
            title="t",
            summary="a",
            recommendation="r",
            source_signal_ids=["s2"],
            evidence={"x": 1},
        )
        m = merge_candidates([c1, c2])
        self.assertEqual(len(m), 1)
        self.assertEqual(set(m[0].source_signal_ids), {"s1", "s2"})


if __name__ == "__main__":
    unittest.main()
