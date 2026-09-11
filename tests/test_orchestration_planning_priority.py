"""Planning soft priority for orchestration ``next_action`` (eligible actions only)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import dumps_json
from argus.orchestrator.state_models import (
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_FINDINGS_GENERATE,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_SIGNALS_COLLECT,
)
from argus.planning.orchestration_priority import (
    load_planning_snapshot_for_priority,
    pick_next_action_soft_planning,
)
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA, planning_latest_path


def _write_planning(root: Path, pid: str, *, mode: str) -> None:
    p = planning_latest_path(root, pid)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        dumps_json(
            {
                "schema": PLANNING_SNAPSHOT_SCHEMA,
                "schema_version": "1",
                "product_id": pid,
                "generated_at_utc": "2026-04-12T12:00:00+00:00",
                "source_strategy_generated_at_utc": "2026-04-12T12:00:00+00:00",
                "source_decisions_generated_at_utc": "2026-04-12T12:00:00+00:00",
                "posture": "explore",
                "planning_mode": mode,
                "priority_workstreams": [],
                "recommended_actions": [],
                "evidence": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )


class TestLoadPlanningSnapshotForPriority(unittest.TestCase):
    def test_missing_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertIsNone(load_planning_snapshot_for_priority(root, "p"))

    def test_invalid_schema(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = planning_latest_path(root, "p")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(dumps_json({"schema": "other", "product_id": "p", "planning_mode": "validate"}) + "\n")
            self.assertIsNone(load_planning_snapshot_for_priority(root, "p"))


class TestPickNextActionSoftPlanning(unittest.TestCase):
    def test_no_planning_canonical_head(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ordered = [ACTION_SIGNALS_COLLECT, ACTION_EXPERIMENTS_PROPOSE]
            na, meta = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_SIGNALS_COLLECT)
            self.assertFalse(meta.get("planning_priority_adjustment_applied"))
            self.assertIsNone(meta.get("planning_mode_considered"))

    def test_validate_prefers_experiment_over_signals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="validate")
            ordered = [ACTION_SIGNALS_COLLECT, ACTION_EXPERIMENTS_PROPOSE]
            na, meta = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_EXPERIMENTS_PROPOSE)
            self.assertTrue(meta.get("planning_priority_adjustment_applied"))
            self.assertEqual(meta.get("planning_mode_considered"), "validate")

    def test_expand_prefers_refinement_over_experiment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="expand")
            ordered = [ACTION_EXPERIMENTS_PROPOSE, ACTION_REFINEMENT_START_IDEA]
            na, meta = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_REFINEMENT_START_IDEA)
            self.assertTrue(meta.get("planning_priority_adjustment_applied"))
            self.assertEqual(meta.get("planning_mode_considered"), "expand")

    def test_redirect_prefers_reassessment_actions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="redirect")
            ordered = [ACTION_FINDINGS_GENERATE, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS]
            na, meta = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertTrue(meta.get("planning_priority_adjustment_applied"))
            self.assertEqual(meta.get("planning_mode_considered"), "redirect")

    def test_redirect_escalation_when_boosted(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="redirect")
            ordered = [ACTION_SIGNALS_COLLECT, ACTION_ESCALATION_PACKET_GENERATE]
            na, _ = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_ESCALATION_PACKET_GENERATE)

    def test_consolidate_prefers_impl_over_experiment(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="consolidate")
            ordered = [ACTION_EXPERIMENTS_PROPOSE, ACTION_IMPLEMENTATION_PLAN_GENERATE]
            na, meta = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_IMPLEMENTATION_PLAN_GENERATE)
            self.assertTrue(meta.get("planning_priority_adjustment_applied"))
            self.assertEqual(meta.get("planning_mode_considered"), "consolidate")

    def test_canonical_tie_equal_boost(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="validate")
            ordered = [ACTION_SIGNALS_COLLECT, ACTION_FINDINGS_GENERATE]
            na, meta = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertEqual(na, ACTION_SIGNALS_COLLECT)
            self.assertFalse(meta.get("planning_priority_adjustment_applied"))

    def test_never_returns_unknown_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_planning(root, "p", mode="validate")
            ordered = [ACTION_SIGNALS_COLLECT]
            na, _ = pick_next_action_soft_planning(ordered, repo_root=root, product_id="p")
            self.assertIn(na, ordered)

    def test_bad_planning_mode_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            p = planning_latest_path(root, "p")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    {
                        "schema": PLANNING_SNAPSHOT_SCHEMA,
                        "product_id": "p",
                        "planning_mode": "not_a_mode",
                        "generated_at_utc": "x",
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            na, meta = pick_next_action_soft_planning(
                [ACTION_SIGNALS_COLLECT, ACTION_EXPERIMENTS_PROPOSE],
                repo_root=root,
                product_id="p",
            )
            self.assertEqual(na, ACTION_SIGNALS_COLLECT)
            self.assertFalse(meta.get("planning_priority_adjustment_applied"))


if __name__ == "__main__":
    unittest.main()
