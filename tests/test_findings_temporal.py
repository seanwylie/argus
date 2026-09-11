"""Tests for temporal-aware finding rules."""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from argus.core.models.enums import FindingKind, SignalType
from argus.core.models.signal import SignalRecord
from argus.findings.engine import generate_findings
from argus.findings.rules.temporal import TemporalSignalsRule
from tests.test_findings_rules import _minimal_product


class TestTemporalSpikeOpportunity(unittest.TestCase):
    def test_spike_ratio_emits_current_opportunity(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        sigs = [
            SignalRecord(
                id="t1",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="trends",
                observed_at=ts,
                payload={
                    "temporal": {
                        "spike_ratio": 1.5,
                        "prior_window_label": "last week",
                    }
                },
            )
        ]
        f = generate_findings(p, sigs, rules=[TemporalSignalsRule()])
        kinds = {x.kind for x in f}
        self.assertIn(FindingKind.CURRENT_OPPORTUNITY, kinds)
        opp = next(x for x in f if x.kind == FindingKind.CURRENT_OPPORTUNITY)
        self.assertIn("anchor_timestamp", (opp.evidence or {}))
        self.assertTrue((opp.evidence or {}).get("temporal_finding"))


class TestTemporalStaleContext(unittest.TestCase):
    def test_stale_seconds_emits_stale_context(self) -> None:
        p = _minimal_product()
        ts = datetime.now(timezone.utc)
        stale_sec = 11 * 24 * 3600
        sigs = [
            SignalRecord(
                id="t2",
                product_id=p.id,
                signal_type=SignalType.METRICS,
                source="intel",
                observed_at=ts,
                payload={"temporal": {"context_stale_seconds": stale_sec}},
            )
        ]
        f = generate_findings(p, sigs, rules=[TemporalSignalsRule()])
        self.assertTrue(any(x.kind == FindingKind.STALE_CONTEXT for x in f))
        st = next(x for x in f if x.kind == FindingKind.STALE_CONTEXT)
        self.assertIn("context", (st.summary or "").lower())


class TestTemporalNoRecentEvidence(unittest.TestCase):
    def test_empty_signals_emits_no_recent_evidence(self) -> None:
        p = _minimal_product()
        f = generate_findings(p, [], rules=[TemporalSignalsRule()])
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].kind, FindingKind.NO_RECENT_EVIDENCE)

    def test_all_signals_older_than_14d_emits_no_recent_evidence(self) -> None:
        p = _minimal_product()
        old = datetime.now(timezone.utc) - timedelta(days=20)
        sigs = [
            SignalRecord(
                id="old1",
                product_id=p.id,
                signal_type=SignalType.FILESYSTEM,
                source="snap",
                observed_at=old,
                payload={"ok": True},
            )
        ]
        f = generate_findings(p, sigs, rules=[TemporalSignalsRule()])
        self.assertTrue(any(x.kind == FindingKind.NO_RECENT_EVIDENCE for x in f))
        self.assertFalse(any(x.kind == FindingKind.STALE_CONTEXT for x in f))


if __name__ == "__main__":
    unittest.main()
