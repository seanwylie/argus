"""Portfolio-level orchestration priorities artifact (argus.portfolio_priorities.v1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.artifact_paths import (
    orchestration_generations_dir,
    portfolio_priorities_path,
    portfolio_priority_trends_path,
)
from argus.orchestrator.portfolio_priorities import (
    PORTFOLIO_PRIORITIES_SCHEMA,
    build_portfolio_priorities,
)
from argus.orchestrator.state_models import (
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_COMPLETE,
    ORCH_STATUS_ELIGIBLE,
    ORCH_STATUS_ESCALATED,
)
from argus.orchestrator.state_pass import emit_orchestration_batch
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA
from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA


def _minimal_product_yaml(root: Path, product_id: str) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: PP
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


def _write_strategy(root: Path, product_id: str, *, posture: str) -> None:
    p = root / "runs" / "strategy" / "latest" / f"{product_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema": STRATEGY_SNAPSHOT_SCHEMA,
                "schema_version": "1",
                "product_id": product_id,
                "generated_at_utc": "2026-04-12T12:00:00+00:00",
                "posture": posture,
                "posture_raw": posture,
                "summary": "x",
            }
        ),
        encoding="utf-8",
    )


def _write_planning(root: Path, product_id: str, *, mode: str) -> None:
    p = root / "runs" / "planning" / "latest" / f"{product_id}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(
            {
                "schema": PLANNING_SNAPSHOT_SCHEMA,
                "schema_version": "1",
                "product_id": product_id,
                "generated_at_utc": "2026-04-12T12:00:00+00:00",
                "planning_mode": mode,
                "posture": "explore",
            }
        ),
        encoding="utf-8",
    )


class TestPortfolioPriorities(unittest.TestCase):
    def test_actionable_outranks_idle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            active = {
                "next_action": "signals_collect",
                "orchestration_status": ORCH_STATUS_ELIGIBLE,
                "eligible_actions": [{"action_id": "signals_collect", "reason": "x", "reason_codes": []}],
                "escalation_triggers": [],
            }
            idle = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_COMPLETE,
                "eligible_actions": [],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root,
                ["idle_p", "active_p"],
                orchestration_states={"idle_p": idle, "active_p": active},
            )
            prods = payload["products"]
            self.assertEqual(prods[0]["product_id"], "active_p")
            self.assertTrue(
                any("next_action is signals_collect" in r for r in prods[0]["priority_reasons"])
            )

    def test_pivot_outranks_stabilize_same_spine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product_yaml(root, "p_pivot")
            _minimal_product_yaml(root, "p_stab")
            _write_strategy(root, "p_pivot", posture="pivot")
            _write_strategy(root, "p_stab", posture="stabilize")
            spine = {
                "next_action": "signals_collect",
                "orchestration_status": ORCH_STATUS_ELIGIBLE,
                "eligible_actions": [{"action_id": "signals_collect", "reason": "x", "reason_codes": []}],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root,
                ["p_stab", "p_pivot"],
                orchestration_states={"p_pivot": spine, "p_stab": spine},
            )
            self.assertEqual(payload["products"][0]["product_id"], "p_pivot")
            self.assertIn("strategy posture is pivot", payload["products"][0]["priority_reasons"])

    def test_escalation_raises_rank(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hi = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_COMPLETE,
                "eligible_actions": [],
                "escalation_triggers": [{"code": "esc", "action_id": "x"}],
            }
            lo = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_COMPLETE,
                "eligible_actions": [],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root,
                ["lo", "hi"],
                orchestration_states={"hi": hi, "lo": lo},
            )
            self.assertEqual(payload["products"][0]["product_id"], "hi")
            self.assertIn("escalation trigger present", payload["products"][0]["priority_reasons"])

    def test_planning_redirect_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product_yaml(root, "p_redir")
            _write_planning(root, "p_redir", mode="redirect")
            st = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_COMPLETE,
                "eligible_actions": [],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root, ["p_redir"], orchestration_states={"p_redir": st}
            )
            r = payload["products"][0]
            self.assertIn("planning mode is redirect", r["priority_reasons"])

    def test_tie_break_product_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            same = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_COMPLETE,
                "eligible_actions": [],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root,
                ["z_prod", "a_prod"],
                orchestration_states={"z_prod": same, "a_prod": same},
            )
            self.assertEqual([p["product_id"] for p in payload["products"]], ["a_prod", "z_prod"])

    def test_recommended_fields_rank_one(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = build_portfolio_priorities(
                root,
                ["x"],
                orchestration_states={
                    "x": {
                        "next_action": "audit_run",
                        "orchestration_status": ORCH_STATUS_ELIGIBLE,
                        "eligible_actions": [{"action_id": "audit_run"}],
                        "escalation_triggers": [],
                    }
                },
            )
            self.assertEqual(payload["recommended_product_id"], "x")
            self.assertEqual(payload["recommended_next_action"], "audit_run")

    def test_emit_batch_writes_portfolio_priorities(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product_yaml(root, "p1")
            emit_orchestration_batch(root, ["p1"], write=True)
            pp = portfolio_priorities_path(root)
            self.assertTrue(pp.is_file())
            raw = json.loads(pp.read_text(encoding="utf-8"))
            self.assertEqual(raw.get("schema"), PORTFOLIO_PRIORITIES_SCHEMA)
            self.assertEqual(raw.get("recommended_product_id"), "p1")
            gens = list(orchestration_generations_dir(root).glob("portfolio_priorities_*.json"))
            self.assertEqual(len(gens), 1)
            tr = portfolio_priority_trends_path(root)
            self.assertTrue(tr.is_file())
            tr_raw = json.loads(tr.read_text(encoding="utf-8"))
            self.assertEqual(tr_raw.get("schema"), "argus.portfolio_priority_trends.v1")
            self.assertEqual(len(tr_raw.get("products") or []), 1)

    def test_waiting_without_escalation_deprioritized_vs_eligible(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            waiting = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                "eligible_actions": [],
                "escalation_triggers": [],
            }
            eligible = {
                "next_action": "signals_collect",
                "orchestration_status": ORCH_STATUS_ELIGIBLE,
                "eligible_actions": [{"action_id": "signals_collect"}],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root,
                ["wait_p", "go_p"],
                orchestration_states={"wait_p": waiting, "go_p": eligible},
            )
            self.assertEqual(payload["products"][0]["product_id"], "go_p")
            wr = payload["products"][1]
            self.assertEqual(wr["product_id"], "wait_p")
            self.assertTrue(
                any("waiting on external input" in r for r in wr["priority_reasons"])
            )

    def test_missing_strategy_graceful(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            st = {
                "next_action": "none",
                "orchestration_status": ORCH_STATUS_ESCALATED,
                "eligible_actions": [],
                "escalation_triggers": [],
            }
            payload = build_portfolio_priorities(
                root, ["solo"], orchestration_states={"solo": st}
            )
            self.assertIsNone(payload["products"][0].get("strategy_posture"))


if __name__ == "__main__":
    unittest.main()
