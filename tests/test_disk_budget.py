"""Disk headroom helpers and doctor disk section."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.disk_budget import (
    DiskBudgetError,
    approx_tree_bytes,
    check_disk_headroom,
    collect_doctor_disk_section,
    require_disk_headroom_for_write,
)


class TestDiskBudget(unittest.TestCase):
    def test_min_free_zero_disables_check(self) -> None:
        with patch.dict(os.environ, {"ARGUS_MIN_FREE_DISK_MB": "0"}):
            from argus.disk_budget import check_disk_headroom, min_free_bytes

            self.assertIsNone(min_free_bytes())
            ok, msg = check_disk_headroom(Path("/"))
            self.assertTrue(ok)
            self.assertEqual(msg, "")

    def test_check_low_disk(self) -> None:
        p = Path("/tmp")
        need = 999_999_999_999
        with patch("argus.disk_budget.min_free_bytes", return_value=need):
            ok, msg = check_disk_headroom(p, op="test")
        self.assertFalse(ok)
        self.assertIn("low disk space", msg)
        self.assertIn("ARGUS_MIN_FREE_DISK_MB", msg)

    def test_require_warn_only(self) -> None:
        p = Path("/tmp")
        with (
            patch("argus.disk_budget.min_free_bytes", return_value=999_999_999_999),
            patch.dict("os.environ", {"ARGUS_ENFORCE_DISK_HEADROOM": ""}, clear=False),
        ):
            require_disk_headroom_for_write(p, op="x")

    def test_require_enforce_raises(self) -> None:
        p = Path("/tmp")
        with (
            patch("argus.disk_budget.min_free_bytes", return_value=999_999_999_999),
            patch.dict("os.environ", {"ARGUS_ENFORCE_DISK_HEADROOM": "1"}, clear=False),
        ):
            with self.assertRaises(DiskBudgetError):
                require_disk_headroom_for_write(p, op="x")

    def test_approx_tree_bytes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a").write_bytes(b"hi")
            sub = root / "s"
            sub.mkdir()
            (sub / "b").write_bytes(b"bye")
            total, n, capped = approx_tree_bytes(root)
            self.assertEqual(total, 5)
            self.assertEqual(n, 2)
            self.assertFalse(capped)

    def test_collect_doctor_disk_section_shape(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            runs = root / "runs"
            runs.mkdir()
            (runs / "x.txt").write_text("abc", encoding="utf-8")
            rep = collect_doctor_disk_section(root)
            self.assertEqual(rep["schema"], "argus.doctor_disk.v1")
            self.assertEqual(rep["runs_dir_approx_bytes"], 3)
            self.assertEqual(rep["runs_dir_files_counted"], 1)
            self.assertFalse(rep["runs_dir_tree_capped"])
