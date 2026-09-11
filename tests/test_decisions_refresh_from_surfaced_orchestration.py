"""``decisions_refresh_from_surfaced_findings`` orchestration tests."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.core.serialize import dumps_json
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import list_experiments, save_experiment
from argus.findings.experiment_surfaced import (
    build_surfaced_findings,
    merged_findings_for_decisions,
    save_experiment_surfaced_bundle,
)
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
)
from argus.orchestrator.step_executor import execute_orchestration_action
from tests.test_orchestration_state import _write
from tests.test_orchestration_step_executor import (
    _idea_row,
    _minimal_product,
    _orch_fresh_now,
    _write_ideas_latest_bundle,
)


def _phase2_spine(root: Path, pid: str, now_s: str) -> None:
    _write(
        root / "runs" / "signals" / "latest" / f"{pid}.json",
        {
            "schema": "argus.signal_collection.v1",
            "product_id": pid,
            "collected_at_utc": now_s,
            "records": [],
        },
    )
    _write(
        root / "runs" / "findings" / "latest" / f"{pid}.json",
        {
            "schema": "argus.findings_bundle.v1",
            "product_id": pid,
            "generated_at_utc": now_s,
            "repo_root": str(root),
            "finding_count": 0,
            "findings": [],
        },
    )
    _write(
        root / "runs" / "decisions" / "latest" / f"{pid}.json",
        {
            "schema": "argus.decisions_bundle.v1",
            "product_id": pid,
            "generated_at_utc": now_s,
            "repo_root": str(root),
            "decisions": [],
        },
    )
    _write_ideas_latest_bundle(
        root,
        pid,
        [_idea_row("idea_dr", "t", product_id=pid)],
        now_iso=now_s,
    )


class TestDecisionsRefreshFromSurfacedOrchestration(unittest.TestCase):
    def test_success_writes_decisions_with_merged_input(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dr_ok"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_dr",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            exps = list_experiments(root, product_id=pid)
            sf = build_surfaced_findings(exps, pid)
            self.assertTrue(sf)
            save_experiment_surfaced_bundle(root, pid, sf)
            p_surf = root / "runs/findings/experiment_surfaced/latest" / f"{pid}.json"
            raw = json.loads(p_surf.read_text(encoding="utf-8"))
            raw["generated_at_utc"] = (base + timedelta(hours=2)).isoformat()
            p_surf.write_text(dumps_json(raw) + "\n", encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue(st["eligibility_facts"].get("decisions_refresh_from_surfaced_findings_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.decisions_bundle.v1")
            self.assertGreaterEqual(int(det.get("surfaced_findings_used_count") or 0), 1)
            self.assertIn("assessment_latest_path", det)
            self.assertIn("authority_note", det)
            dp = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            self.assertTrue(dp.is_file())

    def test_merged_findings_used_via_merged_findings_for_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dr_merge"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_dr2",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            exps = list_experiments(root, product_id=pid)
            sf = build_surfaced_findings(exps, pid)
            self.assertTrue(sf)
            save_experiment_surfaced_bundle(root, pid, sf)
            p_surf = root / "runs/findings/experiment_surfaced/latest" / f"{pid}.json"
            raw = json.loads(p_surf.read_text(encoding="utf-8"))
            raw["generated_at_utc"] = (base + timedelta(hours=3)).isoformat()
            p_surf.write_text(dumps_json(raw) + "\n", encoding="utf-8")
            merged = merged_findings_for_decisions(root, pid)
            self.assertTrue(any(str(f.id).startswith("exp_surface:") for f in merged))

    def test_eligibility_false_when_surfaced_not_newer_than_decisions(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dr_stale"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_dr_stale",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            exps = list_experiments(root, product_id=pid)
            save_experiment_surfaced_bundle(root, pid, build_surfaced_findings(exps, pid))
            p_surf = root / "runs/findings/experiment_surfaced/latest" / f"{pid}.json"
            raw = json.loads(p_surf.read_text(encoding="utf-8"))
            raw["generated_at_utc"] = now_s
            p_surf.write_text(dumps_json(raw) + "\n", encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st["eligibility_facts"].get("decisions_refresh_from_surfaced_findings_eligible"))

    def test_fails_without_canonical_findings_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dr_nofin"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _write(
                root / "runs" / "signals" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.signal_collection.v1",
                    "product_id": pid,
                    "collected_at_utc": now_s,
                    "records": [],
                },
            )
            _write(
                root / "runs" / "decisions" / "latest" / f"{pid}.json",
                {
                    "schema": "argus.decisions_bundle.v1",
                    "product_id": pid,
                    "generated_at_utc": now_s,
                    "repo_root": str(root),
                    "decisions": [],
                },
            )
            _write_ideas_latest_bundle(root, pid, [_idea_row("x", "t", product_id=pid)], now_iso=now_s)
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st["eligibility_facts"].get("decisions_refresh_from_surfaced_findings_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_FAILED)

    def test_fails_when_no_surfaced_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dr_nosurf"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_prop_only",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st["eligibility_facts"].get("decisions_refresh_from_surfaced_findings_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_FAILED)

    def test_not_queued_unhandled_on_success(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_dr_qu"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_qu",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            exps = list_experiments(root, product_id=pid)
            save_experiment_surfaced_bundle(root, pid, build_surfaced_findings(exps, pid))
            p_surf = root / "runs/findings/experiment_surfaced/latest" / f"{pid}.json"
            raw = json.loads(p_surf.read_text(encoding="utf-8"))
            raw["generated_at_utc"] = (base + timedelta(hours=4)).isoformat()
            p_surf.write_text(dumps_json(raw) + "\n", encoding="utf-8")
            r = execute_orchestration_action(root, pid, ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)


if __name__ == "__main__":
    unittest.main()
