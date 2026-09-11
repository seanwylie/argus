"""Strict ``argus.audit_cursor_scan.v1`` validation."""

from __future__ import annotations

import unittest

from argus.audit.cursor_scan import (
    CURSOR_SCAN_BATCH_SCHEMA,
    CURSOR_SCAN_SCHEMA,
    PROVENANCE_CURSOR_SCAN,
    validate_cursor_scan_v1,
)


def _valid_payload(*, angle_id: str = "security") -> dict:
    return {
        "schema": CURSOR_SCAN_SCHEMA,
        "angle_id": angle_id,
        "summary_lines": [f"{angle_id}: ok"],
        "findings": [
            {
                "title": "Finding A",
                "detail": "",
                "severity": "info",
                "evidence_refs": ["products/x/product.yaml"],
            }
        ],
        "risks": [{"statement": "Risk A", "evidence_refs": []}],
        "enhancements": [
            {
                "title": "Enhancement A",
                "detail": "",
                "severity": "info",
                "evidence_refs": [],
            }
        ],
        "confidence": 0.8,
        "repo_evidence_refs": ["products/x/product.yaml"],
        "limitations": ["no live probes"],
        "provenance": PROVENANCE_CURSOR_SCAN,
    }


class TestCursorScanContract(unittest.TestCase):
    def test_valid_passes(self) -> None:
        p = _valid_payload()
        out = validate_cursor_scan_v1(p, expected_angle_id="security")
        self.assertEqual(out["angle_id"], "security")
        self.assertEqual(out["confidence"], 0.8)

    def test_expected_angle_id_mismatch(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_scan_v1(_valid_payload(), expected_angle_id="cost")
        self.assertIn("mismatch", str(ctx.exception))

    def test_missing_required_key(self) -> None:
        p = _valid_payload()
        del p["limitations"]
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_scan_v1(p, expected_angle_id="security")
        self.assertIn("limitations", str(ctx.exception))

    def test_wrong_provenance(self) -> None:
        p = _valid_payload()
        p["provenance"] = "other"
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_scan_v1(p, expected_angle_id="security")
        self.assertIn("provenance", str(ctx.exception))

    def test_finding_missing_title(self) -> None:
        p = _valid_payload()
        p["findings"] = [{"severity": "info", "evidence_refs": []}]
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_scan_v1(p, expected_angle_id="security")
        self.assertIn("findings[0]", str(ctx.exception))

    def test_confidence_out_of_range(self) -> None:
        p = _valid_payload()
        p["confidence"] = 1.5
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_scan_v1(p, expected_angle_id="security")
        self.assertIn("confidence", str(ctx.exception))

    def test_repo_evidence_refs_must_be_array(self) -> None:
        p = _valid_payload()
        p["repo_evidence_refs"] = "not-a-list"
        with self.assertRaises(ValueError) as ctx:
            validate_cursor_scan_v1(p, expected_angle_id="security")
        self.assertIn("repo_evidence_refs", str(ctx.exception))

    def test_batch_schema_constant(self) -> None:
        self.assertTrue(CURSOR_SCAN_BATCH_SCHEMA.endswith(".v1"))


if __name__ == "__main__":
    unittest.main()
