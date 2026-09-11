"""Regression: proof-run doc stays aligned with bundle semantics and audit reality."""

from __future__ import annotations

import unittest
from pathlib import Path

from argus.audit.angles.stub import STUB_ANGLE_IDS


class TestProofRunDoc(unittest.TestCase):
    def test_zero_findings_documented(self) -> None:
        root = Path(__file__).resolve().parents[1]
        path = root / "docs" / "proof-run.md"
        self.assertTrue(path.is_file(), msg="docs/proof-run.md must exist")
        text = path.read_text(encoding="utf-8")
        self.assertIn("Zero findings", text)
        self.assertIn("finding_count", text)

    def test_audit_jq_assertions_match_implementation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertNotIn('.angles.performance.angle_status == "stub"', text)
        self.assertNotIn("Remaining angles still stub", text)
        self.assertIn(
            '.angles.performance.angle_status == "active" or .angles.performance.angle_status == "partial"',
            text,
        )
        self.assertIn(
            '.angles.reliability.angle_status == "active" or .angles.reliability.angle_status == "partial"',
            text,
        )
        self.assertIn(
            '.angles.store_business.angle_status == "active" or .angles.store_business.angle_status == "partial"',
            text,
        )
        self.assertIn(
            '.angles.ux.angle_status == "active" or .angles.ux.angle_status == "partial"',
            text,
        )

    def test_architecture_audit_row_matches_bundle_reality(self) -> None:
        root = Path(__file__).resolve().parents[1]
        arch = (root / "docs" / "architecture.md").read_text(encoding="utf-8")
        self.assertNotIn("six stubs", arch)
        self.assertIn("nine deterministic angles", arch)
        aa = (root / "docs" / "audit-architecture.md").read_text(encoding="utf-8")
        self.assertNotIn("six stubs", aa)
        for needle in ("`store_business`", "`ux`", "`reliability`"):
            self.assertIn(needle, aa, msg=f"audit-architecture.md should list {needle}")

    def test_proof_run_signals_temporal_assertions_documented(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn("argus.signal_collection.v1", text)
        self.assertIn("argus.temporal_bundle.v1", text)
        self.assertIn("canonical", text)
        self.assertIn("worst_freshness_status", text)
        self.assertIn("freshness_bucket", text)
        self.assertIn("argus.signal_continuity.v1", text)
        self.assertIn("manifest_declaration", text)
        self.assertIn('collection_status == "missing"', text)

    def test_proof_run_names_product_signal_manifest_correctly(self) -> None:
        """Product declarations live in signals.yaml (or inline product.yaml), not signal_manifest.yaml."""
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertNotIn("signal_manifest.yaml", text)
        self.assertIn("signals.yaml", text)

    def test_proof_run_documents_optional_signal_cursor_review(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn("signals cursor-ingest", text)
        self.assertIn("runs/signals/review/", text)
        self.assertIn("argus.signal_review_bundle.v1", text)

    def test_model_contracts_document_signal_review_schemas(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mc = (root / "docs" / "model-contracts.md").read_text(encoding="utf-8")
        self.assertIn("argus.signal_cursor_review.v1", mc)
        self.assertIn("argus.signal_review_bundle.v1", mc)
        self.assertIn("Deterministic signals vs optional Cursor signal review", mc)

    def test_model_contracts_document_orchestration_schemas(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mc = (root / "docs" / "model-contracts.md").read_text(encoding="utf-8")
        self.assertIn("argus.orchestration_state.v1", mc)
        self.assertIn("argus.orchestration_index.v1", mc)
        self.assertIn("orchestration_status", mc)
        self.assertIn("waiting_inputs", mc)
        self.assertIn("blocked_waiting_approval", mc)
        self.assertIn("argus.orchestration_cursor_review.v1", mc)
        self.assertIn("argus.orchestration_review_bundle.v1", mc)

    def test_proof_run_documents_orchestration_state_checks(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn("argus orchestration state", text)
        self.assertIn("runs/orchestration/latest", text)
        self.assertIn("argus.orchestration_state.v1", text)
        self.assertIn("eligible_actions", text)
        self.assertIn("next_action", text)
        self.assertIn("escalation_eligible", text)
        self.assertIn("artifacts.refinement.review_state", text)
        self.assertIn("orchestration_status_reason", text)
        self.assertIn("argus.orchestration_task.v1", text)
        self.assertIn("runs/orchestration/tasks/latest", text)
        self.assertIn("argus loop run", text)

    def test_proof_run_documents_orchestration_advancement_and_cursor_review_artifacts(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn("runs/orchestration/latest/advancements/", text)
        self.assertIn("argus.orchestration_advancement.v1", text)
        self.assertIn("runs/orchestration/review/", text)
        self.assertIn("argus.orchestration_review_bundle.v1", text)

    def test_proof_run_documents_orchestration_progression_artifact_checks(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn("argus orchestration run-progression", text)
        self.assertIn("runs/orchestration/latest/progression_runs/", text)
        self.assertIn("runs/orchestration/progression_runs/generations/", text)
        self.assertIn("argus.orchestration_progression_run_artifact.v1", text)
        self.assertIn('cmp -s "$P" "$G"', text)
        self.assertIn("orchestration_fingerprint", text)
        self.assertIn("final_state_summary", text)
        self.assertIn("terminal_status", text)
        self.assertIn("terminal_reason", text)
        self.assertIn("step_count", text)
        self.assertIn("actions_taken", text)
        self.assertIn("--no-write-artifact", text)

    def test_proof_run_documents_audit_ingest_cursor(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "proof-run.md").read_text(encoding="utf-8")
        self.assertIn("ingest-cursor", text)
        self.assertIn("ingest-agent", text)

    def test_docs_readme_lists_orchestration_index(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertIn("index.json", text)
        self.assertIn("argus orchestration", text)
        self.assertIn("eligibility-driven", text)
        self.assertIn("tasks/latest", text)
        self.assertIn("cursor-ingest", text)
        self.assertIn("runs/orchestration/review/", text)
        self.assertIn("run-progression", text)
        self.assertIn("durable progression artifact", text)

    def test_docs_readme_escalation_complements_orchestration_snapshot(self) -> None:
        root = Path(__file__).resolve().parents[1]
        text = (root / "docs" / "README.md").read_text(encoding="utf-8")
        self.assertIn("not an execution queue", text)
        self.assertIn("escalation_eligible", text)

    def test_model_contracts_shared_vocabulary_lists_orchestration_terms(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mc = (root / "docs" / "model-contracts.md").read_text(encoding="utf-8")
        self.assertIn("ideas_generate_eligible", mc)
        self.assertIn("`ideas` \\| `govern`", mc)
        self.assertIn("| **orchestration_status** |", mc)
        self.assertIn("| **waiting_inputs** |", mc)
        self.assertIn("| **next_action** |", mc)
        self.assertIn("| **selected_action** |", mc)
        self.assertIn("argus.orchestration_progression_run.v1", mc)
        self.assertIn("argus.orchestration_progression_run_artifact.v1", mc)
        self.assertIn("argus.orchestration_execution_feedback.v1", mc)
        self.assertIn("orchestration_feedback_recent_failed", mc)
        self.assertIn("orchestration_feedback_by_action_id", mc)
        self.assertIn("proof-run.md", mc)
        self.assertIn("section 5", mc)

    def test_stub_angle_ids_empty_matches_proof_run_audit_checks(self) -> None:
        """Proof-run jq excludes stub lines from implemented angles; empty STUB_ANGLE_IDS matches."""
        self.assertEqual(STUB_ANGLE_IDS, ())

    def test_orchestration_cli_help_distinguishes_state_vs_advance(self) -> None:
        root = Path(__file__).resolve().parents[1]
        orch_cli = (root / "argus" / "cli" / "parsers" / "orchestration.py").read_text(encoding="utf-8")
        self.assertIn("Does not run signals/audit/refine", orch_cli)
        self.assertIn("Without --execute, does not invoke signals collect", orch_cli)
        self.assertIn("cursor-ingest", orch_cli)
        self.assertIn("run-progression", orch_cli)
        self.assertIn("artifact_paths when written", orch_cli)
        sc = (root / "argus" / "orchestrator" / "state_cli.py").read_text(encoding="utf-8")
        self.assertIn("orchestration advance", sc)
        self.assertIn("record next-step intent", sc)
        self.assertIn("cursor-ingest", sc)
        self.assertIn("run-progression", sc)
        self.assertIn("run-progression", sc.split("\n", 1)[0])

    def test_model_contracts_advancement_documents_execute_statuses(self) -> None:
        root = Path(__file__).resolve().parents[1]
        mc = (root / "docs" / "model-contracts.md").read_text(encoding="utf-8")
        self.assertIn("orchestration_advancement.v1", mc)
        self.assertIn("queued_unhandled", mc)
        self.assertIn("`executed`", mc)


if __name__ == "__main__":
    unittest.main()
