"""Tests for capability resume engine."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.capabilities.resume import resume_blocked_actions, sync_blocked_actions_artifact


class TestCapabilityResume(unittest.TestCase):
    def test_sync_blocked_empty_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "runs" / "autonomy").mkdir(parents=True)
            p = sync_blocked_actions_artifact(root)
            self.assertTrue(p.is_file())

    def test_resume_no_pauses(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir(parents=True)
            res = resume_blocked_actions(root)
            self.assertEqual(res.cleared_pauses, [])
