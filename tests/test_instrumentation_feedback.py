"""Tests for :mod:`argus.products.instrumentation_feedback`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.apply_signal_instrumentation import PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA
from argus.products.instrumentation_feedback import (
    load_latest_signal_instrumentation_apply_by_product,
    refine_instrumentation_pressure_with_apply_context,
)
from argus.products.signal_instrumentation import PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA


def _inst_row(*, status: str) -> dict:
    return {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA,
        "product_id": "p",
        "ok": True,
        "instrumentation_status": status,
        "evaluated_at_utc": "2026-01-01T00:00:00Z",
    }


def _apply_row(
    *,
    apply_status: str,
    post_status: str,
) -> dict:
    return {
        "schema": PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA,
        "product_id": "p",
        "work_order_id": "wo",
        "execution_id": "ex",
        "applied_at_utc": "2026-01-01T00:00:00Z",
        "apply_status": apply_status,
        "post_apply": {
            "instrumentation_status": post_status,
            "ok": post_status == "adequate",
            "missing_signal_dimensions": [],
        },
        "files_written": [],
        "files_updated": [],
    }


class InstrumentationFeedbackTests(unittest.TestCase):
    def test_refine_success_adequate_drops_effective_pressure(self) -> None:
        inst = {"p": _inst_row(status="weak")}
        apply = {"p": _apply_row(apply_status="success", post_status="adequate")}
        r = refine_instrumentation_pressure_with_apply_context(inst_by_product=inst, apply_by_product=apply)
        self.assertEqual(r["products_under_instrumentation_pressure_raw"], ["p"])
        self.assertEqual(r["products_under_instrumentation_pressure_effective"], [])
        self.assertEqual(r["products_instrumentation_resolved_via_apply"], ["p"])
        self.assertEqual(r["products_instrumentation_apply_followup"], [])

    def test_refine_partial_stays_under_pressure(self) -> None:
        inst = {"p": _inst_row(status="sparse")}
        apply = {"p": _apply_row(apply_status="partial", post_status="weak")}
        r = refine_instrumentation_pressure_with_apply_context(inst_by_product=inst, apply_by_product=apply)
        self.assertEqual(r["products_under_instrumentation_pressure_effective"], ["p"])
        self.assertIn("p", r["products_instrumentation_apply_followup"])

    def test_refine_success_non_adequate_followup(self) -> None:
        inst = {"p": _inst_row(status="missing")}
        apply = {"p": _apply_row(apply_status="success", post_status="weak")}
        r = refine_instrumentation_pressure_with_apply_context(inst_by_product=inst, apply_by_product=apply)
        self.assertEqual(r["products_under_instrumentation_pressure_effective"], ["p"])
        self.assertIn("p", r["products_instrumentation_apply_followup"])
        self.assertEqual(r["products_instrumentation_resolved_via_apply"], [])

    def test_load_apply_latest_graceful(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            self.assertEqual(load_latest_signal_instrumentation_apply_by_product(root), {})

    def test_load_apply_latest_reads_file(self) -> None:
        with TemporaryDirectory() as td:
            root = Path(td)
            d = root / "runs" / "products" / "signal_instrumentation_apply" / "latest"
            d.mkdir(parents=True)
            body = _apply_row(apply_status="success", post_status="adequate")
            body["product_id"] = "x"
            (d / "x.json").write_text(json.dumps(body), encoding="utf-8")
            m = load_latest_signal_instrumentation_apply_by_product(root)
            self.assertIn("x", m)
            self.assertEqual(m["x"]["apply_status"], "success")


if __name__ == "__main__":
    unittest.main()
