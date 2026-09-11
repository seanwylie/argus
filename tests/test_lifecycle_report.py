"""Tests for kill justification report builder."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.lifecycle.report import (
    build_kill_justification_report,
    render_kill_justification_markdown,
)
from argus.products.scaffold import create_product_scaffold


class TestKillJustificationReport(unittest.TestCase):
    def test_report_shape_and_markdown(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            code, msg, _ = create_product_scaffold(
                root, "rep-test", template_type="content_stream", init_git=False
            )
            self.assertEqual(code, 0, msg)

            from argus.products.inventory import build_inventory

            inv = build_inventory(root)
            node = inv.valid["rep-test"].node
            r = build_kill_justification_report(root, "rep-test", node)

            self.assertEqual(r.get("schema"), "argus.kill_justification_report.v1")
            self.assertEqual(r.get("product_id"), "rep-test")
            self.assertIn("product_summary", r)
            self.assertIn("kill_score", r)
            self.assertIn("findings_history", r)
            self.assertIn("experiments", r)
            self.assertIn("cost_analysis", r)
            self.assertIn("decision_history", r)

            md = render_kill_justification_markdown(r)
            self.assertIn("rep-test", md)
            self.assertIn("Kill score breakdown", md)
            self.assertIn("kill_score", r["kill_score"])
