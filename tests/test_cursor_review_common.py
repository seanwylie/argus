"""Shared cursor-review section validators (argus.core.cursor_review_common)."""

from __future__ import annotations

import unittest
from pathlib import Path

from argus.core.cursor_review_common import (
    validate_cursor_review_findings,
    validate_cursor_review_risks,
)


class TestCursorReviewCommon(unittest.TestCase):
    def test_findings_minimal_valid(self) -> None:
        raw = [
            {
                "title": "t",
                "detail": "",
                "severity": "info",
                "evidence_refs": ["p/x"],
            }
        ]
        out = validate_cursor_review_findings(raw, "findings")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["title"], "t")
        self.assertEqual(out[0]["evidence_refs"], ["p/x"])

    def test_findings_wrong_root_type_message(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_review_findings({}, "findings")
        self.assertEqual(str(ctx.exception), "findings must be an array")

    def test_findings_enhancements_label(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_review_findings("x", "enhancements")
        self.assertEqual(str(ctx.exception), "enhancements must be an array")

    def test_risks_string_and_object(self) -> None:
        out = validate_cursor_review_risks(["one", {"statement": "two", "evidence_refs": ["a"]}])
        self.assertEqual(out[0]["statement"], "one")
        self.assertEqual(out[1]["statement"], "two")
        self.assertEqual(out[1]["evidence_refs"], ["a"])

    def test_risks_not_array(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_review_risks({})
        self.assertEqual(str(ctx.exception), "risks must be an array")

    def test_orchestration_cursor_review_does_not_import_signals_package(self) -> None:
        """Structural guard: orchestration must not depend on signals' cursor_review module."""
        p = Path(__file__).resolve().parents[1] / "argus" / "orchestrator" / "cursor_review.py"
        text = p.read_text(encoding="utf-8")
        self.assertNotIn("signals.cursor_review", text)


if __name__ == "__main__":
    unittest.main()
