"""Orchestration Cursor review: prompt, ingest validation, persistence across reruns."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.orchestrator.cursor_review import (
    ORCHESTRATION_CURSOR_REVIEW_SCHEMA,
    ORCHESTRATION_REVIEW_BUNDLE_SCHEMA,
    PROVENANCE_ORCHESTRATION_CURSOR_REVIEW,
    fingerprint_orchestration_state,
    validate_orchestration_cursor_review_v1,
)
from argus.orchestrator.review_ingest import (
    ingest_orchestration_cursor_review,
    orchestration_review_bundle_path,
    orchestration_review_operator_summary,
)
from argus.orchestrator.review_prompt import build_orchestration_review_prompt


def _minimal_product_yaml(pid: str) -> str:
    return "\n".join(
        [
            f"id: {pid}",
            "name: P",
            "owner:",
            "  team: t",
            "lifecycle:",
            "  stage: idea",
            "metrics:",
            "  local_paths: []",
            "  primary: []",
            "cost:",
            "  monthly_usd: 0",
            "  notes: ''",
            "signals:",
            "  - type: filesystem",
            "    enabled: true",
            "actions:",
            '  start: "./scripts/s.sh"',
            '  stop: "./scripts/s.sh"',
            '  analyze: "./scripts/s.sh"',
            "constraints:",
            "  max_monthly_cost_usd: 1",
            "  min_activity_threshold: 0",
            "",
        ]
    )


def _minimal_product(root: Path, pid: str) -> None:
    d = root / "products" / pid
    d.mkdir(parents=True)
    (d / "product.yaml").write_text(_minimal_product_yaml(pid), encoding="utf-8")
    (d / "scripts").mkdir(parents=True)
    (d / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")


def _valid_review_payload(pid: str) -> dict:
    return {
        "schema": ORCHESTRATION_CURSOR_REVIEW_SCHEMA,
        "product_id": pid,
        "summary_lines": ["orchestration: review ok"],
        "findings": [
            {
                "title": "f1",
                "detail": "",
                "severity": "info",
                "evidence_refs": ["products/x/"],
            }
        ],
        "risks": [{"statement": "r1", "evidence_refs": []}],
        "enhancements": [
            {
                "title": "e1",
                "detail": "",
                "severity": "info",
                "evidence_refs": [],
            }
        ],
        "confidence": 0.75,
        "repo_evidence_refs": ["products/x/product.yaml"],
        "limitations": ["interpretation only"],
        "provenance": PROVENANCE_ORCHESTRATION_CURSOR_REVIEW,
    }


class TestOrchestrationCursorReview(unittest.TestCase):
    def test_validate_rejects_wrong_provenance(self) -> None:
        raw = _valid_review_payload("p1")
        raw["provenance"] = "wrong"
        with self.assertRaises(ValueError):
            validate_orchestration_cursor_review_v1(raw, expected_product_id="p1")

    def test_prompt_contains_contract_and_bounded_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "myprod"
            _minimal_product(root, pid)
            text = build_orchestration_review_prompt(root, pid)
            self.assertIn(ORCHESTRATION_CURSOR_REVIEW_SCHEMA, text)
            self.assertIn(PROVENANCE_ORCHESTRATION_CURSOR_REVIEW, text)
            self.assertIn("eligible_actions", text)

    def test_ingest_persists_and_appends_history_on_rerun(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            raw = _valid_review_payload(pid)
            out1 = ingest_orchestration_cursor_review(root, pid, raw)
            self.assertEqual(out1["schema"], ORCHESTRATION_REVIEW_BUNDLE_SCHEMA)
            p = orchestration_review_bundle_path(root, pid)
            self.assertTrue(p.is_file())
            out2 = ingest_orchestration_cursor_review(root, pid, raw)
            hist = out2.get("ingest_history")
            self.assertIsInstance(hist, list)
            self.assertGreaterEqual(len(hist), 1)

    def test_ingest_no_overwrite_raises_when_present(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            raw = _valid_review_payload(pid)
            ingest_orchestration_cursor_review(root, pid, raw)
            with self.assertRaises(ValueError):
                ingest_orchestration_cursor_review(root, pid, raw, overwrite=False)

    def test_fingerprint_matches_after_reeval(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            from argus.orchestrator.eligibility import evaluate_product_orchestration

            s1 = evaluate_product_orchestration(root, pid)
            fp1 = fingerprint_orchestration_state(s1)
            s2 = evaluate_product_orchestration(root, pid)
            fp2 = fingerprint_orchestration_state(s2)
            self.assertEqual(fp1, fp2)

    def test_operator_summary_detects_stale_review(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p1"
            _minimal_product(root, pid)
            raw = _valid_review_payload(pid)
            ingest_orchestration_cursor_review(root, pid, raw)
            summ = orchestration_review_operator_summary(root, pid)
            self.assertTrue(summ.get("orchestration_review_present"))
            self.assertIsNotNone(summ.get("orchestration_review_matches_state_fingerprint"))


if __name__ == "__main__":
    unittest.main()
