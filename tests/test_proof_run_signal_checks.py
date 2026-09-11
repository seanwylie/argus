"""Proof-run signal / temporal jq predicates (mirror docs/proof-run.md section 5)."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any


def _passes_manifest_missing_check(bundle: dict[str, Any]) -> bool:
    records = bundle.get("records") or []
    if not isinstance(records, list):
        return False
    for r in records:
        if not isinstance(r, dict):
            continue
        if r.get("source") != "manifest_declaration":
            continue
        p = r.get("payload") or {}
        if isinstance(p, dict) and p.get("collection_status") == "missing":
            return False
    return True


def _passes_temporal_worst_freshness(bundle: dict[str, Any]) -> bool:
    signals = bundle.get("signals") or []
    if not isinstance(signals, list) or len(signals) == 0:
        return True
    w = bundle.get("worst_freshness_status")
    if w is None:
        return True
    return w not in ("stale", "expired")


class TestProofRunSignalChecks(unittest.TestCase):
    def test_manifest_missing_fails(self) -> None:
        bad = {
            "records": [
                {
                    "source": "manifest_declaration",
                    "payload": {"collection_status": "missing"},
                }
            ]
        }
        self.assertFalse(_passes_manifest_missing_check(bad))

    def test_manifest_unsupported_ok(self) -> None:
        ok = {
            "records": [
                {
                    "source": "manifest_declaration",
                    "payload": {"collection_status": "unsupported_source_type"},
                }
            ]
        }
        self.assertTrue(_passes_manifest_missing_check(ok))

    def test_temporal_stale_fails(self) -> None:
        bad = {"signals": [{"freshness_status": "fresh"}], "worst_freshness_status": "stale"}
        self.assertFalse(_passes_temporal_worst_freshness(bad))

    def test_temporal_empty_signals_ok(self) -> None:
        self.assertTrue(_passes_temporal_worst_freshness({"signals": [], "worst_freshness_status": "stale"}))

    def test_doc_jq_snippets_parse(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn('jq -e \'[.records[] | select(.source == "manifest_declaration"', text)
        self.assertIn('worst_freshness_status != "stale"', text)
        self.assertIn("argus.signal_continuity.v1", text)


if __name__ == "__main__":
    unittest.main()
