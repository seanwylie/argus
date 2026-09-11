"""Tests for :mod:`argus.portfolio.evidence_maturity`."""

from __future__ import annotations

import unittest

from argus.portfolio.evidence_maturity import (
    evidence_maturity_hint_from_snapshot,
    thin_evidence_baseline_from_fingerprint,
)


class TestEvidenceMaturity(unittest.TestCase):
    def test_thin_baseline_pending(self) -> None:
        fp = {
            "readiness_tier": "unprofiled",
            "first_pass_status": "pending",
        }
        self.assertTrue(thin_evidence_baseline_from_fingerprint(fp))

    def test_thin_baseline_success_unprofiled_not_thin(self) -> None:
        fp = {
            "readiness_tier": "unprofiled",
            "first_pass_status": "success",
        }
        self.assertFalse(thin_evidence_baseline_from_fingerprint(fp))

    def test_hint_thin_bootstrap(self) -> None:
        snap = {
            "readiness": {"readiness_tier": "unprofiled"},
            "import_health": {"first_pass_status": "pending"},
        }
        self.assertEqual(evidence_maturity_hint_from_snapshot(snap), "thin_bootstrap")

    def test_hint_established(self) -> None:
        snap = {
            "readiness": {"readiness_tier": "observe_gap"},
            "import_health": {"first_pass_status": "success"},
        }
        self.assertEqual(evidence_maturity_hint_from_snapshot(snap), "established")


if __name__ == "__main__":
    unittest.main()
