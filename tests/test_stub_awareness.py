"""Architecture stub / gap awareness for decision confidence."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import ActionType, LifecycleStage
from argus.decision.intents import DecisionIntent
from argus.decision.stub_awareness import (
    STUB_AUTONOMY_RESOURCE_CLEANUP,
    StubGapContext,
    apply_stub_awareness_to_candidates,
    architecture_stub_risk_for_assessment,
    infer_stub_gap_context,
)
from argus.lifecycle.model import LifecycleAssessment


def _assessment() -> LifecycleAssessment:
    return LifecycleAssessment(
        product_id="p1",
        stage=LifecycleStage.VALIDATE,
        move_forward=0.5,
        hold=0.5,
        improve=0.5,
        deprecate=0.3,
        kill=0.2,
    )


class TestStubAwarenessConfidence(unittest.TestCase):
    def test_stubbed_dependency_reduces_confidence(self) -> None:
        c = DecisionCandidate(
            id="c1",
            product_id="p1",
            action_type=ActionType.ARCHIVE,
            summary="Wind down",
            confidence=0.8,
            rationale="Test.",
            metadata={"intent": DecisionIntent.KILL_PRODUCT.value},
        )
        ctx = StubGapContext(active_stub_ids=frozenset({STUB_AUTONOMY_RESOURCE_CLEANUP}))
        out = apply_stub_awareness_to_candidates(
            None,
            "p1",
            [],
            [c],
            _assessment(),
            monthly_spend=10.0,
            spend_cap=100.0,
            strategy_profile=None,
            stub_context=ctx,
        )
        self.assertLess(out[0].confidence or 0, 0.8)
        self.assertIn("stubbed or incomplete", out[0].rationale or "")
        self.assertIn("resource_cleanup_stub", out[0].rationale or "")
        md = out[0].metadata or {}
        self.assertEqual(md.get("stub_awareness", {}).get("matched_architectural_stub_ids"), [STUB_AUTONOMY_RESOURCE_CLEANUP])

    def test_unrelated_stub_does_not_affect_decision(self) -> None:
        c = DecisionCandidate(
            id="c2",
            product_id="p1",
            action_type=ActionType.PAUSE,
            summary="Wait",
            confidence=0.6,
            rationale="Hold.",
            metadata={"intent": DecisionIntent.HOLD_STEADY.value},
        )
        ctx = StubGapContext(active_stub_ids=frozenset({STUB_AUTONOMY_RESOURCE_CLEANUP}))
        out = apply_stub_awareness_to_candidates(
            None,
            "p1",
            [],
            [c],
            _assessment(),
            monthly_spend=10.0,
            spend_cap=100.0,
            strategy_profile=None,
            stub_context=ctx,
        )
        self.assertAlmostEqual(out[0].confidence or 0, 0.6, places=5)
        self.assertNotIn("stubbed or incomplete", out[0].rationale or "")
        sa = (out[0].metadata or {}).get("stub_awareness") or {}
        self.assertEqual(sa.get("applied_confidence_factor"), 1.0)

    def test_capability_gap_matches_only_relevant_intent(self) -> None:
        launch = DecisionCandidate(
            id="c3",
            product_id="p1",
            action_type=ActionType.START,
            summary="Run experiment",
            confidence=0.75,
            rationale="Go.",
            metadata={"intent": DecisionIntent.LAUNCH_EXPERIMENT.value},
        )
        hold = DecisionCandidate(
            id="c4",
            product_id="p1",
            action_type=ActionType.PAUSE,
            summary="Wait",
            confidence=0.75,
            rationale="Wait.",
            metadata={"intent": DecisionIntent.HOLD_STEADY.value},
        )
        ctx = StubGapContext(
            active_stub_ids=frozenset(),
            capability_gap_ids=frozenset({"gap.execution.experiment_tracking"}),
        )
        out = apply_stub_awareness_to_candidates(
            None,
            "p1",
            [],
            [launch, hold],
            _assessment(),
            monthly_spend=10.0,
            spend_cap=100.0,
            strategy_profile=None,
            stub_context=ctx,
        )
        by_id = {x.id: x for x in out}
        self.assertLess(by_id["c3"].confidence or 0, 0.75)
        self.assertAlmostEqual(by_id["c4"].confidence or 0, 0.75, places=5)


class TestArchitectureStubRiskForAssessment(unittest.TestCase):
    def test_matches_top_candidate_stub_tags(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pr = root / "products" / "pd"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: pd
name: pd
owner:
  team: t
lifecycle:
  stage: validate
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
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(exist_ok=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")
            dl = root / "runs" / "decisions" / "latest"
            dl.mkdir(parents=True)
            bundle = {
                "schema": "argus.decisions_bundle.v1",
                "product_id": "pd",
                "generated_at_utc": "2026-01-10T12:00:00+00:00",
                "repo_root": str(root),
                "lifecycle": {"stage": "validate", "kill_candidate": False, "scores": {}, "reasoning": {}},
                "candidates": [
                    {
                        "id": "c1",
                        "product_id": "pd",
                        "action_type": "archive",
                        "summary": "Wind down",
                        "confidence": 0.8,
                        "priority_score": 1.0,
                        "metadata": {"intent": "kill_product"},
                        "rationale": "Test.",
                    }
                ],
            }
            (dl / "pd.json").write_text(json.dumps(bundle), encoding="utf-8")
            r = architecture_stub_risk_for_assessment(root, "pd", [])
            self.assertGreater(r, 0.0)

    def test_zero_without_decisions_file(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(architecture_stub_risk_for_assessment(root, "pd", []), 0.0)


class TestInferStubGapContext(unittest.TestCase):
    def test_infer_always_includes_autonomy_cleanup_stub(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ctx = infer_stub_gap_context(Path(td))
            self.assertIn(STUB_AUTONOMY_RESOURCE_CLEANUP, ctx.active_stub_ids)


class TestDecisionAssessmentStubHooks(unittest.TestCase):
    def test_score_confidence_accepts_architecture_stub_risk(self) -> None:
        from argus.decision_assessment.confidence import score_confidence

        c1, _f1 = score_confidence(
            evidence_density=0.7,
            temporal_freshness_risk=0.1,
            advisor_consensus_confidence=0.6,
            advisor_disagreement_count=0,
            decision_churn_score=0.1,
            has_decisions_artifact=True,
            has_findings_artifact=True,
            has_signals_artifact=True,
            architecture_stub_risk=0.0,
        )
        c2, _f2 = score_confidence(
            evidence_density=0.7,
            temporal_freshness_risk=0.1,
            advisor_consensus_confidence=0.6,
            advisor_disagreement_count=0,
            decision_churn_score=0.1,
            has_decisions_artifact=True,
            has_findings_artifact=True,
            has_signals_artifact=True,
            architecture_stub_risk=0.5,
        )
        self.assertLess(c2, c1)


if __name__ == "__main__":
    unittest.main()
