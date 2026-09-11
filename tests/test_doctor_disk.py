"""Doctor ``disk`` section (runs footprint + volume stats)."""

from __future__ import annotations

import json
import unittest
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from argus.cli.doctor_cmd import cmd_doctor


def _args(**kwargs: object) -> SimpleNamespace:
    base = {"products_dir": None, "json": False, "strict": False}
    base.update(kwargs)
    return SimpleNamespace(**base)


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: {pid}
owner:
  team: t
lifecycle:
  stage: validate
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 0
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")


class TestDoctorDisk(unittest.TestCase):
    def test_doctor_json_includes_disk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "pd")
            buf = StringIO()
            with patch("sys.stdout", buf):
                rc = cmd_doctor(root, _args(json=True))
            self.assertEqual(rc, 0)
            out = json.loads(buf.getvalue())
            self.assertIn("disk", out)
            d = out["disk"]
            self.assertEqual(d.get("schema"), "argus.doctor_disk.v1")
            self.assertIn("runs_dir_approx_bytes", d)
            self.assertIn("volume_free_bytes", d)
