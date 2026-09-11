"""Autonomy full-loop runner (runs/autonomy/<run_id>/)."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.autonomy.runner import cron_next_sleep_seconds, new_autonomy_run_id, run_autonomy_cycle


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    pr.joinpath("product.yaml").write_text(
        textwrap.dedent(
            f"""
            id: {pid}
            name: {pid}
            owner:
              team: test
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
              analyze: "./scripts/s.sh"
            constraints:
              max_monthly_cost_usd: 100
              min_activity_threshold: 0
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho ok\n", encoding="utf-8")


class TestCronSleep(unittest.TestCase):
    def test_every_15_min(self) -> None:
        s = cron_next_sleep_seconds("*/15 * * * *")
        self.assertGreaterEqual(s, 1.0)
        self.assertLessEqual(s, 15 * 60)


class TestAutonomyCycle(unittest.TestCase):
    def test_run_creates_manifest(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "auto_p1")
            rid = new_autonomy_run_id()
            self.assertTrue(rid.startswith("auto_"))
            code, man = run_autonomy_cycle(
                root,
                run_id=rid,
                products_dir=None,
                product_id="auto_p1",
                continue_on_error=True,
                execution_enabled=False,
            )
            self.assertIsInstance(code, int)
            self.assertEqual(man.get("run_id"), rid)
            mf = root / "runs" / "autonomy" / rid / "manifest.json"
            self.assertTrue(mf.is_file())


if __name__ == "__main__":
    unittest.main()
