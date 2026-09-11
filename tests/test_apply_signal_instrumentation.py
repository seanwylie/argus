"""Tests for deterministic signal instrumentation apply (worker builder)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.apply_signal_instrumentation import (
    PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA,
    apply_signal_instrumentation,
    signal_instrumentation_apply_dir,
    signal_instrumentation_apply_latest_dir,
)
from argus.products.instrumentation_work_orders import evaluate_instrumentation_work_order_issuance
from argus.products.signal_instrumentation import run_product_signal_instrumentation
from argus.worker.execute_work_order import REQUEST_TYPE_SIGNAL_INSTRUMENTATION, execute_work_order
from argus.worker.work_order_actions import apply_work_order_action
from argus.worker.work_orders import write_work_order_artifacts
from tests.test_product_signal_instrumentation import _minimal_product, _mission_config


def _issue_and_write_wo(
    root: Path,
    *,
    product_id: str,
    include_lifecycle_cross_check: bool = False,
) -> str:
    ev = evaluate_instrumentation_work_order_issuance(
        root,
        product_id=product_id,
        selected_at_utc="2026-04-14T12:00:00Z",
        include_lifecycle_cross_check=include_lifecycle_cross_check,
    )
    wos = ev.get("issued_work_orders") or []
    assert len(wos) == 1, ev.get("skipped")
    wo = wos[0]
    write_work_order_artifacts(root, wo)
    return str(wo["work_order_id"])


class ApplySignalInstrumentationTests(unittest.TestCase):
    def test_approved_apply_writes_signals_yaml_and_artifact(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "thin")
            run_product_signal_instrumentation(root, product_id="thin", write_artifacts=True)
            woid = _issue_and_write_wo(root, product_id="thin")
            apply_work_order_action(root, work_order_id=woid, action_type="approve", acted_at_utc="2026-04-14T13:00:00Z")
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T14:00:00Z",
                save_artifacts=True,
            )
            self.assertEqual(rep["exit_code"], 0)
            oc = rep["outcome"]
            self.assertEqual(oc["request_type"], REQUEST_TYPE_SIGNAL_INSTRUMENTATION)
            self.assertEqual(oc["worker_mode"], "signal_instrumentation_apply")
            self.assertIn("instrumentation_apply", oc)
            iap = oc["instrumentation_apply"]
            assert isinstance(iap, dict)
            self.assertEqual(iap["schema"], PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA)
            self.assertIn("thin", str(root / "products" / "thin" / "signals.yaml"))
            self.assertTrue((root / "products" / "thin" / "signals.yaml").is_file())
            self.assertTrue(signal_instrumentation_apply_latest_dir(root).joinpath("thin.json").is_file())

    def test_pending_still_blocked_without_override(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "p1")
            run_product_signal_instrumentation(root, product_id="p1", write_artifacts=True)
            woid = _issue_and_write_wo(root, product_id="p1")
            rep = execute_work_order(root, work_order_id=woid, started_at_utc="2026-04-14T12:00:00Z")
            self.assertEqual(rep["exit_code"], 1)
            self.assertEqual(rep["outcome"]["execution_status"], "blocked")

    def test_rejected_blocked(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "p2")
            run_product_signal_instrumentation(root, product_id="p2", write_artifacts=True)
            woid = _issue_and_write_wo(root, product_id="p2")
            apply_work_order_action(root, work_order_id=woid, action_type="reject", acted_at_utc="2026-04-14T13:00:00Z")
            rep = execute_work_order(root, work_order_id=woid, started_at_utc="2026-04-14T14:00:00Z")
            self.assertEqual(rep["exit_code"], 1)

    def test_no_save_skips_product_and_apply_artifacts(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "p3")
            run_product_signal_instrumentation(root, product_id="p3", write_artifacts=True)
            woid = _issue_and_write_wo(root, product_id="p3")
            apply_work_order_action(root, work_order_id=woid, action_type="approve", acted_at_utc="2026-04-14T13:00:00Z")
            rep = execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T14:00:00Z",
                save_artifacts=False,
            )
            self.assertEqual(rep["exit_code"], 0)
            self.assertFalse((root / "products" / "p3" / "signals.yaml").exists())
            self.assertFalse(signal_instrumentation_apply_dir(root).exists())

    def test_unsupported_request_type_blocked(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "p4")
            from argus.worker.work_orders import create_work_order

            wo = create_work_order(
                product_id="p4",
                request_type="custom_task",
                selected_by="t",
                rationale="r",
                source_artifact_paths=[],
                acceptance_criteria=[],
                approval_required=False,
                work_order_id="wo_x",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            write_work_order_artifacts(root, wo)
            rep = execute_work_order(root, work_order_id="wo_x", started_at_utc="2026-04-14T12:00:00Z")
            self.assertEqual(rep["exit_code"], 1)
            self.assertEqual(rep["outcome"]["execution_status"], "blocked")
            self.assertIn("no real builder", rep["outcome"]["notes"])

    def test_signals_yaml_content_deterministic(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "p5")
            run_product_signal_instrumentation(root, product_id="p5", write_artifacts=True)
            woid = _issue_and_write_wo(root, product_id="p5")
            apply_work_order_action(root, work_order_id=woid, action_type="approve", acted_at_utc="2026-04-14T13:00:00Z")
            execute_work_order(
                root,
                work_order_id=woid,
                started_at_utc="2026-04-14T14:00:00Z",
                save_artifacts=True,
            )
            text = (root / "products" / "p5" / "signals.yaml").read_text(encoding="utf-8")
            self.assertIn("argus.product_signal_manifest.v1", text)
            self.assertIn("argus_apply_", text)

    def test_apply_module_direct_no_save(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _mission_config(root)
            _minimal_product(root, "p6")
            run_product_signal_instrumentation(root, product_id="p6", write_artifacts=True)
            woid = _issue_and_write_wo(root, product_id="p6")
            apply_work_order_action(root, work_order_id=woid, action_type="approve", acted_at_utc="2026-04-14T13:00:00Z")
            from argus.worker.work_orders import find_work_order

            wo = find_work_order(root, woid)
            assert wo is not None
            r = apply_signal_instrumentation(
                root,
                work_order=wo,
                execution_id="ex_test",
                save=False,
            )
            self.assertEqual(r["apply_status"], "blocked")
            self.assertFalse((root / "products" / "p6" / "signals.yaml").exists())


if __name__ == "__main__":
    unittest.main()
