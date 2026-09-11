"""Tests for worker execution (argus.worker_execution_outcome.v1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.cli.worker_cmd import run_worker_subcommand
from argus.products.instrumentation_work_orders import evaluate_instrumentation_work_order_issuance
from argus.products.signal_instrumentation import run_product_signal_instrumentation
from argus.worker.execute_work_order import (
    REQUEST_TYPE_SIGNAL_INSTRUMENTATION,
    WORKER_EXECUTION_OUTCOME_SCHEMA,
    execute_work_order,
    executions_dir,
    executions_latest_dir,
    new_execution_id,
    resolve_work_order_for_execution,
)
from argus.worker.work_order_actions import apply_work_order_action
from argus.worker.work_orders import (
    create_work_order,
    write_work_order_artifacts,
)
from tests.test_product_signal_instrumentation import _minimal_product, _mission_config


def _prepare_signal_wo(root: Path, product_id: str) -> str:
    _mission_config(root)
    _minimal_product(root, product_id)
    run_product_signal_instrumentation(root, product_id=product_id, write_artifacts=True)
    ev = evaluate_instrumentation_work_order_issuance(
        root,
        product_id=product_id,
        selected_at_utc="2026-04-14T12:00:00Z",
        include_lifecycle_cross_check=False,
    )
    wos = ev.get("issued_work_orders") or []
    assert len(wos) == 1, ev.get("skipped")
    wo = wos[0]
    write_work_order_artifacts(root, wo)
    return str(wo["work_order_id"])


class ExecuteWorkOrderTests(unittest.TestCase):
    def test_deterministic_execution_id(self) -> None:
        e1 = new_execution_id(
            started_at_utc="2026-04-14T12:00:00Z",
            work_order_id="wo_stable",
        )
        e2 = new_execution_id(
            started_at_utc="2026-04-14T12:00:00Z",
            work_order_id="wo_stable",
        )
        self.assertEqual(e1, e2)
        self.assertTrue(e1.startswith("ex_20260414120000_"))

    def test_approved_intake_writes_artifacts(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            woid = _prepare_signal_wo(root, "p_exec")
            apply_work_order_action(
                root,
                work_order_id=woid,
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T12:00:00Z",
                save_artifacts=True,
            )
            self.assertEqual(rep["exit_code"], 0)
            oc = rep["outcome"]
            self.assertEqual(oc["schema"], WORKER_EXECUTION_OUTCOME_SCHEMA)
            self.assertEqual(oc["work_order_id"], woid)
            self.assertEqual(oc["product_id"], "p_exec")
            self.assertEqual(oc["request_type"], REQUEST_TYPE_SIGNAL_INSTRUMENTATION)
            self.assertIn(oc["execution_status"], ("success", "partial"))
            self.assertEqual(oc["worker_mode"], "signal_instrumentation_apply")
            self.assertIn("instrumentation_apply", oc)
            self.assertEqual(oc["source_work_order_path"], f"runs/worker/work_orders/{woid}.json")
            self.assertIsInstance(oc["files_touched"], list)
            eid = str(oc["execution_id"])
            self.assertTrue((executions_dir(root) / f"{eid}.json").is_file())
            self.assertTrue((executions_dir(root) / f"{eid}.md").is_file())
            safe = "signal_instrumentation"
            self.assertTrue(
                (executions_latest_dir(root) / f"p_exec__{safe}.json").is_file()
            )

    def test_pending_approval_blocked(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p",
                request_type="t",
                selected_by="core",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=True,
                work_order_id="wo_pend",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(wo["status"], "pending_approval")
            write_work_order_artifacts(root, wo)
            rep = execute_work_order(
                root,
                work_order_id="wo_pend",
                started_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 1)
            self.assertEqual(rep["outcome"]["execution_status"], "blocked")

    def test_allow_pending_approval(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            woid = _prepare_signal_wo(root, "p_allow")
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T12:00:00Z",
                allow_pending_approval=True,
            )
            self.assertEqual(rep["exit_code"], 0)
            self.assertIn("allow_pending_approval", rep["outcome"]["notes"])

    def test_missing_work_order(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            rep = execute_work_order(
                root,
                work_order_id="wo_nope",
                started_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 1)
            self.assertIsNotNone(rep.get("error"))
            self.assertEqual(rep["outcome"]["execution_status"], "blocked")

    def test_no_save(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            woid = _prepare_signal_wo(root, "p_ns")
            apply_work_order_action(
                root,
                work_order_id=woid,
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T12:00:00Z",
                save_artifacts=False,
            )
            self.assertEqual(rep["exit_code"], 0)
            self.assertEqual(rep["saved_paths"], {})
            self.assertFalse(executions_dir(root).exists())

    def test_resolve_latest_by_product_request(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            woid = _prepare_signal_wo(root, "platest")
            apply_work_order_action(
                root,
                work_order_id=woid,
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            pl, stamped, err = resolve_work_order_for_execution(
                root, product_id="platest", request_type=REQUEST_TYPE_SIGNAL_INSTRUMENTATION
            )
            self.assertIsNone(err)
            assert pl is not None
            self.assertEqual(pl["work_order_id"], woid)
            assert stamped is not None
            rep = execute_work_order(
                root,
                product_id="platest",
                request_type=REQUEST_TYPE_SIGNAL_INSTRUMENTATION,
                started_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(rep["exit_code"], 0)
            self.assertEqual(rep["outcome"]["work_order_id"], woid)

    def test_resolve_conflict(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            pl, _, err = resolve_work_order_for_execution(
                root, work_order_id="a", product_id="b", request_type="c"
            )
            self.assertIsNone(pl)
            self.assertIsNotNone(err)
            self.assertIn("either", str(err))

    def test_outcome_json_roundtrip(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            woid = _prepare_signal_wo(root, "pj")
            apply_work_order_action(
                root,
                work_order_id=woid,
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T12:00:00Z",
            )
            oc = rep["outcome"]
            raw = json.loads(json.dumps(oc))
            for k in (
                "schema",
                "execution_id",
                "work_order_id",
                "product_id",
                "request_type",
                "started_at_utc",
                "finished_at_utc",
                "worker_mode",
                "execution_status",
                "implementation_plan_summary",
                "implementation_spec_summary",
                "files_touched",
                "validation_summary",
                "notes",
                "source_work_order_path",
            ):
                self.assertIn(k, raw)

    def test_cli_execute_json(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            woid = _prepare_signal_wo(root, "cli_p")
            apply_work_order_action(
                root,
                work_order_id=woid,
                action_type="approve",
                acted_at_utc="2026-04-14T13:00:00Z",
            )

            class Args:
                worker_command = "execute-work-order"
                json = True
                work_order_id = woid
                product_id = None
                request_type = None
                allow_pending_approval = False
                no_save = True
                products_dir = None

            with patch("argus.cli.worker_cmd.repo_root", return_value=root):
                code = run_worker_subcommand(Args())
            self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
