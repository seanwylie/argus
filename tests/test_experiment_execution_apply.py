"""Link execution JSON files to experiments (status transitions)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.experiments.execution_apply import (
    apply_execution_outcomes,
    pending_unapplied_execution_outcomes_for_product,
)
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType
from argus.experiments.store import load_experiment, save_experiment


class TestExecutionApply(unittest.TestCase):
    def test_success_proposed_to_active_then_completed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = Experiment(
                id="exp_link_1",
                product_id="p1",
                hypothesis="h",
                type=ExperimentType.GROWTH,
                description="",
                expected_outcome="",
                status=ExperimentStatus.PROPOSED,
                confidence=0.5,
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            exdir = root / "runs" / "execution" / "p1"
            exdir.mkdir(parents=True)

            p1 = exdir / "a.json"
            p1.write_text(
                json.dumps(
                    {
                        "experiment_id": "exp_link_1",
                        "success": True,
                        "finished_at_utc": "2026-01-02T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            r1 = apply_execution_outcomes(root)
            self.assertEqual(r1.files_applied, 1)
            e1 = load_experiment(root, "exp_link_1")
            self.assertEqual(e1.status, ExperimentStatus.ACTIVE)
            self.assertEqual(e1.execution_failure_streak, 0)

            p2 = exdir / "b.json"
            p2.write_text(
                json.dumps(
                    {
                        "experiment_id": "exp_link_1",
                        "success": True,
                        "finished_at_utc": "2026-01-03T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            r2 = apply_execution_outcomes(root)
            self.assertEqual(r2.files_applied, 1)
            e2 = load_experiment(root, "exp_link_1")
            self.assertEqual(e2.status, ExperimentStatus.COMPLETED)
            self.assertIsNotNone(e2.end_at)

    def test_three_failures_to_failed(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = Experiment(
                id="exp_fail_1",
                product_id="p1",
                hypothesis="h",
                type=ExperimentType.GROWTH,
                description="",
                expected_outcome="",
                status=ExperimentStatus.PROPOSED,
                confidence=0.5,
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            exdir = root / "runs" / "execution" / "p1"
            exdir.mkdir(parents=True)
            for i in range(3):
                p = exdir / f"f{i}.json"
                p.write_text(
                    json.dumps(
                        {
                            "experiment_id": "exp_fail_1",
                            "success": False,
                            "error": "e",
                            "finished_at_utc": f"2026-01-0{i+1}T00:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            apply_execution_outcomes(root)
            e = load_experiment(root, "exp_fail_1")
            self.assertEqual(e.status, ExperimentStatus.FAILED)
            self.assertGreaterEqual(e.execution_failure_streak, 3)

    def test_action_id_resolves_via_map(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs" / "experiments").mkdir(parents=True)
            (root / "runs" / "experiments" / "action_to_experiment.json").write_text(
                json.dumps({"act_1": "exp_map_1"}),
                encoding="utf-8",
            )
            exp = Experiment(
                id="exp_map_1",
                product_id="p1",
                hypothesis="h",
                type=ExperimentType.GROWTH,
                description="",
                expected_outcome="",
                status=ExperimentStatus.PROPOSED,
                confidence=0.5,
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            exdir = root / "runs" / "execution" / "p1"
            exdir.mkdir(parents=True)
            exdir / "x.json"
            (exdir / "x.json").write_text(
                json.dumps(
                    {
                        "action_id": "act_1",
                        "success": True,
                        "finished_at_utc": "2026-01-02T00:00:00+00:00",
                    }
                ),
                encoding="utf-8",
            )
            apply_execution_outcomes(root)
            e = load_experiment(root, "exp_map_1")
            self.assertEqual(e.status, ExperimentStatus.ACTIVE)
            self.assertEqual(e.last_execution_action_id, "act_1")

    def test_product_scope_applies_only_that_products_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for eid in ("exp_a", "exp_b"):
                exp = Experiment(
                    id=eid,
                    product_id="p_a" if eid == "exp_a" else "p_b",
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at="2026-01-01T00:00:00+00:00",
                )
                save_experiment(root, exp)
            for pid in ("p_a", "p_b"):
                exdir = root / "runs" / "execution" / pid
                exdir.mkdir(parents=True)
                eid = "exp_a" if pid == "p_a" else "exp_b"
                (exdir / "run.json").write_text(
                    json.dumps(
                        {
                            "experiment_id": eid,
                            "success": True,
                            "finished_at_utc": "2026-01-02T00:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            r = apply_execution_outcomes(root, product_id="p_a")
            self.assertEqual(r.product_id, "p_a")
            self.assertEqual(r.files_seen, 1)
            self.assertEqual(r.files_applied, 1)
            self.assertEqual(load_experiment(root, "exp_a").status, ExperimentStatus.ACTIVE)
            self.assertEqual(load_experiment(root, "exp_b").status, ExperimentStatus.PROPOSED)

            r2 = apply_execution_outcomes(root, product_id="p_b")
            self.assertEqual(r2.product_id, "p_b")
            self.assertEqual(r2.files_applied, 1)
            self.assertEqual(load_experiment(root, "exp_b").status, ExperimentStatus.ACTIVE)

    def test_repo_wide_apply_processes_all_products(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for eid in ("exp_a2", "exp_b2"):
                exp = Experiment(
                    id=eid,
                    product_id="p_a" if eid == "exp_a2" else "p_b",
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at="2026-01-01T00:00:00+00:00",
                )
                save_experiment(root, exp)
            for pid in ("p_a", "p_b"):
                exdir = root / "runs" / "execution" / pid
                exdir.mkdir(parents=True)
                eid = "exp_a2" if pid == "p_a" else "exp_b2"
                (exdir / "run.json").write_text(
                    json.dumps(
                        {
                            "experiment_id": eid,
                            "success": True,
                            "finished_at_utc": "2026-01-02T00:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            r = apply_execution_outcomes(root)
            self.assertIsNone(r.product_id)
            self.assertEqual(r.files_applied, 2)
            self.assertEqual(load_experiment(root, "exp_a2").status, ExperimentStatus.ACTIVE)
            self.assertEqual(load_experiment(root, "exp_b2").status, ExperimentStatus.ACTIVE)

    def test_pending_detection_per_product_after_scoped_apply(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            exp = Experiment(
                id="exp_pend",
                product_id="p_a",
                hypothesis="h",
                type=ExperimentType.GROWTH,
                description="",
                expected_outcome="",
                status=ExperimentStatus.PROPOSED,
                confidence=0.5,
                created_at="2026-01-01T00:00:00+00:00",
            )
            save_experiment(root, exp)
            for pid in ("p_a", "p_b"):
                exdir = root / "runs" / "execution" / pid
                exdir.mkdir(parents=True)
                (exdir / "orphan.json").write_text(
                    json.dumps(
                        {
                            "experiment_id": "exp_pend" if pid == "p_a" else "exp_other",
                            "success": True,
                            "finished_at_utc": "2026-01-02T00:00:00+00:00",
                        }
                    ),
                    encoding="utf-8",
                )
            save_experiment(
                root,
                Experiment(
                    id="exp_other",
                    product_id="p_b",
                    hypothesis="h",
                    type=ExperimentType.GROWTH,
                    description="",
                    expected_outcome="",
                    status=ExperimentStatus.PROPOSED,
                    confidence=0.5,
                    created_at="2026-01-01T00:00:00+00:00",
                ),
            )
            self.assertTrue(pending_unapplied_execution_outcomes_for_product(root, "p_a"))
            self.assertTrue(pending_unapplied_execution_outcomes_for_product(root, "p_b"))
            apply_execution_outcomes(root, product_id="p_a")
            self.assertFalse(pending_unapplied_execution_outcomes_for_product(root, "p_a"))
            self.assertTrue(pending_unapplied_execution_outcomes_for_product(root, "p_b"))


if __name__ == "__main__":
    unittest.main()
