"""Tests for autonomy activation gate."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.autonomy.activate import run_activation_gate
from argus.products.scaffold import create_product_scaffold


class TestAutonomyActivate(unittest.TestCase):
    def test_activation_fails_without_products_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            res = run_activation_gate(root)
            self.assertFalse(res.ok)
            self.assertTrue(any("doctor" in r.lower() for r in res.reasons))

    def test_activation_succeeds_minimal_sane_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            c, msg, _ = create_product_scaffold(
                root, "act-demo", template_type="content_stream", init_git=False
            )
            self.assertEqual(c, 0, msg)
            (root / "runs").mkdir(parents=True)
            (root / "runs" / "autonomy").mkdir(parents=True)
            (root / "runs" / "autonomy" / "autonomy.json").write_text(
                json.dumps({"schema": "argus.autonomy.v1", "mode": "manual"}),
                encoding="utf-8",
            )
            res = run_activation_gate(root)
            self.assertTrue(res.ok, res.reasons)
            self.assertEqual(res.autonomy_mode, "supervised")
            raw = json.loads((root / "runs" / "autonomy" / "autonomy.json").read_text(encoding="utf-8"))
            self.assertEqual(raw.get("mode"), "supervised")
