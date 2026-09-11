"""Autonomy spawn: proposal + optional scaffold (quota, approval gate)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.autonomy.spawn import (
    apply_spawn_proposal,
    build_spawn_proposal,
    quota_remaining,
    write_proposal_file,
)


class TestAutonomySpawn(unittest.TestCase):
    def test_proposal_deterministic_empty_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            p1 = build_spawn_proposal(root)
            p2 = build_spawn_proposal(root)
            self.assertEqual(p1.fingerprint, p2.fingerprint)
            self.assertEqual(p1.proposed_slug, p2.proposed_slug)
            self.assertTrue(p1.thesis)

    def test_quota_unlimited_first_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            rem, _ = quota_remaining(root, max_per_period=3, period_days=30)
            self.assertEqual(rem, 3)

    def test_apply_creates_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            prop = build_spawn_proposal(root)
            # avoid collision with fingerprint slug if re-run
            prop = build_spawn_proposal(root)
            code, payload = apply_spawn_proposal(root, prop)
            self.assertEqual(code, 0, msg=str(payload))
            pid = str(payload.get("product_id"))
            pr = root / "products" / pid
            self.assertTrue((pr / "product.yaml").is_file())
            self.assertTrue((pr / "doctrine.yaml").is_file())
            self.assertTrue((pr / "experiment_plan.yaml").is_file())

    def test_spawn_log_append(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            prop = build_spawn_proposal(root)
            apply_spawn_proposal(root, prop)
            log = root / "runs" / "autonomy" / "spawn_log.jsonl"
            self.assertTrue(log.is_file())
            line = log.read_text(encoding="utf-8").strip().splitlines()[0]
            row = json.loads(line)
            self.assertIn("product_id", row)

    def test_write_proposal_file(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            prop = build_spawn_proposal(root)
            path = write_proposal_file(root, prop)
            self.assertTrue(path.is_file())
            raw = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], "argus.spawn_proposal.v1")


if __name__ == "__main__":
    unittest.main()
