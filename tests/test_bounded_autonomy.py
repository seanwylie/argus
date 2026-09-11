"""Bounded autonomy rollout: tiers, matrix, escalation hooks, policy resolution."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.autonomy.action_matrix import explain_rollout_action
from argus.autonomy.models import AutonomyMode
from argus.autonomy.operator_policy import effective_policy, save_autonomy_config
from argus.autonomy.policy_resolution import resolve_policy_layers
from argus.autonomy.tiers import AutonomyTier, mode_for_tier
from argus.core.models.enums import LifecycleStage
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
from argus.escalation.rules import (
    RULE_ESCALATION_PRESSURE,
    evaluate_triggers,
)


class TestTierModeMapping(unittest.TestCase):
    def test_mode_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            save_autonomy_config(root, AutonomyMode.SUPERVISED, tier=2)
            mode, pol, tier = effective_policy(root)
            self.assertEqual(mode, AutonomyMode.SUPERVISED)
            self.assertEqual(tier, AutonomyTier.SAFE_EXECUTION)
            self.assertGreaterEqual(pol.max_experiments_per_utc_day, 0)


class TestExplainMatrix(unittest.TestCase):
    def test_account_creation_forbidden(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, _, tier = effective_policy(root)
            out = explain_rollout_action("account_creation", current_tier=tier)
            self.assertTrue(out.get("ok"))
            self.assertFalse(out.get("allowed_under_current_tier"))


class TestPolicyResolution(unittest.TestCase):
    def test_doctrine_wins(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = resolve_policy_layers(root, "p", doctrine_errors=["bad: yaml"])
            self.assertEqual(r.winning_layer, "doctrine")

    def test_safety_invariants_before_autonomy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = resolve_policy_layers(
                root,
                "p",
                safety_invariants=["invariant: no prod writes without branch protection"],
                autonomy_reasons=["would block"],
            )
            self.assertEqual(r.winning_layer, "safety_invariants")


def _minimal_product_node(pid: str = "pe") -> ProductNode:
    return ProductNode(
        id=pid,
        name="P",
        owner=OwnerInfo(team="t"),
        metrics=MetricsDefinition(local_paths=[], primary=[]),
        cost=CostDefinition(monthly_usd=0.0),
        signals=[SignalDefinition(type="filesystem", enabled=True)],
        actions=ActionsMap(),
        constraints=ConstraintsDefinition(max_monthly_cost_usd=10.0),
        lifecycle=ProductLifecycle(stage=LifecycleStage.VALIDATE),
        product_root=f"products/{pid}",
        config_path=f"products/{pid}/product.yaml",
        type_info=ProductTypeInfo(type="app", status="active"),
    )


class TestRolloutEscalationTriggers(unittest.TestCase):
    def test_escalation_pressure_rule(self) -> None:
        from argus.lifecycle.model import LifecycleAssessment

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ad = root / "runs" / "decision_assessment" / "latest"
            ad.mkdir(parents=True)
            (ad / "pe.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.decision_context_assessment.v1",
                        "product_id": "pe",
                        "confidence_score": 0.5,
                        "risk_score": 0.5,
                        "escalation_pressure": 0.9,
                        "assessed_at_utc": "2026-01-01T00:00:00Z",
                    }
                ),
                encoding="utf-8",
            )
            node = _minimal_product_node("pe")
            la = LifecycleAssessment(
                product_id="pe",
                stage=LifecycleStage.VALIDATE,
                move_forward=0.5,
                hold=0.5,
                improve=0.5,
                deprecate=0.3,
                kill=0.2,
            )
            m = evaluate_triggers(node, [], la, [], repo_root=root)
            ids = {x.rule_id for x in m}
            self.assertIn(RULE_ESCALATION_PRESSURE, ids)


class TestTier4OptIn(unittest.TestCase):
    def test_tier4_blocks_without_env(self) -> None:
        from argus.actions.models import ActionContract

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pr = root / "products" / "p4"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: p4
name: p4
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
            save_autonomy_config(root, mode_for_tier(AutonomyTier.FULL_AUTONOMY), tier=4)
            c = ActionContract(
                action_id="a1",
                product_id="p4",
                action_type="analyze",
                working_directory="products/p4",
                command="echo ok",
                requires_approval=False,
                safe_to_auto_execute=True,
                estimated_cost_usd=0.0,
            )
            from argus.autonomy.controller import check_autonomy_execution

            with patch.dict("os.environ", {}, clear=False):
                res = check_autonomy_execution(root, c)
            self.assertFalse(res.allowed)
            self.assertTrue(any("tier 4" in r.lower() for r in res.reasons))


if __name__ == "__main__":
    unittest.main()
