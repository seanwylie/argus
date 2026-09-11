"""Tests for work order action artifacts and effective status loading."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.worker.execute_work_order import execute_work_order
from argus.worker.work_order_actions import (
    WORK_ORDER_ACTION_SCHEMA,
    apply_work_order_action,
    compute_effective_status,
    load_actions_for_work_order,
    new_action_id,
    work_order_actions_dir,
)
from argus.worker.work_orders import (
    create_work_order,
    find_work_order,
    load_latest_work_order_for,
    write_work_order_artifacts,
)


class WorkOrderActionTests(unittest.TestCase):
    def test_new_action_id_deterministic(self) -> None:
        a = new_action_id(
            acted_at_utc="2026-04-14T12:00:00Z",
            work_order_id="wo_x",
            action_type="approve",
        )
        b = new_action_id(
            acted_at_utc="2026-04-14T12:00:00Z",
            work_order_id="wo_x",
            action_type="approve",
        )
        self.assertEqual(a, b)

    def test_approve_transition(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_ap",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(wo["status"], "pending_approval")
            write_work_order_artifacts(root, wo)
            rep = apply_work_order_action(
                root,
                work_order_id="wo_ap",
                action_type="approve",
                acted_by="alice",
                note="lgtm",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 0)
            act = rep["action"]
            assert act is not None
            self.assertEqual(act["schema"], WORK_ORDER_ACTION_SCHEMA)
            self.assertEqual(act["action_type"], "approve")
            self.assertEqual(act["previous_status"], "pending_approval")
            self.assertEqual(act["resulting_status"], "approved")
            self.assertEqual(act["acted_by"], "alice")
            self.assertTrue(str(rep.get("saved_path") or "").endswith(".json"))

    def test_reject_transition(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_rj",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            rep = apply_work_order_action(
                root,
                work_order_id="wo_rj",
                action_type="reject",
                note="no",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 0)
            assert rep["action"] is not None
            self.assertEqual(rep["action"]["resulting_status"], "rejected")

    def test_cancel_transition(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_ca",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            rep = apply_work_order_action(
                root,
                work_order_id="wo_ca",
                action_type="cancel",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 0)
            assert rep["action"] is not None
            self.assertEqual(rep["action"]["resulting_status"], "cancelled")

    def test_reopen_transition(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_ro",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            r1 = apply_work_order_action(
                root,
                work_order_id="wo_ro",
                action_type="reject",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            self.assertEqual(r1["exit_code"], 0)
            r2 = apply_work_order_action(
                root,
                work_order_id="wo_ro",
                action_type="reopen",
                acted_at_utc="2026-04-14T14:00:00Z",
            )
            self.assertEqual(r2["exit_code"], 0)
            assert r2["action"] is not None
            self.assertEqual(r2["action"]["resulting_status"], "pending_approval")

    def test_effective_status_from_actions(self) -> None:
        stamped = "pending_approval"
        actions = [
            {
                "schema": WORK_ORDER_ACTION_SCHEMA,
                "resulting_status": "approved",
            }
        ]
        self.assertEqual(compute_effective_status(stamped, actions), "approved")

    def test_find_work_order_overlays_status(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_ov",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            apply_work_order_action(
                root,
                work_order_id="wo_ov",
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            pl = find_work_order(root, "wo_ov")
            assert pl is not None
            self.assertEqual(pl["status_stamped"], "pending_approval")
            self.assertEqual(pl["status"], "approved")
            self.assertEqual(len(pl.get("action_history") or []), 1)

    def test_load_latest_merges_actions(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="lat",
                request_type="sig",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_lat",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            apply_work_order_action(
                root,
                work_order_id="wo_lat",
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            pl = load_latest_work_order_for(root, product_id="lat", request_type="sig")
            assert pl is not None
            self.assertEqual(pl["status"], "approved")

    def test_execution_after_approve(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            from argus.products.instrumentation_work_orders import (
                evaluate_instrumentation_work_order_issuance,
            )
            from argus.products.signal_instrumentation import run_product_signal_instrumentation
            from tests.test_product_signal_instrumentation import _minimal_product, _mission_config

            _mission_config(root)
            _minimal_product(root, "ex")
            run_product_signal_instrumentation(root, product_id="ex", write_artifacts=True)
            ev = evaluate_instrumentation_work_order_issuance(
                root,
                product_id="ex",
                selected_at_utc="2026-04-14T12:00:00Z",
                include_lifecycle_cross_check=False,
            )
            wo = ev["issued_work_orders"][0]
            woid = str(wo["work_order_id"])
            write_work_order_artifacts(root, wo)
            apply_work_order_action(
                root,
                work_order_id=woid,
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T14:00:00Z",
                save_artifacts=False,
            )
            self.assertEqual(rep["exit_code"], 0)

    def test_blocked_after_reject(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="ex",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_br",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            apply_work_order_action(
                root,
                work_order_id="wo_br",
                action_type="reject",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            rep = execute_work_order(
                root,
                work_order_id="wo_br",
                started_at_utc="2026-04-14T14:00:00Z",
                save_artifacts=False,
            )
            self.assertEqual(rep["exit_code"], 1)
            notes = str(rep["outcome"].get("notes") or "")
            self.assertIn("rejected", notes.lower())

    def test_blocked_after_cancel(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="ex",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_bc",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            apply_work_order_action(
                root,
                work_order_id="wo_bc",
                action_type="cancel",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            rep = execute_work_order(
                root,
                work_order_id="wo_bc",
                started_at_utc="2026-04-14T14:00:00Z",
                save_artifacts=False,
            )
            self.assertEqual(rep["exit_code"], 1)
            notes = str(rep["outcome"].get("notes") or "")
            self.assertIn("cancelled", notes.lower())

    def test_actions_persist_under_actions_dir(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_path",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            apply_work_order_action(
                root,
                work_order_id="wo_path",
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            base = work_order_actions_dir(root)
            self.assertTrue(base.is_dir())
            loaded = load_actions_for_work_order(root, "wo_path")
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["schema"], WORK_ORDER_ACTION_SCHEMA)

    def test_double_approve_rejected(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_dbl",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            r1 = apply_work_order_action(
                root,
                work_order_id="wo_dbl",
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            self.assertEqual(r1["exit_code"], 0)
            r2 = apply_work_order_action(
                root,
                work_order_id="wo_dbl",
                action_type="approve",
                acted_at_utc="2026-04-14T14:00:00Z",
            )
            self.assertEqual(r2["exit_code"], 1)
            self.assertIsNotNone(r2.get("error"))

    def test_no_save(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_ns",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            rep = apply_work_order_action(
                root,
                work_order_id="wo_ns",
                action_type="approve",
                save=False,
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 0)
            self.assertIsNone(rep.get("saved_path"))
            self.assertFalse(work_order_actions_dir(root).exists())


if __name__ == "__main__":
    unittest.main()
