"""``strategy_refresh_from_decision_evolution`` orchestration + snapshot builder."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.serialize import dumps_json
from argus.findings.experiment_surfaced import EXPERIMENT_SURFACED_SCHEMA
from argus.findings.persistence import save_findings_bundle
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_STATUS_EXECUTED,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from argus.strategy.snapshot import (
    STRATEGY_SNAPSHOT_SCHEMA,
    build_strategy_snapshot,
    strategy_latest_path,
)
from tests.test_orchestration_step_executor import _minimal_product


def _decisions_bundle(
    pid: str,
    root: Path,
    *,
    gen_at: str,
    candidates: list[dict],
    evolution: dict | None,
) -> dict:
    b: dict = {
        "schema": "argus.decisions_bundle.v1",
        "product_id": pid,
        "generated_at_utc": gen_at,
        "repo_root": str(root),
        "lifecycle": {"product_id": pid, "stage": "idea", "scores": {}, "reasoning": {}, "kill_candidate": False},
        "candidates": candidates,
    }
    if evolution:
        b["decision_evolution"] = evolution
    return b


class TestStrategyRefreshOrchestration(unittest.TestCase):
    def test_dispatch_registered(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sr_disp"
            _minimal_product(root, pid)
            r = execute_orchestration_action(root, pid, ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION)
            self.assertNotEqual(r.get("action_status"), "queued_unhandled")

    def test_eligible_when_strategy_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sr_elig"
            _minimal_product(root, pid)
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[],
                        evolution=None,
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue(st["eligibility_facts"].get("strategy_refresh_from_decision_evolution_eligible"))

    def test_not_eligible_when_strategy_current(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sr_dup"
            _minimal_product(root, pid)
            dec_ts = "2026-04-12T12:00:00+00:00"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(dumps_json(_decisions_bundle(pid, root, gen_at=dec_ts, candidates=[], evolution=None)) + "\n", encoding="utf-8")
            sp = strategy_latest_path(root, pid)
            sp.parent.mkdir(parents=True, exist_ok=True)
            snap = build_strategy_snapshot(root, pid)
            sp.write_text(dumps_json(snap) + "\n", encoding="utf-8")
            # Re-read strategy: generated_at is "now" — should be >= decisions timestamp → not eligible
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st["eligibility_facts"].get("strategy_refresh_from_decision_evolution_eligible"))

    def test_eligible_when_decisions_newer_than_strategy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sr_newer"
            _minimal_product(root, pid)
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
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
            sp = strategy_latest_path(root, pid)
            sp.parent.mkdir(parents=True, exist_ok=True)
            old = {
                "schema": STRATEGY_SNAPSHOT_SCHEMA,
                "schema_version": "1",
                "product_id": pid,
                "generated_at_utc": "2026-04-12T10:00:00+00:00",
                "source_decisions_generated_at_utc": "2026-04-12T09:00:00+00:00",
                "summary": "x",
                "posture": "stabilize",
                "theme_signals": [],
                "recommended_mode": "hold",
                "evidence": {},
            }
            sp.write_text(dumps_json(old) + "\n", encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue(st["eligibility_facts"].get("strategy_refresh_from_decision_evolution_eligible"))

    def test_execute_writes_strategy_latest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sr_ok"
            _minimal_product(root, pid)
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
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
            r = execute_orchestration_action(root, pid, ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), STRATEGY_SNAPSHOT_SCHEMA)
            self.assertIn("strategy_latest_path", det)
            lp = root / str(det.get("strategy_latest_path") or "")
            self.assertTrue(lp.is_file())
            data = json.loads(lp.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), STRATEGY_SNAPSHOT_SCHEMA)
            self.assertEqual(data.get("product_id"), pid)
            self.assertIn("posture", data)
            self.assertIn("posture_raw", data)
            self.assertEqual(data.get("posture_raw"), data.get("posture"))
            self.assertIn("evidence", data)
            self.assertIn("posture_raw", det)
            self.assertIn("posture_final", det)
            self.assertIn("posture_changed", det)
            self.assertIn("history_used_count", det)


class TestStrategySnapshotPostures(unittest.TestCase):
    def test_posture_stabilize(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_post_stab"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {
                                "id": "a",
                                "action_type": "analyze",
                                "summary": "x",
                                "change_type": "unchanged",
                            },
                            {
                                "id": "b",
                                "action_type": "analyze",
                                "summary": "y",
                                "change_type": "unchanged",
                            },
                        ],
                        evolution={
                            "new_count": 0,
                            "unchanged_count": 2,
                            "modified_count": 0,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture"), "stabilize")

    def test_posture_pivot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_post_piv"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[],
                        evolution={
                            "new_count": 0,
                            "unchanged_count": 0,
                            "modified_count": 0,
                            "removed_count": 2,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture"), "pivot")

    def test_posture_explore(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_post_exp"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {"id": "a", "action_type": "analyze", "summary": "s", "change_type": "new"},
                            {"id": "b", "action_type": "analyze", "summary": "t", "change_type": "modified_content"},
                        ],
                        evolution={
                            "new_count": 1,
                            "unchanged_count": 0,
                            "modified_count": 1,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            sp = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(
                dumps_json(
                    {
                        "schema": EXPERIMENT_SURFACED_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "findings": [{"id": "x"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture"), "explore")

    def test_posture_double_down(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_post_dd"
            p = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                dumps_json(
                    _decisions_bundle(
                        pid,
                        root,
                        gen_at="2026-04-12T12:00:00+00:00",
                        candidates=[
                            {
                                "id": "a",
                                "action_type": "analyze",
                                "summary": "hello world",
                                "change_type": "new",
                            },
                            {
                                "id": "b",
                                "action_type": "analyze",
                                "summary": "hello world",
                                "change_type": "modified_content",
                            },
                            {"id": "c", "action_type": "stop", "summary": "z", "change_type": "unchanged"},
                        ],
                        evolution={
                            "new_count": 1,
                            "unchanged_count": 1,
                            "modified_count": 1,
                            "removed_count": 0,
                        },
                    )
                )
                + "\n",
                encoding="utf-8",
            )
            sp = root / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{pid}.json"
            sp.parent.mkdir(parents=True, exist_ok=True)
            sp.write_text(
                dumps_json(
                    {
                        "schema": EXPERIMENT_SURFACED_SCHEMA,
                        "product_id": pid,
                        "generated_at_utc": "2026-04-12T12:00:00+00:00",
                        "findings": [{"id": "1"}, {"id": "2"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            save_findings_bundle(
                root,
                pid,
                [
                    Finding(
                        id="canon_f1",
                        product_id=pid,
                        kind=FindingKind.GROWTH_OPPORTUNITY,
                        severity=SeverityLevel.MEDIUM,
                        effort=EffortBucket.SMALL,
                        title="Signal-backed",
                        summary="From canonical bundle",
                        recommendation="Track",
                        evidence={"source": "test"},
                    )
                ],
            )
            s = build_strategy_snapshot(root, pid)
            self.assertEqual(s.get("posture"), "double_down")
            self.assertFalse(s.get("skepticism_applied"))


if __name__ == "__main__":
    unittest.main()
