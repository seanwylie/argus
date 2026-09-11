"""Portfolio bounded progression (``argus.portfolio_progression.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from argus.orchestrator.state_models import (
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_SKIPPED,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_ELIGIBLE,
)
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    operator_queue_output_dir,
    write_operator_queue,
)
from argus.portfolio.progression import (
    PORTFOLIO_PROGRESSION_SCHEMA,
    load_operator_queue_or_rebuild,
    portfolio_progression_dir,
    run_portfolio_progression,
)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: P
owner:
  team: test
lifecycle:
  stage: validate
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
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    for n in ("start.sh", "stop.sh", "analyze.sh"):
        (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")


class TestPortfolioProgression(unittest.TestCase):
    def test_mixed_ready_waiting_import_failed(self) -> None:
        """Top queue slice: import-failed skipped, waiting skipped, ready advances."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("ready", "imp_fail", "wait_blk"):
                _minimal_product(root, pid)
            write_operator_queue(root)

            def _state(pid: str) -> dict:
                if pid == "imp_fail":
                    return {
                        "product_id": pid,
                        "orchestration_status": ORCH_STATUS_ELIGIBLE,
                        "next_action": "signals_collect",
                        "import_health": {"first_pass_status": "failed", "gating_tier": "failed"},
                        "eligible_actions": [{"action_id": "signals_collect", "reason": "r"}],
                        "waiting_inputs": [],
                    }
                if pid == "wait_blk":
                    return {
                        "product_id": pid,
                        "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                        "next_action": "none",
                        "import_health": {"first_pass_status": "success", "gating_tier": "success"},
                        "eligible_actions": [],
                        "waiting_inputs": [{"kind": "k"}],
                    }
                return {
                    "product_id": pid,
                    "orchestration_status": ORCH_STATUS_ELIGIBLE,
                    "next_action": "audit_run",
                    "import_health": {"first_pass_status": "success", "gating_tier": "success"},
                    "eligible_actions": [{"action_id": "audit_run", "reason": "r"}],
                    "waiting_inputs": [],
                }

            def _ev_side_effect(r: Path, pid: str) -> dict:
                return _state(pid)

            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                adv.return_value = (
                    MagicMock(),
                    {
                        "action_status": ACTION_STATUS_EXECUTED,
                        "transition_reason": "ok",
                        "snapshot_orchestration_status": ORCH_STATUS_ELIGIBLE,
                    },
                )
                with patch(
                    "argus.portfolio.progression.evaluate_product_orchestration",
                    side_effect=_ev_side_effect,
                ):
                    out = run_portfolio_progression(
                        root,
                        limit=3,
                        skip_import_failed=True,
                        skip_waiting=True,
                        write_artifacts=False,
                    )
            outcomes = {p["product_id"]: p["outcome"] for p in out["products"]}
            self.assertEqual(outcomes["imp_fail"], "skipped")
            self.assertEqual(outcomes["wait_blk"], "skipped")
            self.assertEqual(outcomes["ready"], "advanced")
            self.assertGreaterEqual(out["summary_counts"]["skipped"], 2)

    def test_queue_rebuild_when_latest_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            _minimal_product(root, "b")
            qdir = operator_queue_output_dir(root)
            qdir.mkdir(parents=True, exist_ok=True)
            payload, src = load_operator_queue_or_rebuild(root)
            self.assertEqual(src, "rebuilt")
            self.assertEqual(payload["schema"], OPERATOR_QUEUE_SCHEMA)

    def test_dry_run_does_not_call_advance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            write_operator_queue(root)
            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                out = run_portfolio_progression(root, limit=1, dry_run=True, write_artifacts=False)
                adv.assert_not_called()
            self.assertEqual(out["products"][0]["outcome"], "no_action")
            self.assertIn("dry_run", out["products"][0]["reason"].lower())

    def test_one_advance_per_product_limit(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for pid in ("x1", "x2", "x3"):
                _minimal_product(root, pid)
            write_operator_queue(root)
            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                adv.return_value = (MagicMock(), {"action_status": ACTION_STATUS_SKIPPED, "transition_reason": "t"})
                with patch("argus.portfolio.progression.evaluate_product_orchestration") as ev:
                    ev.return_value = {
                        "product_id": "x1",
                        "orchestration_status": ORCH_STATUS_ELIGIBLE,
                        "next_action": "none",
                        "import_health": {"first_pass_status": "success", "gating_tier": "success"},
                        "eligible_actions": [],
                        "waiting_inputs": [],
                    }
                    run_portfolio_progression(root, limit=2, dry_run=False, execute=False, write_artifacts=False)
                self.assertEqual(adv.call_count, 2)

    def test_skip_import_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "bad")
            write_operator_queue(root)
            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                with patch("argus.portfolio.progression.evaluate_product_orchestration") as ev:
                    ev.return_value = {
                        "product_id": "bad",
                        "orchestration_status": ORCH_STATUS_ELIGIBLE,
                        "next_action": "signals_collect",
                        "import_health": {"first_pass_status": "failed", "gating_tier": "failed"},
                        "eligible_actions": [{"action_id": "signals_collect", "reason": "r"}],
                        "waiting_inputs": [],
                    }
                    out = run_portfolio_progression(
                        root,
                        limit=1,
                        skip_import_failed=True,
                        write_artifacts=False,
                    )
                adv.assert_not_called()
            self.assertEqual(out["products"][0]["outcome"], "skipped")
            self.assertEqual(out["products"][0]["skipped_reason"], "import_failed")

    def test_skip_waiting(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "wait")
            write_operator_queue(root)
            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                with patch("argus.portfolio.progression.evaluate_product_orchestration") as ev:
                    ev.return_value = {
                        "product_id": "wait",
                        "orchestration_status": ORCH_STATUS_BLOCKED_WAITING_INPUT,
                        "next_action": "none",
                        "import_health": {"first_pass_status": "success"},
                        "eligible_actions": [],
                        "waiting_inputs": [{"kind": "x"}],
                    }
                    out = run_portfolio_progression(
                        root,
                        limit=1,
                        skip_waiting=True,
                        write_artifacts=False,
                    )
                adv.assert_not_called()
            self.assertEqual(out["products"][0]["outcome"], "skipped")
            self.assertEqual(out["products"][0]["skipped_reason"], "waiting_blocked")

    def test_stale_but_high_value_product_still_gets_one_advance(self) -> None:
        """advance_ready with observability debt still receives a single advance step."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "stale_hi")
            write_operator_queue(root)
            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                adv.return_value = (
                    MagicMock(),
                    {
                        "action_status": ACTION_STATUS_EXECUTED,
                        "transition_reason": "signals_collect",
                        "snapshot_orchestration_status": ORCH_STATUS_ELIGIBLE,
                    },
                )
                with patch("argus.portfolio.progression.evaluate_product_orchestration") as ev:
                    ev.return_value = {
                        "product_id": "stale_hi",
                        "orchestration_status": ORCH_STATUS_ELIGIBLE,
                        "next_action": "signals_collect",
                        "import_health": {"first_pass_status": "success", "gating_tier": "success"},
                        "eligible_actions": [{"action_id": "signals_collect", "reason": "stale"}],
                        "waiting_inputs": [],
                        "eligibility_facts": {"signals_collection_time_stale": True},
                    }
                    out = run_portfolio_progression(root, limit=1, write_artifacts=False)
            self.assertEqual(out["products"][0]["outcome"], "advanced")
            self.assertEqual(adv.call_count, 1)

    def test_writes_artifacts_and_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "z")
            write_operator_queue(root)
            with patch("argus.portfolio.progression.advance_orchestration") as adv:
                adv.return_value = (MagicMock(), {"action_status": ACTION_STATUS_SKIPPED, "transition_reason": "t"})
                with patch("argus.portfolio.progression.evaluate_product_orchestration") as ev:
                    ev.return_value = {
                        "product_id": "z",
                        "orchestration_status": ORCH_STATUS_ELIGIBLE,
                        "next_action": "none",
                        "import_health": {"first_pass_status": "success", "gating_tier": "success"},
                        "eligible_actions": [],
                        "waiting_inputs": [],
                    }
                    out = run_portfolio_progression(root, limit=1, dry_run=False, execute=False, write_artifacts=True)
            self.assertEqual(out["schema"], PORTFOLIO_PROGRESSION_SCHEMA)
            d = portfolio_progression_dir(root)
            self.assertTrue((d / "latest.json").is_file())
            self.assertTrue((d / "latest.md").is_file())
            self.assertTrue(any(p.name.endswith(".json") and p.name != "latest.json" for p in d.iterdir()))
            js = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(js["schema"], PORTFOLIO_PROGRESSION_SCHEMA)


if __name__ == "__main__":
    unittest.main()
