"""Tests for the analysis loop orchestrator."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.loop import run_analysis_loop
from argus.orchestrator.stages import LOOP_STAGE_ORDER, LoopStage
from argus.products.scaffold import create_product_scaffold


class TestLoopStages(unittest.TestCase):
    def test_stage_order(self) -> None:
        self.assertEqual(
            [s.value for s in LOOP_STAGE_ORDER],
            [
                "discover",
                "signals",
                "findings",
                "decisions",
            ],
        )
        self.assertEqual(len(LoopStage), 4)


class TestLoopRun(unittest.TestCase):
    def test_invalid_product_filter_fails_discover(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, manifest = run_analysis_loop(
                root,
                product_id="no_such_product",
            )
            self.assertEqual(code, 1)
            self.assertFalse(manifest.get("ok"))
            stages = manifest.get("stages") or []
            self.assertEqual(len(stages), 1)
            self.assertEqual(stages[0].get("stage"), "discover")
            self.assertFalse(stages[0].get("ok"))

    def test_full_loop_on_scaffolded_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            c, msg, _ = create_product_scaffold(
                root, "loop-test-app", template_type="content_stream", init_git=False
            )
            self.assertEqual(c, 0, msg)

            code, manifest = run_analysis_loop(root, product_id="loop-test-app")
            self.assertEqual(code, 0, manifest)
            self.assertTrue(manifest.get("ok"))
            run_id = manifest.get("run_id")
            assert run_id is not None
            run_dir = root / "runs" / "loop" / run_id
            self.assertTrue((run_dir / "manifest.json").is_file())
            self.assertTrue((run_dir / "stages" / "discover" / "output.json").is_file())
            self.assertTrue((run_dir / "stages" / "decisions" / "output.json").is_file())
