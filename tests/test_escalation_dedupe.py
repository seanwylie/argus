"""Escalation dedupe: skip noisy duplicate writes."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.escalation.dedupe import (
    find_recent_duplicate_packet,
    rule_fingerprint,
    summarize_escalation_groups,
)


class TestEscalationDedupe(unittest.TestCase):
    def test_rule_fingerprint_order_independent(self) -> None:
        a = rule_fingerprint(["b", "a"])
        b = rule_fingerprint(["a", "b"])
        self.assertEqual(a, b)

    def test_find_duplicate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lat = root / "runs" / "escalations" / "latest"
            lat.mkdir(parents=True)
            ts = "20260110T120000Z"
            pid = "myapp"
            rules = ["rule_a", "rule_b"]
            now = datetime.now(timezone.utc).isoformat()
            payload = {
                "packet_id": f"esc_{ts}_{pid}",
                "product_id": pid,
                "created_at": now,
                "triggering_rules": rules,
            }
            (lat / f"esc_{ts}_{pid}.json").write_text(json.dumps(payload), encoding="utf-8")
            dup = find_recent_duplicate_packet(root, pid, rules, hours=48.0)
            self.assertIsNotNone(dup)
            self.assertEqual(dup.get("packet_id"), f"esc_{ts}_{pid}")

    def test_summarize_groups(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            lat = root / "runs" / "escalations" / "latest"
            lat.mkdir(parents=True)
            for i in range(2):
                payload = {
                    "packet_id": f"esc_2026011{i}T120000Z_x",
                    "product_id": "x",
                    "created_at": f"2026-01-1{i}T12:00:00+00:00",
                    "triggering_rules": ["same"],
                }
                (lat / f"esc_2026011{i}T120000Z_x.json").write_text(json.dumps(payload), encoding="utf-8")
            s = summarize_escalation_groups(root)
            self.assertEqual(s.get("schema"), "argus.escalation_dedupe_summary.v1")
            groups = s.get("groups") or []
            self.assertTrue(any(g.get("count", 0) >= 2 for g in groups))
