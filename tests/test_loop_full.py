"""Tests for ``argus loop full`` harness."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.loop.harness import run_full_loop_harness
from argus.products.scaffold import create_product_scaffold


class TestLoopFullHarness(unittest.TestCase):
    def test_invalid_product_filter(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            c, msg, _ = create_product_scaffold(
                root, "ok-app", template_type="content_stream", init_git=False
            )
            self.assertEqual(c, 0, msg)
            code, summary = run_full_loop_harness(root, product_id="missing")
            self.assertEqual(code, 2)
            self.assertFalse(summary.get("ok", True))

    def test_summary_written(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            c, msg, _ = create_product_scaffold(
                root, "harness-app", template_type="content_stream", init_git=False
            )
            self.assertEqual(c, 0, msg)
            code, summary = run_full_loop_harness(root, product_id="harness-app")
            self.assertEqual(code, 0, summary)
            rid = summary.get("run_id")
            assert rid
            sp = root / "runs" / "loop" / rid / "summary.json"
            self.assertTrue(sp.is_file())
            data = json.loads(sp.read_text(encoding="utf-8"))
            self.assertEqual(data.get("schema"), "argus.loop.full_summary.v1")
            stages = [s.get("stage") for s in (data.get("stages") or [])]
            self.assertIn("discovery", stages)
            self.assertIn("ideas", stages)
            self.assertIn("dashboard", stages)
            self.assertNotIn("simulation", stages)
            self.assertNotIn("portfolio", stages)
            self.assertIn("chain", data)
            chain = data.get("chain")
            self.assertIsInstance(chain, dict)
            self.assertEqual(chain.get("escalation"), "separate")
            self.assertIsInstance(chain.get("next_commands"), list)
            txt = root / "runs" / "loop" / rid / "summary.txt"
            self.assertTrue(txt.is_file(), "harness should write summary.txt next to summary.json")
            body = txt.read_text(encoding="utf-8")
            self.assertIn("Dry-run execution flag:", body)
            self.assertIn("--- Capability load (this run) ---", body)
            self.assertIn("--- Proposed actions", body)
            self.assertIn("stub_impact=", body)
