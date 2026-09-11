"""Tests for intervention inbox + action ledger."""

from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from argus.portfolio.intervention import (
    INTERVENTION_EVIDENCE_REFRESH,
    PORTFOLIO_INTERVENTION_SCHEMA,
)
from argus.portfolio.intervention_actions import (
    ACTION_ACKNOWLEDGE,
    ACTION_IGNORE,
    ACTION_RESOLVE,
    ACTION_SNOOZE,
    append_intervention_action,
    intervention_inbox_actions_dir,
    item_id_for_intervention_row,
    merge_intervention_actions,
    record_intervention_resolve,
    snooze_until_iso,
)
from argus.portfolio.intervention_inbox import (
    build_intervention_inbox_payload,
    run_intervention_inbox,
)


def _row(
    pid: str = "alpha",
    *,
    cat: str = "human_review",
    codes: list[str] | None = None,
    severity: str = "high",
) -> dict:
    c = codes or ["intervention.repeated_blocked_progression"]
    return {
        "product_id": pid,
        "intervention_category": cat,
        "severity": severity,
        "detection_reason_codes": c,
        "evidence_summary": "e",
        "recommended_operator_action": "act",
        "chronicity": "chronic",
    }


def _report(run_id: str, rows: list[dict]) -> dict:
    return {
        "schema": PORTFOLIO_INTERVENTION_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": "2026-04-01T00:00:00Z",
        "inputs": {},
        "thresholds": {},
        "flagged_products": rows,
        "stable_benign_products": [],
    }


class TestInterventionInbox(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="argus-inv-"))

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def test_inbox_from_intervention_report(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        r = _report("20260410T120000Z", [_row()])
        inv.joinpath("latest.json").write_text(json.dumps(r), encoding="utf-8")
        payload = build_intervention_inbox_payload(root, intervention_report=r)
        self.assertEqual(payload.get("schema"), "argus.intervention_inbox.v1")
        self.assertEqual(payload.get("source_intervention_run_id"), "20260410T120000Z")
        items = payload.get("open_items") or []
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["product_id"], "alpha")
        self.assertEqual(items[0]["ack_state"], "open")
        self.assertTrue(items[0]["in_active_queue"])

    def test_evidence_unchanged_across_last_two_intervention_artifacts(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = {
            "product_id": "alpha",
            "intervention_category": INTERVENTION_EVIDENCE_REFRESH,
            "severity": "medium",
            "detection_reason_codes": ["intervention.repeated_same_next_action_no_readiness_gain"],
            "evidence_summary": "e",
            "recommended_operator_action": "refresh",
            "chronicity": "emerging",
        }
        inv.joinpath("20260401T000000Z.json").write_text(
            json.dumps(_report("20260401T000000Z", [row])),
            encoding="utf-8",
        )
        inv.joinpath("20260402T000000Z.json").write_text(
            json.dumps(_report("20260402T000000Z", [row])),
            encoding="utf-8",
        )
        inv.joinpath("latest.json").write_text(
            json.dumps(_report("20260402T000000Z", [row])),
            encoding="utf-8",
        )
        payload = build_intervention_inbox_payload(root, recent_intervention_limit=10)
        item = (payload.get("open_items") or [])[0]
        self.assertTrue(item.get("evidence_unchanged_across_last_two_intervention_runs"))
        self.assertEqual(int(item.get("intervention_reports_with_detection_row") or 0), 2)
        roll = payload.get("active_queue_rollups") or {}
        self.assertGreaterEqual(int(roll.get("active_item_count") or 0), 1)
        self.assertIn(INTERVENTION_EVIDENCE_REFRESH, (roll.get("by_intervention_category") or {}))

    def test_evidence_not_unchanged_when_detection_fingerprint_changes(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        base = {
            "product_id": "alpha",
            "intervention_category": INTERVENTION_EVIDENCE_REFRESH,
            "detection_reason_codes": ["intervention.repeated_same_next_action_no_readiness_gain"],
            "evidence_summary": "e",
            "recommended_operator_action": "refresh",
            "chronicity": "emerging",
        }
        row_lo = {**base, "severity": "medium"}
        row_hi = {**base, "severity": "high"}
        inv.joinpath("20260401T000000Z.json").write_text(
            json.dumps(_report("20260401T000000Z", [row_lo])),
            encoding="utf-8",
        )
        inv.joinpath("20260402T000000Z.json").write_text(
            json.dumps(_report("20260402T000000Z", [row_hi])),
            encoding="utf-8",
        )
        inv.joinpath("latest.json").write_text(
            json.dumps(_report("20260402T000000Z", [row_hi])),
            encoding="utf-8",
        )
        payload = build_intervention_inbox_payload(root, recent_intervention_limit=10)
        item = (payload.get("open_items") or [])[0]
        self.assertFalse(item.get("evidence_unchanged_across_last_two_intervention_runs"))

    def test_recurring_across_stamped_runs(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("20260401T000000Z.json").write_text(
            json.dumps(_report("20260401T000000Z", [row])),
            encoding="utf-8",
        )
        inv.joinpath("20260402T000000Z.json").write_text(
            json.dumps(_report("20260402T000000Z", [row])),
            encoding="utf-8",
        )
        inv.joinpath("latest.json").write_text(
            json.dumps(_report("20260402T000000Z", [row])),
            encoding="utf-8",
        )
        payload = build_intervention_inbox_payload(root, recent_intervention_limit=10)
        found = [x for x in (payload.get("open_items") or []) if x.get("item_id") == iid]
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0]["recurring"])
        self.assertEqual(found[0]["first_seen_run_id"], "20260401T000000Z")
        self.assertEqual(found[0]["last_seen_run_id"], "20260402T000000Z")
        self.assertGreaterEqual(found[0]["seen_in_run_count"], 2)

    def test_ack_acknowledged(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        append_intervention_action(root, item_id=iid, verb=ACTION_ACKNOWLEDGE, note="seen")
        payload = build_intervention_inbox_payload(root)
        item = (payload.get("open_items") or [])[0]
        self.assertEqual(item["ack_state"], "acknowledged")
        self.assertTrue(item["in_active_queue"])

    def test_resolve_then_snooze_state(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        record_intervention_resolve(root, iid, note="done")
        payload = build_intervention_inbox_payload(root)
        item = (payload.get("open_items") or [])[0]
        self.assertEqual(item["ack_state"], "open")
        self.assertTrue(item.get("reopened_after_resolve_or_ignore"))
        append_intervention_action(
            root,
            item_id=iid,
            verb=ACTION_SNOOZE,
            snooze_until_utc=snooze_until_iso(days=30.0),
        )
        payload2 = build_intervention_inbox_payload(root)
        item2 = (payload2.get("open_items") or [])[0]
        self.assertEqual(item2["ack_state"], "snoozed")
        self.assertFalse(item2["in_active_queue"])

    def test_resolve_then_ignore(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        append_intervention_action(
            root,
            item_id=iid,
            verb=ACTION_IGNORE,
            resolution_fingerprint="alpha|human_review|high|intervention.repeated_blocked_progression",
        )
        payload = build_intervention_inbox_payload(root)
        item = (payload.get("open_items") or [])[0]
        self.assertEqual(item["ack_state"], "open")
        self.assertTrue(item.get("reopened_after_resolve_or_ignore"))

    def test_snoozed_suppressed_until_due(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        append_intervention_action(
            root,
            item_id=iid,
            verb=ACTION_SNOOZE,
            snooze_until_utc=snooze_until_iso(days=30.0),
        )
        payload = build_intervention_inbox_payload(root)
        item = (payload.get("open_items") or [])[0]
        self.assertFalse(item["in_active_queue"])

    def test_snooze_expired_surfaces(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        append_intervention_action(
            root,
            item_id=iid,
            verb=ACTION_SNOOZE,
            snooze_until_utc=snooze_until_iso(days=-1.0),
        )
        payload = build_intervention_inbox_payload(root)
        item = (payload.get("open_items") or [])[0]
        self.assertEqual(item["ack_state"], "open")
        self.assertTrue(item["in_active_queue"])

    def test_material_change_reopens_as_new_fingerprint(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        iid = item_id_for_intervention_row(row)
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        append_intervention_action(
            root,
            item_id=iid,
            verb=ACTION_RESOLVE,
            resolution_fingerprint="alpha|human_review|high|intervention.repeated_blocked_progression",
        )
        row2 = _row(codes=["intervention.oscillating_next_action"])
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260411T120000Z", [row2])), encoding="utf-8")
        payload = build_intervention_inbox_payload(root)
        items = payload.get("open_items") or []
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["ack_state"], "open")
        self.assertFalse(items[0].get("reopened_after_resolve_or_ignore"))

    def test_merge_actions_order(self) -> None:
        root = self.root
        d = intervention_inbox_actions_dir(root)
        d.mkdir(parents=True)
        iid = "inv-test-1"
        append_intervention_action(root, item_id=iid, verb=ACTION_ACKNOWLEDGE)
        append_intervention_action(root, item_id=iid, verb=ACTION_RESOLVE, resolution_fingerprint="fp")
        m = merge_intervention_actions(root)
        self.assertEqual(m[iid]["ack_state"], "resolved")

    def test_write_latest(self) -> None:
        root = self.root
        inv = root / "runs" / "portfolio" / "intervention"
        inv.mkdir(parents=True)
        row = _row()
        inv.joinpath("latest.json").write_text(json.dumps(_report("20260410T120000Z", [row])), encoding="utf-8")
        payload = run_intervention_inbox(root, write_artifacts=True)
        self.assertTrue((root / "runs" / "portfolio" / "intervention_inbox" / "latest.json").is_file())
        self.assertEqual(payload["schema"], "argus.intervention_inbox.v1")


if __name__ == "__main__":
    unittest.main()
