"""Regression: north-star burn-down and open-gaps stay aligned with shipped reality."""

from __future__ import annotations

import unittest
from pathlib import Path


class TestArgusContextBurnDown(unittest.TestCase):
    def test_north_star_documents_burn_down_table(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "argus-context" / "north-star.md").read_text(encoding="utf-8")
        self.assertIn("Burn-down", text)
        self.assertIn("credibly stateful", text)
        self.assertIn("orchestration state", text)
        self.assertIn("06-open-gaps.md", text)

    def test_open_gaps_audit_reflects_nine_angle_bundle(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "argus-context" / "06-open-gaps.md").read_text(encoding="utf-8")
        self.assertIn("nine", text.lower())
        self.assertIn("bundle.json", text)


if __name__ == "__main__":
    unittest.main()
