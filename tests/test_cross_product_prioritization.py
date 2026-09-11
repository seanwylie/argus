"""Deterministic cross-product orchestration prioritization (index batch)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.eligibility import (
    RC_ORCH_FEEDBACK_REPEATED_FAILURES,
    RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
)
from argus.orchestrator.state_pass import (
    build_cross_product_prioritization,
    emit_orchestration_batch,
    orchestration_priority_tuple,
)


def _minimal_product_yaml(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: CrossPri
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths:
    - metrics/
  primary: []
cost:
  monthly_usd: 1
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
    (pr / "scripts").mkdir(exist_ok=True)
    for name in ("start.sh", "stop.sh", "analyze.sh"):
        (pr / "scripts" / name).write_text("#!/bin/sh\necho x\n", encoding="utf-8")
    (pr / "metrics").mkdir(exist_ok=True)
    (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")


class TestCrossProductPrioritization(unittest.TestCase):
    def test_healthy_eligible_outranks_inert(self) -> None:
        healthy = {
            "eligible_actions": [{"action_id": "signals_collect", "reason": "x", "reason_codes": []}],
            "escalation_triggers": [],
            "eligibility_facts": {},
        }
        inert = {"eligible_actions": [], "escalation_triggers": [], "eligibility_facts": {}}
        out = build_cross_product_prioritization({"a": healthy, "b": inert})
        self.assertEqual(out["ranked_product_ids"][0], "a")

    def test_repeated_failure_outranks_eligible_only(self) -> None:
        stuck = {
            "eligible_actions": [{"action_id": "signals_collect", "reason": "x", "reason_codes": []}],
            "escalation_triggers": [{"code": RC_ORCH_FEEDBACK_REPEATED_FAILURES, "action_id": "signals_collect"}],
            "eligibility_facts": {},
        }
        healthy_only = {
            "eligible_actions": [{"action_id": "audit_run", "reason": "y", "reason_codes": []}],
            "escalation_triggers": [],
            "eligibility_facts": {},
        }
        out = build_cross_product_prioritization({"h": healthy_only, "s": stuck})
        self.assertEqual(out["ranked_product_ids"][0], "s")

    def test_repeated_queued_unhandled_ordering(self) -> None:
        uh = {
            "eligible_actions": [],
            "escalation_triggers": [],
            "eligibility_facts": {},
        }
        uh2 = {
            "eligible_actions": [],
            "escalation_triggers": [{"code": RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED, "action_id": "x"}],
            "eligibility_facts": {},
        }
        out = build_cross_product_prioritization({"low": uh, "hi": uh2})
        self.assertEqual(out["ranked_product_ids"][0], "hi")

    def test_retry_reopened_outranks_fully_inert(self) -> None:
        reopened = {
            "eligible_actions": [],
            "escalation_triggers": [],
            "eligibility_facts": {"orchestration_feedback_failed_deprioritize_suppressed_action_ids": ["signals_collect"]},
        }
        inert = {"eligible_actions": [], "escalation_triggers": [], "eligibility_facts": {}}
        out = build_cross_product_prioritization({"z": inert, "r": reopened})
        self.assertEqual(out["ranked_product_ids"][0], "r")

    def test_unrelated_products_isolated_tiebreak(self) -> None:
        """Equal tuple → sort by product_id ascending."""
        a = {"eligible_actions": [], "escalation_triggers": [], "eligibility_facts": {}}
        b = {"eligible_actions": [], "escalation_triggers": [], "eligibility_facts": {}}
        out = build_cross_product_prioritization({"z": a, "m": b})
        self.assertEqual(out["ranked_product_ids"], ["m", "z"])

    def test_orchestration_priority_tuple_explicit(self) -> None:
        st = {
            "eligible_actions": [{"action_id": "x"}],
            "escalation_triggers": [{"code": RC_ORCH_FEEDBACK_REPEATED_FAILURES}],
            "eligibility_facts": {"orchestration_feedback_failed_deprioritize_suppressed_action_ids": ["signals_collect"]},
        }
        self.assertEqual(orchestration_priority_tuple(st), (1, 0, 1, 1, 1))

    def test_emit_batch_includes_prioritization(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid_a = "p_alpha"
            pid_b = "p_beta"
            _minimal_product_yaml(root, pid_a)
            _minimal_product_yaml(root, pid_b)
            payload, _ = emit_orchestration_batch(root, [pid_b, pid_a], write=False)
            self.assertIsNone(payload.get("batch_advancement_artifact_path_repo_relative"))
            self.assertIsNone(payload.get("batch_advancement_selected_advancement_path_repo_relative"))
            self.assertIn("cross_product_prioritization", payload)
            cp = payload["cross_product_prioritization"]
            assert isinstance(cp, dict)
            self.assertIn("ranked_product_ids", cp)
            self.assertEqual(len(cp["entries"]), 2)
            prods = payload.get("products") or []
            self.assertEqual(len(prods), 2)
            for row in prods:
                if isinstance(row, dict):
                    self.assertIn("priority_rank", row)
                    self.assertIn("priority_tuple", row)


if __name__ == "__main__":
    unittest.main()
