"""Tests for :mod:`argus.worker.work_orders` and CLI inspection."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.cli.worker_cmd import run_worker_subcommand
from argus.worker.work_orders import (
    WORK_ORDER_SCHEMA,
    create_work_order,
    find_work_order,
    load_latest_work_orders,
    new_work_order_id,
    work_orders_dir,
    write_work_order_artifacts,
)


class WorkOrderContractTests(unittest.TestCase):
    def test_schema_shape(self) -> None:
        wo = create_work_order(
            product_id="my-app",
            request_type="signal_instrumentation",
            selected_by="argus.core",
            rationale="Improve observability before optimization.",
            source_artifact_paths=["runs/products/signal_instrumentation/latest/my-app.json"],
            acceptance_criteria=["Manifest lists at least one temporal signal."],
            implementation_seed={"hint": "extend signals.yaml"},
            priority="high",
            autonomy_mode="supervised",
            approval_required=False,
            mission_context={"mission_id": "revenue"},
            risk_notes=["Read-only assessment only."],
            recommended_worker_mode="apply_patch",
            selected_at_utc="2026-04-14T12:00:00Z",
            work_order_id="wo_fixed_test_id",
        )
        self.assertEqual(wo["schema"], WORK_ORDER_SCHEMA)
        self.assertEqual(wo["work_order_id"], "wo_fixed_test_id")
        self.assertEqual(wo["product_id"], "my-app")
        self.assertEqual(wo["request_type"], "signal_instrumentation")
        self.assertEqual(wo["status"], "approved")
        self.assertFalse(wo["approval_required"])
        self.assertIsInstance(wo["implementation_seed"], dict)
        self.assertEqual(wo["mission_context"]["mission_id"], "revenue")

    def test_deterministic_work_order_id(self) -> None:
        w1 = new_work_order_id(
            selected_at_utc="2026-04-14T12:00:00Z",
            product_id="a",
            request_type="t",
        )
        w2 = new_work_order_id(
            selected_at_utc="2026-04-14T12:00:00Z",
            product_id="a",
            request_type="t",
        )
        self.assertEqual(w1, w2)

    def test_artifact_writing_and_latest_lookup(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="example_task",
                selected_by="test",
                rationale="r",
                source_artifact_paths=["runs/x.json"],
                acceptance_criteria=["c1"],
                approval_required=False,
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            wid = str(wo["work_order_id"])
            write_work_order_artifacts(root, wo)
            base = work_orders_dir(root)
            self.assertTrue((base / f"{wid}.json").is_file())
            self.assertTrue((base / f"{wid}.md").is_file())
            self.assertTrue((base / "latest" / "p1__example_task.json").is_file())

            loaded = load_latest_work_orders(root)
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["work_order_id"], wid)

    def test_find_by_id(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="test",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=False,
                work_order_id="wo_find_me",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            got = find_work_order(root, "wo_find_me")
            self.assertIsNotNone(got)
            assert got is not None
            self.assertEqual(got["product_id"], "p1")

    def test_load_latest_filters_product(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            for pid in ("a", "b"):
                wo = create_work_order(
                    product_id=pid,
                    request_type="task",
                    selected_by="t",
                    rationale="r",
                    source_artifact_paths=[],
                    acceptance_criteria=[],
                    approval_required=False,
                    selected_at_utc="2026-04-14T12:00:00Z",
                    work_order_id=f"wo_{pid}",
                )
                write_work_order_artifacts(root, wo)
            only_a = load_latest_work_orders(root, product_id="a")
            self.assertEqual(len(only_a), 1)
            self.assertEqual(only_a[0]["product_id"], "a")

    def test_cli_show_json_no_orders(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "argus").mkdir(exist_ok=True)  # unused; repo_root points to package parent
        # repo_root() resolves to argus parent — use patch
        with TemporaryDirectory() as td:
            root = Path(td)
            class Args:
                worker_command = "show-work-orders"
                json = True
                work_order_id = None
                product_id = None
                products_dir = None

            with patch("argus.cli.worker_cmd.repo_root", return_value=root):
                code = run_worker_subcommand(Args())
            self.assertEqual(code, 0)

    def test_pending_approval_default(self) -> None:
        wo = create_work_order(
            product_id="p",
            request_type="t",
            selected_by="core",
            rationale="r",
            source_artifact_paths=[],
            acceptance_criteria=[],
            approval_required=True,
            selected_at_utc="2026-04-14T12:00:00Z",
            work_order_id="wo_pending",
        )
        self.assertEqual(wo["status"], "pending_approval")

    def test_write_artifacts_without_latest(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            wo = create_work_order(
                product_id="p1",
                request_type="t",
                selected_by="test",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=False,
                work_order_id="wo_no_latest",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo, write_latest=False)
            base = work_orders_dir(root)
            self.assertTrue((base / "wo_no_latest.json").is_file())
            self.assertFalse((base / "latest").exists())

    def test_cli_show_single_missing(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)

            class Args:
                worker_command = "show-work-orders"
                json = False
                work_order_id = "wo_missing"
                product_id = None
                products_dir = None

            with patch("argus.cli.worker_cmd.repo_root", return_value=root):
                code = run_worker_subcommand(Args())
            self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
