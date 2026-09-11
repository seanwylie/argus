"""Unit and integration tests for :mod:`argus.orchestrator.next_action_policy`."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.next_action_policy import (
    NEXT_ACTION_POLICY_SCHEMA,
    NextActionPolicyInput,
    resolve_next_action,
)
from argus.orchestrator.state_models import (
    ACTION_DECISIONS_GENERATE,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_FINDINGS_GENERATE,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
)
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA, planning_latest_path


class TestNextActionPolicyUnit(unittest.TestCase):
    def test_fallback_first_eligible_when_planning_absent(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = NextActionPolicyInput(
                waiting=False,
                deduped_action_ids=("signals_collect", "audit_run", "findings_generate"),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier=None,
                readiness_reason="",
                repo_root=root,
                product_id="p",
            )
            r = resolve_next_action(inp)
            self.assertEqual(r.next_action, "signals_collect")
            self.assertEqual(r.rule_applied, "soft_planning_pick_next_action")
            self.assertIn("canonical head", (r.tie_break or "").lower())
            self.assertFalse(r.planning_meta.get("planning_priority_adjustment_applied"))

    def test_planning_redirect_boosts_over_canonical_head(self) -> None:
        """Planning ``redirect`` mode boosts escalation/govern-class ids vs interpret head."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_plan"
            pl = planning_latest_path(root, pid)
            pl.parent.mkdir(parents=True, exist_ok=True)
            pl.write_text(
                json.dumps(
                    {
                        "schema": PLANNING_SNAPSHOT_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T00:00:00+00:00",
                        "planning_mode": "redirect",
                        "posture": "explore",
                        "priority_workstreams": [],
                        "recommended_actions": [],
                    }
                ),
                encoding="utf-8",
            )
            inp = NextActionPolicyInput(
                waiting=False,
                deduped_action_ids=(ACTION_FINDINGS_GENERATE, ACTION_ESCALATION_PACKET_GENERATE),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier="success",
                readiness_reason="first_pass_status=success — full pipeline eligible",
                repo_root=root,
                product_id=pid,
            )
            r = resolve_next_action(inp)
            self.assertEqual(r.next_action, ACTION_ESCALATION_PACKET_GENERATE)
            self.assertEqual(r.rule_applied, "soft_planning_pick_next_action")
            self.assertTrue(r.planning_meta.get("planning_priority_adjustment_applied"))
            inf = r.influences.get("soft_planning") or {}
            self.assertEqual(inf.get("canonical_head"), ACTION_FINDINGS_GENERATE)

    def test_progression_impl_beats_planning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_impl"
            pl = planning_latest_path(root, pid)
            pl.parent.mkdir(parents=True, exist_ok=True)
            pl.write_text(
                json.dumps(
                    {
                        "schema": PLANNING_SNAPSHOT_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T00:00:00+00:00",
                        "planning_mode": "redirect",
                        "posture": "explore",
                        "priority_workstreams": [],
                        "recommended_actions": [],
                    }
                ),
                encoding="utf-8",
            )
            inp = NextActionPolicyInput(
                waiting=False,
                deduped_action_ids=(ACTION_FINDINGS_GENERATE, ACTION_IMPLEMENTATION_PLAN_GENERATE),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier=None,
                readiness_reason="",
                repo_root=root,
                product_id=pid,
            )
            r = resolve_next_action(inp)
            self.assertEqual(r.next_action, ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertEqual(r.rule_applied, "progression_implementation_plan_generate")

    def test_waiting_prefers_refinement_submit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = NextActionPolicyInput(
                waiting=True,
                deduped_action_ids=(ACTION_REFINEMENT_SUBMIT_REVIEWS_IN, "signals_collect"),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier=None,
                readiness_reason="",
                repo_root=root,
                product_id="p",
            )
            r = resolve_next_action(inp)
            self.assertEqual(r.next_action, ACTION_REFINEMENT_SUBMIT_REVIEWS_IN)
            self.assertEqual(r.rule_applied, "waiting_refinement_submit_or_none")

    def test_observability_head_unaffected_by_validate_planning(self) -> None:
        """Stale-artifact hygiene puts ``temporal_refresh`` first; validate mode does not boost it away."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_obs"
            pl = planning_latest_path(root, pid)
            pl.parent.mkdir(parents=True, exist_ok=True)
            pl.write_text(
                json.dumps(
                    {
                        "schema": PLANNING_SNAPSHOT_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T00:00:00+00:00",
                        "planning_mode": "validate",
                        "posture": "explore",
                        "priority_workstreams": [],
                        "recommended_actions": [],
                    }
                ),
                encoding="utf-8",
            )
            inp = NextActionPolicyInput(
                waiting=False,
                deduped_action_ids=("temporal_refresh", "signals_collect"),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier=None,
                readiness_reason="",
                repo_root=root,
                product_id=pid,
            )
            r = resolve_next_action(inp)
            self.assertEqual(r.next_action, "temporal_refresh")
            self.assertFalse(r.planning_meta.get("planning_priority_adjustment_applied"))

    def test_import_tier_influence_surface(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = NextActionPolicyInput(
                waiting=False,
                deduped_action_ids=(ACTION_FINDINGS_GENERATE, ACTION_DECISIONS_GENERATE),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier="partial",
                readiness_reason="first_pass_status=partial — limited pipeline",
                repo_root=root,
                product_id="p",
            )
            r = resolve_next_action(inp)
            self.assertEqual(r.influences.get("import_readiness_tier"), "partial")
            self.assertTrue(r.influences.get("readiness_gating"))
            self.assertIn("import_tier_influence", r.influences)

    def test_payload_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            inp = NextActionPolicyInput(
                waiting=False,
                deduped_action_ids=("signals_collect",),
                stuck=False,
                human_exhausted=False,
                rejected=False,
                import_readiness_tier=None,
                readiness_reason="",
                repo_root=root,
                product_id="p",
            )
            r = resolve_next_action(inp)
            d = r.to_payload_dict()
            self.assertEqual(d["schema"], NEXT_ACTION_POLICY_SCHEMA)
            self.assertIn("rule_applied", d)
            self.assertIn("influences", d)


@patch("argus.orchestrator.eligibility.utc_now")
class TestNextActionPolicyIntegration(unittest.TestCase):
    def test_orchestration_state_exposes_policy_partial_import(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_na_partial"
            pr = root / "products" / pid
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                f"""
id: {pid}
name: NA Policy
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths: [metrics/]
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
raw_extensions:
  import_state:
    schema: argus.import_state.v1
    first_pass_status: partial
    imported_at_utc: 2026-04-13T12:00:00Z
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            for n in ("start.sh", "stop.sh", "analyze.sh"):
                (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
            (pr / "metrics").mkdir(parents=True)
            (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")
            ts = "2026-04-13T11:00:00Z"
            sig_p = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sig_p.parent.mkdir(parents=True, exist_ok=True)
            sig_p.write_text(
                json.dumps({"collected_at_utc": ts, "record_count": 1, "records": []}),
                encoding="utf-8",
            )
            for sub, body in (
                ("findings", {"finding_count": 0, "findings": []}),
                ("decisions", {"candidates": []}),
            ):
                p = root / "runs" / sub / "latest" / f"{pid}.json"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(body), encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            pol = st.get("next_action_policy") or {}
            self.assertEqual(pol.get("schema"), NEXT_ACTION_POLICY_SCHEMA)
            self.assertEqual((pol.get("influences") or {}).get("import_readiness_tier"), "partial")
            self.assertEqual(st.get("eligibility_facts", {}).get("next_action_rule_applied"), pol.get("rule_applied"))


if __name__ == "__main__":
    unittest.main()
