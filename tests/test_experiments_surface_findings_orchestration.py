"""``experiments_surface_findings`` orchestration: sidecar artifact, eligibility, failures."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import save_experiment
from argus.findings.experiment_surfaced import (
    EXPERIMENT_SURFACED_SCHEMA,
    EXPERIMENT_SURFACING_RULE_ID,
    experiment_surfaced_latest_path,
)
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
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
        [_idea_row("idea_sf", "t", product_id=pid)],
        now_iso=now_s,
    )


class TestExperimentsSurfaceFindingsOrchestration(unittest.TestCase):
    def test_success_writes_sidecar_and_provenance(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sf_ok"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_sf_a",
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
            st = evaluate_product_orchestration(root, pid)
            self.assertTrue(st["eligibility_facts"].get("experiments_surface_findings_eligible"))

            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), EXPERIMENT_SURFACED_SCHEMA)
            self.assertEqual(det.get("surfacing_rule"), EXPERIMENT_SURFACING_RULE_ID)
            self.assertEqual(det.get("surfaced_finding_count"), 1)
            self.assertEqual(det.get("affected_experiment_ids"), ["exp_sf_a"])
            path = experiment_surfaced_latest_path(root, pid)
            self.assertTrue(path.is_file())
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), EXPERIMENT_SURFACED_SCHEMA)
            self.assertEqual(data.get("surfacing_rule"), EXPERIMENT_SURFACING_RULE_ID)
            f0 = (data.get("findings") or [])[0]
            self.assertEqual(f0.get("id"), f"exp_surface:{pid}:exp_sf_a")
            ev = f0.get("evidence") or {}
            self.assertEqual(ev.get("provenance"), "experiment_surfaced")
            self.assertIs(ev.get("synthetic"), True)
            self.assertEqual(ev.get("surfacing_rule"), EXPERIMENT_SURFACING_RULE_ID)

    def test_rerun_same_finding_ids_no_duplicates(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sf_dedupe"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_sf_b",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=now_s,
                    end_at=None,
                    status=ExperimentStatus.FAILED,
                    confidence=0.5,
                    created_at=now_s,
                ),
            )
            r1 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r1.get("action_status"), ACTION_STATUS_EXECUTED)
            path = experiment_surfaced_latest_path(root, pid)
            data1 = json.loads(path.read_text(encoding="utf-8"))
            ids1 = [x.get("id") for x in (data1.get("findings") or [])]
            r2 = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r2.get("action_status"), ACTION_STATUS_EXECUTED)
            data2 = json.loads(path.read_text(encoding="utf-8"))
            ids2 = [x.get("id") for x in (data2.get("findings") or [])]
            self.assertEqual(ids1, ids2)
            self.assertEqual(len(ids1), 1)

    def test_fails_when_no_experiments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sf_noexp"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_FAILED)
            self.assertIn("experiments_surface_findings", (r.get("execution_error") or ""))

    def test_fails_when_no_usable_outcome(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sf_noverdict"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_sf_c",
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
                    last_evaluation_verdict=None,
                ),
            )
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_FAILED)
            err = (r.get("execution_error") or "").lower()
            self.assertTrue(
                "usable outcome" in err or "outcome evidence" in err or "experiments_surface_findings_eligible" in err
            )

    def test_eligibility_fact_false_without_spine(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_sf_gate"
            _minimal_product(root, pid)
            _ts = _orch_fresh_now().isoformat()
            save_experiment(
                root,
                Experiment(
                    id="exp_sf_d",
                    product_id=pid,
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    success_metrics=[],
                    start_at=_ts,
                    end_at=None,
                    status=ExperimentStatus.COMPLETED,
                    confidence=0.5,
                    created_at=_ts,
                ),
            )
            st = evaluate_product_orchestration(root, pid)
            self.assertFalse(st["eligibility_facts"].get("experiments_surface_findings_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_EXPERIMENTS_SURFACE_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_FAILED)


if __name__ == "__main__":
    unittest.main()
