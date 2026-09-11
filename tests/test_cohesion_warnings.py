"""Cohesion: operator-visible degradation (warnings/logs), not silent failures."""

from __future__ import annotations

import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import patch

from argus.products.inventory import build_inventory
from argus.signals.adapters.temporal_snapshots import TemporalSnapshotSignalAdapter
from argus.signals.contract import ProductSignalContext


def _product_yaml(pid: str) -> str:
    return f"""
id: {pid}
name: T
owner:
  team: t
lifecycle:
  stage: validate
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: temporal
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
"""


class TestWeeklyEconomicsWarning(unittest.TestCase):
    def test_warns_when_economics_unavailable(self) -> None:
        from argus.planning.weekly import build_weekly_plan

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "products").mkdir()
            with patch(
                "argus.planning.weekly.analyze_inventory",
                side_effect=RuntimeError("simulated economics failure"),
            ):
                with warnings.catch_warnings(record=True) as wrec:
                    warnings.simplefilter("always")
                    build_weekly_plan(root)
            msgs = [str(w.message) for w in wrec]
            self.assertTrue(
                any("economics slice omitted" in m for m in msgs),
                msg=msgs,
            )


class TestTemporalSnapshotAdapterLogging(unittest.TestCase):
    def test_logs_malformed_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            pid = "cohesion_p"
            pr = root / "products" / pid
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (pr / "product.yaml").write_text(_product_yaml(pid), encoding="utf-8")
            snap = pr / "metrics" / "snapshots"
            snap.mkdir(parents=True)
            (snap / "posthog_malformed.json").write_text("NOT VALID JSON {{{", encoding="utf-8")

            inv = build_inventory(root)
            node = inv.valid[pid].node
            ctx = ProductSignalContext(repo_root=root, product=node, product_root=pr.resolve())
            ad = TemporalSnapshotSignalAdapter()
            with self.assertLogs("argus.signals.adapters.temporal_snapshots", level="WARNING") as cm:
                ad.collect(ctx)
            self.assertTrue(any("posthog_malformed" in x for x in cm.output), msg=cm.output)


if __name__ == "__main__":
    unittest.main()
