"""Orchestration bridge + escalation risk bucketing (deterministic)."""

from __future__ import annotations

import unittest

from argus.escalation.orchestration_bridge import (
    merge_trigger_matches,
    orchestration_trigger_to_match,
)
from argus.escalation.rules import TriggerMatch, max_risk_for_matches


class TestOrchestrationBridge(unittest.TestCase):
    def test_trigger_stable_rule_id_with_session(self) -> None:
        m = orchestration_trigger_to_match(
            {"code": "refinement_not_converged_stuck", "session_id": "s1", "round": 2}
        )
        self.assertEqual(m.rule_id, "refinement_not_converged_stuck@s1")
        self.assertIn("refinement_not_converged_stuck", m.detail)

    def test_merge_prefers_primary_order(self) -> None:
        a = TriggerMatch(rule_id="cost_over_monthly_ceiling", detail="a")
        b = TriggerMatch(rule_id="refinement_session_rejected@s2", detail="b")
        c = TriggerMatch(rule_id="cost_over_monthly_ceiling", detail="c")
        m = merge_trigger_matches([a], [b, c])
        self.assertEqual(len(m), 2)
        self.assertEqual(m[0].rule_id, "cost_over_monthly_ceiling")
        self.assertEqual(m[1].rule_id, "refinement_session_rejected@s2")


class TestOrchestrationRisk(unittest.TestCase):
    def test_max_risk_orchestration_rejected_critical(self) -> None:
        r = max_risk_for_matches(
            [TriggerMatch(rule_id="refinement_session_rejected@sid", detail="{}")]
        )
        self.assertEqual(r, "critical")

    def test_max_risk_orchestration_stuck_high(self) -> None:
        r = max_risk_for_matches(
            [TriggerMatch(rule_id="refinement_not_converged_stuck@sid", detail="{}")]
        )
        self.assertEqual(r, "high")

    def test_core_rules_unchanged(self) -> None:
        from argus.escalation.rules import RULE_KILL_OR_DEPRECATE

        r = max_risk_for_matches([TriggerMatch(rule_id=RULE_KILL_OR_DEPRECATE, detail="x")])
        self.assertEqual(r, "critical")


if __name__ == "__main__":
    unittest.main()
