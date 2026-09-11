"""Tests for ``argus.validation``."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.validation.validate import validate_repo_artifacts


class TestValidation(unittest.TestCase):
    def test_empty_repo_ok(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs").mkdir(parents=True)
            rep = validate_repo_artifacts(root)
            self.assertTrue(rep.ok)

    def test_execution_run_requires_fields(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rd = root / "runs" / "execution" / "exec_test_1"
            rd.mkdir(parents=True)
            (rd / "run.json").write_text(
                json.dumps({"run_id": "x", "status": "success"}),
                encoding="utf-8",
            )
            rep = validate_repo_artifacts(root)
            self.assertFalse(rep.ok)
            msgs = " ".join(i.message for i in rep.issues)
            self.assertIn("missing", msgs.lower())
