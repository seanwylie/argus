"""Operator snapshot (``argus.operator_snapshot.v1``) — consolidated per-product judgment."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.mission.provenance import MISSION_PROVENANCE_SCHEMA
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.operator_snapshot import (
    OPERATOR_SNAPSHOT_SCHEMA,
    build_operator_snapshot,
    operator_snapshot_json_path,
    operator_snapshot_md_path,
    write_operator_snapshot_artifacts,
)
from argus.orchestrator.readiness import ORCHESTRATION_READINESS_SCHEMA
from argus.orchestrator.state_pass import write_orchestration_state_payload


def _product_yaml(
    root: Path,
    product_id: str,
    *,
    first_pass: str | None = None,
) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    raw_ext = ""
    if first_pass is not None:
        raw_ext = f"""
raw_extensions:
  import_state:
    schema: argus.import_state.v1
    first_pass_status: {first_pass}
    imported_at_utc: 2026-04-13T12:00:00Z
"""
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: SnapTest
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths: [metrics/]
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
{raw_ext}
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    for n in ("start.sh", "stop.sh", "analyze.sh"):
        (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")


def _write_spine(root: Path, product_id: str, *, confidence: float | None) -> None:
    ts = "2026-04-13T11:00:00Z"
    sig_p = root / "runs" / "signals" / "latest" / f"{product_id}.json"
    sig_p.parent.mkdir(parents=True, exist_ok=True)
    sig_p.write_text(
        json.dumps({"collected_at_utc": ts, "record_count": 1, "records": []}),
        encoding="utf-8",
    )
    fin_p = root / "runs" / "findings" / "latest" / f"{product_id}.json"
    fin_p.parent.mkdir(parents=True, exist_ok=True)
    fin_p.write_text(json.dumps({"finding_count": 0, "findings": []}), encoding="utf-8")
    dec_p = root / "runs" / "decisions" / "latest" / f"{product_id}.json"
    dec_p.parent.mkdir(parents=True, exist_ok=True)
    body: dict = {"candidates": []}
    if confidence is not None:
        body = {
            "candidates": [{"rank": 1, "confidence": confidence, "title": "Top decision title"}],
        }
    dec_p.write_text(json.dumps(body), encoding="utf-8")


@patch("argus.orchestrator.eligibility.utc_now")
class TestOperatorSnapshot(unittest.TestCase):
    def test_mature_ready_product(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "snap_mature"
            _product_yaml(root, pid, first_pass="success")
            _write_spine(root, pid, confidence=0.92)
            st = evaluate_product_orchestration(root, pid)
            snap = build_operator_snapshot(root, st)
            self.assertEqual(snap["schema"], OPERATOR_SNAPSHOT_SCHEMA)
            self.assertEqual((snap.get("mission_context") or {}).get("schema"), MISSION_PROVENANCE_SCHEMA)
            self.assertEqual((snap.get("mission_context") or {}).get("resolved_mission_id"), "revenue")
            self.assertEqual(snap["product_id"], pid)
            self.assertEqual(snap["readiness"]["schema"], ORCHESTRATION_READINESS_SCHEMA)
            # Spine present but audit/temporal gaps may keep tier below advance_ready in minimal trees.
            self.assertIn(
                snap["readiness"]["readiness_tier"],
                ("advance_ready", "interpret_gap"),
            )
            self.assertIn(snap["readiness"]["confidence_gate"], ("open", "caution"))
            self.assertLess(snap["understanding_debt"], 0.55)
            self.assertEqual(snap["decision_summary"].get("top_decision_title"), "Top decision title")
            self.assertIn("operator_recommendation", snap)
            jpath, mpath = write_operator_snapshot_artifacts(root, pid, st)
            self.assertEqual(jpath, operator_snapshot_json_path(root, pid))
            self.assertEqual(mpath, operator_snapshot_md_path(root, pid))
            self.assertTrue(jpath.is_file())
            self.assertTrue(mpath.is_file())
            md = mpath.read_text(encoding="utf-8")
            self.assertIn("Operator snapshot", md)
            self.assertIn(pid, md)

    def test_partial_import_product(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "snap_partial"
            _product_yaml(root, pid, first_pass="partial")
            _write_spine(root, pid, confidence=0.8)
            st = evaluate_product_orchestration(root, pid)
            snap = build_operator_snapshot(root, st)
            self.assertEqual(snap["readiness"]["readiness_tier"], "import_incomplete")
            self.assertEqual(snap["readiness"]["confidence_gate"], "caution")
            self.assertIn("first_pass", (snap["readiness_reason"] or "").lower())

    def test_low_confidence_product(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "snap_lowconf"
            _product_yaml(root, pid, first_pass="success")
            _write_spine(root, pid, confidence=0.15)
            st = evaluate_product_orchestration(root, pid)
            snap = build_operator_snapshot(root, st)
            self.assertEqual(snap["readiness"]["confidence_gate"], "caution")
            self.assertEqual(snap["decision_summary"].get("top_decision_confidence"), 0.15)

    def test_waiting_input_product(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "snap_wait"
            _product_yaml(root, pid, first_pass="success")
            _write_spine(root, pid, confidence=0.9)
            st = evaluate_product_orchestration(root, pid)
            st = dict(st)
            st["waiting_inputs"] = [
                {"kind": "refinement_grounded_input", "detail": "need file X", "session_id": "s1"},
            ]
            st["orchestration_status"] = "blocked_waiting_input"
            snap = build_operator_snapshot(root, st)
            wb = snap["waiting_and_blocking"]
            self.assertEqual(len(wb["waiting_inputs"]), 1)
            self.assertIn("Waiting on:", snap["operator_recommendation"])

    def test_legacy_no_import_state_product(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "snap_legacy"
            _product_yaml(root, pid, first_pass=None)
            _write_spine(root, pid, confidence=0.85)
            st = evaluate_product_orchestration(root, pid)
            snap = build_operator_snapshot(root, st)
            self.assertEqual(snap["readiness"]["readiness_tier"], "unprofiled")
            self.assertIn("readiness.no_import_state", snap["readiness"]["readiness_reason_codes"])

    def test_write_orchestration_state_payload_writes_snapshot(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "snap_int"
            _product_yaml(root, pid, first_pass="success")
            _write_spine(root, pid, confidence=0.7)
            st = evaluate_product_orchestration(root, pid)
            write_orchestration_state_payload(root, pid, st)
            self.assertTrue(operator_snapshot_json_path(root, pid).is_file())
            self.assertTrue(operator_snapshot_md_path(root, pid).is_file())


if __name__ == "__main__":
    unittest.main()
