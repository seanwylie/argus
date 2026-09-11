"""CLI helper: manifest + temporal hints for ``signals show``."""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.cli.signals_cmd import signal_operator_context
from argus.core.models.enums import SignalType
from argus.core.models.signal import SignalRecord
from argus.products.inventory import build_inventory
from argus.signals.persistence import save_collection


def _write(p: Path, content: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def _minimal_product_yaml(pid: str) -> str:
    return f"""
    id: {pid}
    name: T
    owner:
      team: t
    lifecycle:
      stage: idea
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
      max_monthly_cost_usd: 1
      min_activity_threshold: 0
    """


class TestSignalOperatorContext(unittest.TestCase):
    def test_manifest_count_and_temporal_after_save(self) -> None:
        from datetime import datetime, timezone

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "op_ctx_p"
            pr = root / "products" / pid
            _write(pr / "product.yaml", _minimal_product_yaml(pid))
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")

            inv = build_inventory(root)
            self.assertIn(pid, inv.valid)

            now = datetime.now(timezone.utc)
            rec = SignalRecord(
                id="s1",
                product_id=pid,
                signal_type=SignalType.FILESYSTEM,
                source="t",
                observed_at=now,
                payload={},
            )
            save_collection(root, pid, [rec])

            ctx = signal_operator_context(root, pid)
            self.assertEqual(ctx.get("product_signal_manifest_entries"), 0)
            self.assertTrue(ctx.get("temporal_bundle_present"))
            self.assertIsNotNone(ctx.get("worst_freshness_status"))


if __name__ == "__main__":
    unittest.main()
