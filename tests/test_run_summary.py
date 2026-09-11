"""Tests for ``argus run summary`` and human run summaries."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.loop.harness import run_full_loop_harness
from argus.products.scaffold import create_product_scaffold
from argus.run.summary import (
    _format_assessment_explain,
    _stub_and_freshness_for_product,
    build_human_run_summary,
    latest_loop_run_id,
)


class TestRunSummaryHelpers(unittest.TestCase):
    def test_format_assessment_explain_without_assessment(self) -> None:
        s = _format_assessment_explain(None, None, 0)
        self.assertIn("no assessment", s)
        self.assertIn("stub_impact=(none)", s)

    def test_latest_loop_run_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "runs" / "loop"
            base.mkdir(parents=True)
            older = base / "older"
            newer = base / "newer"
            older.mkdir()
            newer.mkdir()
            st = older.stat()
            os.utime(newer, (st.st_mtime + 2, st.st_mtime + 2))
            self.assertEqual(latest_loop_run_id(root), "newer")


class TestRunSummaryIntegration(unittest.TestCase):
    def test_build_summary_after_harness(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            c, msg, _ = create_product_scaffold(
                root, "sum-app", template_type="content_stream", init_git=False
            )
            self.assertEqual(c, 0, msg)
            code, summary = run_full_loop_harness(root, product_id="sum-app")
            self.assertEqual(code, 0, summary)
            rid = summary.get("run_id")
            assert rid
            text = build_human_run_summary(root, str(rid))
            self.assertIn("Argus loop run summary", text)
            self.assertIn("--- Decision context", text)
            self.assertIn("--- Ideas", text)
            self.assertIn("--- Blocked actions", text)
            self.assertIn("--- Escalations (repo) ---", text)
            # stub line appears when decisions exist with metadata
            _stub, nfw = _stub_and_freshness_for_product(root, "sum-app")
            self.assertIsInstance(nfw, int)


class TestRunSummaryMalformedDryRun(unittest.TestCase):
    def test_malformed_dry_run_json_is_handled(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "runs" / "loop" / "testrun"
            run_dir.mkdir(parents=True)
            (run_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "schema": "argus.loop.full_summary.v1",
                        "run_id": "testrun",
                        "ok": True,
                        "exit_code": 0,
                        "dry_run_execution": True,
                        "started_at_utc": "x",
                        "finished_at_utc": "x",
                        "target_product_ids": [],
                        "stages": [],
                        "chain": {"next_commands": []},
                    }
                ),
                encoding="utf-8",
            )
            (run_dir / "stages" / "execution").mkdir(parents=True)
            (run_dir / "stages" / "execution" / "dry_run_results.json").write_text(
                "not json", encoding="utf-8"
            )
            text = build_human_run_summary(root, "testrun")
            self.assertIn("invalid JSON", text)
