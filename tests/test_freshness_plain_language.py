"""Tests for orchestration freshness plain-language helpers (display-only)."""

from __future__ import annotations

import unittest

from argus.orchestrator.freshness_plain_language import (
    freshness_explanation_lines,
    freshness_summary_sentence,
)
from argus.orchestrator.state_models import ORCH_STATUS_STALE_REFRESH_NEEDED


class TestFreshnessLines(unittest.TestCase):
    def test_fresh_signals_stale_temporal(self) -> None:
        facts = {
            "signals_collection_time_stale": False,
            "temporal_freshness_stale": True,
            "temporal_worst_freshness_status": "expired",
            "signals_refresh_needed": True,
            "audit_bundle_time_stale": False,
            "audit_product_gap_incomplete": False,
            "audit_security_stub": False,
        }
        lines = freshness_explanation_lines(
            facts,
            orchestration_status=ORCH_STATUS_STALE_REFRESH_NEEDED,
        )
        text = " ".join(lines)
        self.assertIn("freshly collected", text)
        self.assertIn("temporal", text.lower())
        self.assertIn("Other checks pending", text)

    def test_summary_sentence_signals_ok(self) -> None:
        facts = {
            "signals_collection_time_stale": False,
            "temporal_freshness_stale": True,
            "signals_refresh_needed": True,
            "audit_bundle_time_stale": True,
            "audit_product_gap_incomplete": False,
            "audit_security_stub": False,
        }
        s = freshness_summary_sentence(facts, orchestration_status=ORCH_STATUS_STALE_REFRESH_NEEDED)
        self.assertIsNotNone(s)
        assert s is not None
        self.assertIn("signals look fresh", s)
        self.assertIn("temporal", s)


if __name__ == "__main__":
    unittest.main()
