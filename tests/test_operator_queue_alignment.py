"""Tests for operator queue ↔ orchestration alignment (freshest durable view)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.operator_snapshot import OPERATOR_SNAPSHOT_SCHEMA, build_operator_snapshot
from argus.orchestrator.state_models import ORCHESTRATION_STATE_SCHEMA
from argus.portfolio.operator_queue import load_operator_view


def _minimal_orch(product_id: str, evaluated_at: str) -> dict:
    return {
        "schema": ORCHESTRATION_STATE_SCHEMA,
        "product_id": product_id,
        "evaluated_at_utc": evaluated_at,
        "orchestration_status": "eligible",
        "orchestration_status_reason": "test",
        "next_action": "findings_generate",
        "eligible_actions": [],
        "waiting_inputs": [],
        "artifacts": {},
        "eligibility_facts": {},
    }


class TestOperatorQueueAlignment(unittest.TestCase):
    def test_load_operator_view_supersedes_stale_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            orch_dir = root / "runs" / "orchestration" / "latest"
            orch_dir.mkdir(parents=True)
            snap_dir = root / "runs" / "orchestration" / "operator_snapshot"
            snap_dir.mkdir(parents=True)

            old_orch = _minimal_orch(pid, "2026-01-01T00:00:00+00:00")
            snap_old = build_operator_snapshot(root, old_orch, product_id=pid)
            (snap_dir / f"{pid}.json").write_text(json.dumps(snap_old), encoding="utf-8")

            new_orch = _minimal_orch(pid, "2026-06-01T12:00:00+00:00")
            new_orch["next_action"] = "decisions_generate"
            (orch_dir / f"{pid}.json").write_text(json.dumps(new_orch), encoding="utf-8")

            view, src = load_operator_view(root, pid)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(view.get("next_action"), "decisions_generate")
            self.assertIn("supersedes", src)

    def test_load_operator_view_uses_snapshot_when_fresher(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p2"
            orch_dir = root / "runs" / "orchestration" / "latest"
            orch_dir.mkdir(parents=True)
            snap_dir = root / "runs" / "orchestration" / "operator_snapshot"
            snap_dir.mkdir(parents=True)

            orch = _minimal_orch(pid, "2026-01-01T00:00:00+00:00")
            (orch_dir / f"{pid}.json").write_text(json.dumps(orch), encoding="utf-8")

            snap = build_operator_snapshot(root, orch, product_id=pid)
            snap["source_evaluated_at_utc"] = "2026-06-01T00:00:00+00:00"
            self.assertEqual(snap.get("schema"), OPERATOR_SNAPSHOT_SCHEMA)
            (snap_dir / f"{pid}.json").write_text(json.dumps(snap), encoding="utf-8")

            view, src = load_operator_view(root, pid)
            self.assertIsNotNone(view)
            assert view is not None
            self.assertEqual(src, "snapshot_file")
            self.assertEqual(view.get("next_action"), "findings_generate")


if __name__ == "__main__":
    unittest.main()
