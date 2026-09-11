"""Tests for decision context assessment (confidence, uncertainty, risk, escalation pressure)."""

from __future__ import annotations

import json
import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.serialize import to_jsonable
from argus.dashboard.data import build_dashboard_payload
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.decision_assessment.leap import evaluate_exploratory_leap
from argus.decision_assessment.models import EscalationRecommendation
from argus.experiments.models import EvaluationVerdict, ExperimentStatus
from argus.strategy.modes import StrategyMode


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_yaml(pid: str, *, stage: str = "validate", monthly: float = 10.0, cap: float = 100.0) -> str:
    return f"""
    id: {pid}
    name: {pid}
    owner:
      team: test
    lifecycle:
      stage: {stage}
    metrics:
      local_paths: []
      primary: []
    cost:
      monthly_usd: {monthly}
      notes: ""
    signals:
      - type: filesystem
        enabled: true
    actions:
      start: "./scripts/s.sh"
      stop: "./scripts/s.sh"
      analyze: "./scripts/s.sh"
    constraints:
      max_monthly_cost_usd: {cap}
      min_activity_threshold: 0
    """


def _scaffold_product(root: Path, pid: str, **kwargs: object) -> None:
    pr = root / "products" / pid
    _write(pr / "product.yaml", _minimal_yaml(pid, **kwargs))
    (pr / "scripts").mkdir(parents=True, exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")


def _signals_bundle(root: Path, pid: str, *, n: int = 12) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    recs = []
    for i in range(n):
        recs.append(
            {
                "id": f"s{i}",
                "product_id": pid,
                "signal_type": "metrics",
                "source": "t",
                "observed_at": now,
                "confidence": 0.9,
                "tags": [],
                "payload": {"x": i},
            }
        )
    _write(
        root / "runs" / "signals" / "latest" / f"{pid}.json",
        json.dumps(
            {
                "schema": "argus.signals_bundle.v1",
                "product_id": pid,
                "collected_at_utc": now,
                "repo_root": str(root),
                "records": recs,
            }
        ),
    )


def _findings_bundle(root: Path, pid: str, findings: list[Finding]) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    _write(
        root / "runs" / "findings" / "latest" / f"{pid}.json",
        json.dumps(
            {
                "schema": "argus.findings_bundle.v1",
                "product_id": pid,
                "generated_at_utc": now,
                "repo_root": str(root),
                "finding_count": len(findings),
                "findings": [to_jsonable(f) for f in findings],
            }
        ),
    )


def _advisors_consensus(root: Path, pid: str, *, conf: float, disagree_n: int) -> None:
    dis = [f"stub-{i}" for i in range(disagree_n)]
    _write(
        root / "runs" / "advisors" / f"{pid}.latest.json",
        json.dumps(
            {
                "consensus": {
                    "confidence_score": conf,
                    "disagreement_signals": dis,
                }
            }
        ),
    )


def _decisions_latest(root: Path, pid: str) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    _write(
        root / "runs" / "decisions" / "latest" / f"{pid}.json",
        json.dumps(
            {
                "schema": "argus.decisions_bundle.v1",
                "product_id": pid,
                "generated_at_utc": now,
                "repo_root": str(root),
                "lifecycle": {
                    "product_id": pid,
                    "stage": "validate",
                    "scores": {},
                    "kill_candidate": False,
                },
                "candidates": [],
            }
        ),
    )


def _trends_latest(root: Path) -> None:
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc).isoformat()
    _write(
        root / "runs" / "trends" / "latest.json",
        json.dumps({"schema": "argus.trends.v1", "generated_at_utc": now, "repo_root": str(root)}),
    )


class TestDecisionAssessmentScenarios(unittest.TestCase):
    def test_fresh_evidence_and_aligned_advisors_boost_confidence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "fresh_adv"
            _scaffold_product(root, pid, stage="validate")
            _signals_bundle(root, pid, n=20)
            _findings_bundle(root, pid, [])
            _decisions_latest(root, pid)
            _trends_latest(root)
            _advisors_consensus(root, pid, conf=0.92, disagree_n=0)
            a = evaluate_decision_context(root, pid)
            self.assertGreater(a.confidence_score, 0.55)
            self.assertLess(a.uncertainty_score, 0.55)

    def test_stale_gap_and_conflicting_advisors_raise_uncertainty(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "stale_adv"
            _scaffold_product(root, pid)
            # No signals / findings / decisions → temporal grounding marks missing sources
            _advisors_consensus(root, pid, conf=0.5, disagree_n=4)
            a = evaluate_decision_context(root, pid)
            self.assertGreater(a.uncertainty_score, 0.45)

    def test_failed_experiments_raise_recurrence(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "rec_exp"
            _scaffold_product(root, pid)
            exdir = root / "runs" / "experiments"
            exdir.mkdir(parents=True)
            for i in range(4):
                _write(
                    exdir / f"exp_fail_{i}_aaaaaaaa.json",
                    json.dumps(
                        {
                            "schema": "argus.experiment.v1",
                            "id": f"exp_fail_{i}_aaaaaaaa",
                            "product_id": pid,
                            "hypothesis": "h",
                            "type": "growth",
                            "description": "d",
                            "expected_outcome": "x",
                            "status": ExperimentStatus.COMPLETED.value,
                            "created_at": "2026-01-01T00:00:00+00:00",
                            "last_evaluation_verdict": EvaluationVerdict.FAILED.value,
                        }
                    ),
                )
            a = evaluate_decision_context(root, pid)
            self.assertGreaterEqual(a.recurrence_risk_score, 0.15)

    def test_exploration_mode_allows_leap_more_than_profit(self) -> None:
        from argus.core.models.enums import LifecycleStage

        ok_expl, reason_expl, _, _ = evaluate_exploratory_leap(
            strategy_mode=StrategyMode.EXPLORATION,
            confidence_score=0.45,
            uncertainty_score=0.42,
            risk_score=0.35,
            evidence_density_score=0.38,
            monthly_cost_usd=10.0,
            max_monthly_cost_usd=100.0,
            lifecycle_stage=LifecycleStage.VALIDATE,
        )
        ok_pr, reason_pr, _, _ = evaluate_exploratory_leap(
            strategy_mode=StrategyMode.PROFIT,
            confidence_score=0.45,
            uncertainty_score=0.42,
            risk_score=0.35,
            evidence_density_score=0.38,
            monthly_cost_usd=10.0,
            max_monthly_cost_usd=100.0,
            lifecycle_stage=LifecycleStage.VALIDATE,
        )
        self.assertTrue(ok_expl)
        self.assertFalse(ok_pr)
        self.assertIn("EXPLORATION", reason_pr)

    def test_escalation_pressure_when_contradictory_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "escal"
            _scaffold_product(root, pid)
            f1 = Finding(
                id="f1",
                product_id=pid,
                kind=FindingKind.COST_RISK,
                severity=SeverityLevel.HIGH,
                effort=EffortBucket.SMALL,
                title="cost",
                summary="cost",
                recommendation="r",
                evidence={},
            )
            f2 = Finding(
                id="f2",
                product_id=pid,
                kind=FindingKind.GROWTH_OPPORTUNITY,
                severity=SeverityLevel.HIGH,
                effort=EffortBucket.SMALL,
                title="growth",
                summary="growth",
                recommendation="r",
                evidence={},
            )
            _findings_bundle(root, pid, [f1, f2])
            _advisors_consensus(root, pid, conf=0.4, disagree_n=5)
            a = evaluate_decision_context(root, pid)
            self.assertGreaterEqual(a.escalation_pressure, 0.38)

    def test_dashboard_includes_decision_context(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "dash_ctx"
            _scaffold_product(root, pid)
            _signals_bundle(root, pid, n=8)
            _findings_bundle(root, pid, [])
            _decisions_latest(root, pid)
            payload = build_dashboard_payload(root)
            prows = payload.get("products") or []
            match = next((p for p in prows if p.get("product_id") == pid), None)
            self.assertIsNotNone(match)
            assert match is not None
            self.assertIn("decision_context", match)
            ov = match.get("operator_visibility") or {}
            self.assertIn("uncertainty", ov)


class TestEscalationRecommendation(unittest.TestCase):
    def test_enum_values(self) -> None:
        self.assertEqual(EscalationRecommendation.PROCEED.value, "proceed")
