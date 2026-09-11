"""``planning_refresh_from_strategy`` orchestration + planning snapshot builder."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import dumps_json
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_STATUS_EXECUTED,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from argus.planning.snapshot import (
    PLANNING_SNAPSHOT_SCHEMA,
    build_planning_snapshot,
    planning_latest_path,
)
from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA
from tests.test_orchestration_step_executor import _minimal_product
from tests.test_strategy_refresh_orchestration import _decisions_bundle


def _strategy_bundle(
    pid: str,
    *,
    gen_at: str,
    posture: str,
    theme_signals: list[dict] | None = None,
) -> dict:
    return {
        "schema": STRATEGY_SNAPSHOT_SCHEMA,
        "schema_version": "1",
        "product_id": pid,
        "generated_at_utc": gen_at,
        "source_decisions_generated_at_utc": gen_at,
        "summary": f"posture={posture}",
        "posture": posture,
        "theme_signals": theme_signals or [],
        "recommended_mode": "x",
        "evidence": {},
    }


class TestPlanningRefreshOrchestration(unittest.TestCase):
    def test_dispatch_registered(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pr_disp"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_PLANNING_REFRESH_FROM_STRATEGY)
            self.assertNotEqual(r.get("action_status"), "queued_unhandled")

    def test_eligible_when_planning_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pr_elig"
            _minimal_product(root, pid)
            ts = "2026-04-12T12:00:00+00:00"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(_decisions_bundle(pid, root, gen_at=ts, candidates=[], evolution=None)) + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at=ts, posture="stabilize")) + "\n",
                encoding="utf-8",
            )
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue(st["eligibility_facts"].get("planning_refresh_from_strategy_eligible"))

    def test_not_eligible_when_planning_current(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pr_dup"
            _minimal_product(root, pid)
            ts = "2026-04-12T12:00:00+00:00"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(_decisions_bundle(pid, root, gen_at=ts, candidates=[], evolution=None)) + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at=ts, posture="stabilize")) + "\n",
                encoding="utf-8",
            )
            snap = build_planning_snapshot(root, pid)
            pp = planning_latest_path(root, pid)
            pp.parent.mkdir(parents=True, exist_ok=True)
            pp.write_text(dumps_json(snap) + "\n", encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st["eligibility_facts"].get("planning_refresh_from_strategy_eligible"))

    def test_eligible_when_decisions_newer_than_planning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pr_fresh"
            _minimal_product(root, pid)
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T18:00:00+00:00",
                        candidates=[],
                        evolution=None,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at="2026-04-12T12:00:00+00:00", posture="explore")) + "\n",
                encoding="utf-8",
            )
            old_plan = {
                "schema": PLANNING_SNAPSHOT_SCHEMA,
                "schema_version": "1",
                "product_id": pid,
                "generated_at_utc": "2026-04-12T10:00:00+00:00",
                "source_strategy_generated_at_utc": "2026-04-12T09:00:00+00:00",
                "source_decisions_generated_at_utc": "2026-04-12T09:00:00+00:00",
                "posture": "explore",
                "planning_mode": "validate",
                "priority_workstreams": [],
                "recommended_actions": [],
                "evidence": {},
            }
            pp = planning_latest_path(root, pid)
            pp.parent.mkdir(parents=True, exist_ok=True)
            pp.write_text(dumps_json(old_plan) + "\n", encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue(st["eligibility_facts"].get("planning_refresh_from_strategy_eligible"))

    def test_execute_writes_planning_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pr_ok"
            _minimal_product(root, pid)
            ts = "2026-04-12T12:00:00+00:00"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at=ts,
                        candidates=[
                            {
                                "id": "c1",
                                "product_id": pid,
                                "action_type": "analyze",
                                "summary": "hello world",
                                "change_type": "new",
                            }
                        ],
                        evolution={"new_count": 1, "unchanged_count": 0, "modified_count": 0, "removed_count": 0},
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(
                    _strategy_bundle(
                        pid,
                        gen_at=ts,
                        posture="explore",
                        theme_signals=[{"theme": "onboarding", "signal": "strengthening", "basis": "x"}],
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            r = execute_orchestration_action(root, pid, ACTION_PLANNING_REFRESH_FROM_STRATEGY)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), PLANNING_SNAPSHOT_SCHEMA)
            self.assertIn("planning_latest_path", det)
            lp = root / str(det.get("planning_latest_path") or "")
            self.assertTrue(lp.is_file())
            data = json.loads(lp.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), PLANNING_SNAPSHOT_SCHEMA)
            self.assertEqual(data.get("product_id"), pid)
            self.assertEqual(data.get("planning_mode"), "validate")
            self.assertIn("priority_workstreams", data)
            self.assertIn("recommended_actions", data)
            for row in data.get("recommended_actions") or []:
                self.assertIn("action_type", row)
                self.assertIn("title", row)
                self.assertIn("reason", row)
                self.assertIn("priority", row)
            self.assertEqual(det.get("recommended_action_count"), len(data.get("recommended_actions") or []))


class TestPlanningModeMapping(unittest.TestCase):
    def test_double_down_expand(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pm_dd"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(_decisions_bundle(pid, root, gen_at="2026-04-12T12:00:00+00:00", candidates=[], evolution=None))
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at="2026-04-12T12:00:00+00:00", posture="double_down")) + "\n",
                encoding="utf-8",
            )
            s = build_planning_snapshot(root, pid)
            self.assertEqual(s.get("planning_mode"), "expand")

    def test_explore_validate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pm_ex"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(_decisions_bundle(pid, root, gen_at="2026-04-12T12:00:00+00:00", candidates=[], evolution=None))
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at="2026-04-12T12:00:00+00:00", posture="explore")) + "\n",
                encoding="utf-8",
            )
            s = build_planning_snapshot(root, pid)
            self.assertEqual(s.get("planning_mode"), "validate")

    def test_pivot_redirect(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pm_pv"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(_decisions_bundle(pid, root, gen_at="2026-04-12T12:00:00+00:00", candidates=[], evolution=None))
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at="2026-04-12T12:00:00+00:00", posture="pivot")) + "\n",
                encoding="utf-8",
            )
            s = build_planning_snapshot(root, pid)
            self.assertEqual(s.get("planning_mode"), "redirect")

    def test_stabilize_consolidate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_pm_st"
            (root / "runs" / "decisions" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "decisions" / "latest" / f"{pid}.json").write_text(
                dumps_json(_decisions_bundle(pid, root, gen_at="2026-04-12T12:00:00+00:00", candidates=[], evolution=None))
                + "\n",
                encoding="utf-8",
            )
            (root / "runs" / "strategy" / "latest").mkdir(parents=True, exist_ok=True)
            (root / "runs" / "strategy" / "latest" / f"{pid}.json").write_text(
                dumps_json(_strategy_bundle(pid, gen_at="2026-04-12T12:00:00+00:00", posture="stabilize")) + "\n",
                encoding="utf-8",
            )
            s = build_planning_snapshot(root, pid)
            self.assertEqual(s.get("planning_mode"), "consolidate")


if __name__ == "__main__":
    unittest.main()
