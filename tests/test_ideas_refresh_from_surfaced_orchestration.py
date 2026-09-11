"""``ideas_refresh_from_surfaced_findings`` orchestration tests."""

from __future__ import annotations

import json
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.core.serialize import dumps_json
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import list_experiments, save_experiment
from argus.findings.experiment_surfaced import (
    build_surfaced_findings,
    merged_findings_for_decisions,
    save_experiment_surfaced_bundle,
)
from argus.idea_generation.pipeline import latest_path
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
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
        [_idea_row("idea_ir", "t", product_id=pid)],
        now_iso=now_s,
    )


class TestIdeasRefreshFromSurfacedOrchestration(unittest.TestCase):
    def test_success_writes_ideas_with_merged_input_meta(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ir_ok"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_ir",
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
            self.assertTrue(st["eligibility_facts"].get("ideas_refresh_from_surfaced_findings_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)
            det = r.get("execution_detail") or {}
            self.assertEqual(det.get("schema"), "argus.ideas_bundle.v1")
            self.assertGreaterEqual(int(det.get("surfaced_findings_used_count") or 0), 1)
            lp = latest_path(root)
            self.assertTrue(lp.is_file())
            bundle = json.loads(lp.read_text(encoding="utf-8"))
            meta = bundle.get("meta") or {}
            ifi = meta.get("ideas_findings_input") or {}
            self.assertEqual(ifi.get("mode"), "merged_canonical_and_experiment_surfaced")
            self.assertGreaterEqual(int(ifi.get("surfaced_findings_used_count") or 0), 1)

    def test_merged_findings_used_in_pipeline_via_findings_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ir_merge"
            base = _orch_fresh_now()
            now_s = base.isoformat()
            _minimal_product(root, pid)
            _phase2_spine(root, pid, now_s)
            save_experiment(
                root,
                Experiment(
                    id="exp_ir2",
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
            with patch("argus.idea_generation.pipeline.generate_from_findings") as mock_gff:
                from argus.idea_generation.pipeline import run_pipeline

                mock_gff.return_value = []
                run_pipeline(
                    root,
                    pid,
                    advisor_expansion=False,
                    llm_idea_expansion=False,
                    max_advisor_expansion_ideas=0,
                    max_llm_expansion_ideas=0,
                    findings_rows=merged_findings_for_decisions(root, pid),
                )
                kwargs = mock_gff.call_args.kwargs
                self.assertIn("findings_rows", kwargs)
                rows = kwargs["findings_rows"]
                self.assertTrue(any(str(f.id).startswith("exp_surface:") for f in rows))

    def test_fails_without_canonical_findings_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ir_nofin"
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
            self.assertFalse(st["eligibility_facts"].get("ideas_refresh_from_surfaced_findings_eligible"))

    def test_fails_when_no_surfaced_findings(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ir_nosurf"
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
            self.assertFalse(st["eligibility_facts"].get("ideas_refresh_from_surfaced_findings_eligible"))
            r = execute_orchestration_action(root, pid, ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_FAILED)

    def test_not_queued_unhandled_on_success(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_ir_qu"
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
            r = execute_orchestration_action(root, pid, ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS)
            self.assertEqual(r.get("action_status"), ACTION_STATUS_EXECUTED)


if __name__ == "__main__":
    unittest.main()
