"""Tests for instrumentation → work order issuance."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.instrumentation_work_orders import (
    INSTRUMENTATION_WORK_ORDER_ISSUANCE_SCHEMA,
    evaluate_instrumentation_work_order_issuance,
    issue_instrumentation_work_orders,
    load_full_signal_instrumentation_payload,
)
from argus.products.signal_instrumentation import (
    evaluate_product_signal_instrumentation,
    run_product_signal_instrumentation,
)
from argus.worker.work_orders import WORK_ORDER_SCHEMA, find_work_order, work_orders_dir
from tests.test_product_signal_instrumentation import _minimal_product, _strong_product


class InstrumentationWorkOrderIssuanceTests(unittest.TestCase):
    def test_sparse_product_emits_work_order(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            run_product_signal_instrumentation(
                root, product_id="thin", write_artifacts=True
            )
            ev = evaluate_instrumentation_work_order_issuance(
                root,
                product_id="thin",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(ev["schema"], INSTRUMENTATION_WORK_ORDER_ISSUANCE_SCHEMA)
            self.assertEqual(len(ev["issued_work_orders"]), 1)
            wo = ev["issued_work_orders"][0]
            self.assertEqual(wo["schema"], WORK_ORDER_SCHEMA)
            self.assertEqual(wo["request_type"], "signal_instrumentation")
            self.assertEqual(wo["selected_by"], "argus_core")
            self.assertTrue(wo["approval_required"])
            self.assertIn("runs/products/signal_instrumentation/latest/thin.json", wo["source_artifact_paths"])

    def test_adequate_product_skipped(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _strong_product(root, "strong")
            run_product_signal_instrumentation(
                root, product_id="strong", write_artifacts=True
            )
            ev = evaluate_instrumentation_work_order_issuance(
                root,
                product_id="strong",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(ev["issued_work_orders"], [])
            self.assertTrue(any(s.get("reason") == "instrumentation_status_adequate" for s in ev["skipped"]))

    def test_mission_context_threaded(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _strong_product(root, "m1")
            # Downgrade assessment by replacing artifact with synthetic sparse payload would be complex;
            # use thin product + add mission_id to yaml
            pr = root / "products" / "thin2"
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: thin2
name: thin2
mission_id: revenue
owner:
  team: test
lifecycle:
  stage: validate
metrics:
  local_paths: [metrics/]
  primary: []
signals:
  - type: filesystem
    enabled: true
actions:
  analyze: "./scripts/a.sh"
constraints:
  max_monthly_cost_usd: 10
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "a.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (pr / "metrics").mkdir(parents=True)
            (pr / "metrics" / "one.txt").write_text("x", encoding="utf-8")
            run_product_signal_instrumentation(
                root, product_id="thin2", write_artifacts=True
            )
            ev = evaluate_instrumentation_work_order_issuance(
                root,
                product_id="thin2",
                selected_at_utc="2026-04-14T12:00:00Z",
            )
            self.assertEqual(len(ev["issued_work_orders"]), 1)
            mc = ev["issued_work_orders"][0].get("mission_context")
            self.assertIsInstance(mc, dict)
            self.assertEqual(mc.get("mission_id"), "revenue")

    def test_artifact_writing(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            run_product_signal_instrumentation(
                root, product_id="thin", write_artifacts=True
            )
            out = issue_instrumentation_work_orders(
                root,
                write_artifacts=True,
                product_id="thin",
            )
            woid = str(out["issued_work_orders"][0]["work_order_id"])
            self.assertTrue((work_orders_dir(root) / f"{woid}.json").is_file())
            self.assertTrue(
                (work_orders_dir(root) / "latest" / "thin__signal_instrumentation.json").is_file()
            )
            loaded = find_work_order(root, woid)
            self.assertIsNotNone(loaded)

    def test_focused_product_filter(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "a")
            _minimal_product(root, "b")
            for p in ("a", "b"):
                run_product_signal_instrumentation(
                    root, product_id=p, write_artifacts=True
                )
            ev = evaluate_instrumentation_work_order_issuance(
                root, product_id="a", selected_at_utc="2026-04-14T12:00:00Z"
            )
            self.assertEqual(len(ev["issued_work_orders"]), 1)
            self.assertEqual(ev["issued_work_orders"][0]["product_id"], "a")

    def test_no_save_no_files(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            run_product_signal_instrumentation(
                root, product_id="thin", write_artifacts=True
            )
            issue_instrumentation_work_orders(
                root, write_artifacts=False, product_id="thin"
            )
            self.assertFalse((root / "runs" / "worker").exists())

    def test_load_full_payload(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            _minimal_product(root, "thin")
            pl = evaluate_product_signal_instrumentation(root, "thin")
            from argus.products.signal_instrumentation import (
                write_product_signal_instrumentation_artifacts,
            )

            write_product_signal_instrumentation_artifacts(root, pl)
            full = load_full_signal_instrumentation_payload(root, "thin")
            self.assertIsNotNone(full)
            assert full is not None
            self.assertIn("missing_signal_dimensions", full)


if __name__ == "__main__":
    unittest.main()
