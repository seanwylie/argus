"""Orchestration eligibility vs ``raw_extensions.import_state`` (importer first-pass gating)."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.orchestrator.eligibility import (
    RC_IMPORT_FIRST_PASS_FAILED,
    RC_IMPORT_FIRST_PASS_PARTIAL,
    evaluate_product_orchestration,
)
from argus.orchestrator.state_models import (
    ACTION_DECISIONS_GENERATE,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ORCH_STATUS_ELIGIBLE,
)


def _product_yaml_with_import(
    root: Path,
    product_id: str,
    *,
    first_pass_status: str,
    imported_at_utc: str = "2026-04-13T12:00:00Z",
) -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: ImportReadinessTest
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths:
    - metrics/
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
raw_extensions:
  import_state:
    schema: argus.import_state.v1
    first_pass_status: {first_pass_status}
    imported_at_utc: {imported_at_utc}
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    for name, body in (
        ("start.sh", "#!/bin/sh\necho start\n" + "x" * 50),
        ("stop.sh", "#!/bin/sh\necho stop\n" + "y" * 50),
        ("analyze.sh", "#!/bin/sh\necho analyze\n" + "z" * 50),
    ):
        (pr / "scripts" / name).write_text(body, encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")


def _write_fresh_signals_findings_decisions(root: Path, product_id: str) -> None:
    ts = "2026-04-13T11:00:00Z"
    sig_p = root / "runs" / "signals" / "latest" / f"{product_id}.json"
    sig_p.parent.mkdir(parents=True, exist_ok=True)
    sig_p.write_text(
        json.dumps({"collected_at_utc": ts, "record_count": 1, "records": []}),
        encoding="utf-8",
    )
    fin_p = root / "runs" / "findings" / "latest" / f"{product_id}.json"
    fin_p.parent.mkdir(parents=True, exist_ok=True)
    fin_p.write_text(json.dumps({"finding_count": 0, "findings": []}), encoding="utf-8")
    dec_p = root / "runs" / "decisions" / "latest" / f"{product_id}.json"
    dec_p.parent.mkdir(parents=True, exist_ok=True)
    dec_p.write_text(json.dumps({"candidates": []}), encoding="utf-8")


@patch("argus.orchestrator.eligibility.utc_now")
class TestOrchestrationImportReadiness(unittest.TestCase):
    def test_success_allows_full_pipeline_actions(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_imp_ok"
            _product_yaml_with_import(root, pid, first_pass_status="success")
            _write_fresh_signals_findings_decisions(root, pid)
            st = evaluate_product_orchestration(root, pid)
            ids = {row["action_id"] for row in st.get("eligible_actions") or []}
            self.assertIn(ACTION_IDEAS_GENERATE, ids)
            self.assertEqual(st.get("import_health", {}).get("gating_tier"), "success")
            self.assertIn("success", (st.get("readiness_reason") or ""))
            facts = st.get("eligibility_facts") or {}
            self.assertTrue(facts.get("ideas_generate_eligible"))

    def test_failed_blocks_phase2_and_downstream(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_imp_fail"
            _product_yaml_with_import(root, pid, first_pass_status="failed")
            _write_fresh_signals_findings_decisions(root, pid)
            st = evaluate_product_orchestration(root, pid)
            ids = {row["action_id"] for row in st.get("eligible_actions") or []}
            self.assertNotIn(ACTION_FINDINGS_GENERATE, ids)
            self.assertNotIn(ACTION_IDEAS_GENERATE, ids)
            self.assertIn(RC_IMPORT_FIRST_PASS_FAILED, st.get("orchestration_status_reason_codes") or [])
            facts = st.get("eligibility_facts") or {}
            self.assertFalse(facts.get("findings_generate_eligible"))
            self.assertFalse(facts.get("ideas_generate_eligible"))

    def test_partial_allows_findings_and_decisions_not_ideas(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_imp_partial"
            _product_yaml_with_import(root, pid, first_pass_status="partial")
            _write_fresh_signals_findings_decisions(root, pid)
            st = evaluate_product_orchestration(root, pid)
            ids = {row["action_id"] for row in st.get("eligible_actions") or []}
            self.assertIn(ACTION_FINDINGS_GENERATE, ids)
            self.assertIn(ACTION_DECISIONS_GENERATE, ids)
            self.assertNotIn(ACTION_IDEAS_GENERATE, ids)
            self.assertIn(RC_IMPORT_FIRST_PASS_PARTIAL, st.get("orchestration_status_reason_codes") or [])
            facts = st.get("eligibility_facts") or {}
            self.assertTrue(facts.get("findings_generate_eligible"))
            self.assertTrue(facts.get("decisions_generate_eligible"))
            self.assertFalse(facts.get("ideas_generate_eligible"))

    def test_failed_still_eligible_for_non_pipeline_actions(self, mock_now) -> None:
        """After a failed first pass, hygiene/refinement actions may remain eligible."""
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_imp_fail_hygiene"
            _product_yaml_with_import(root, pid, first_pass_status="failed")
            _write_fresh_signals_findings_decisions(root, pid)
            st = evaluate_product_orchestration(root, pid)
            self.assertIn(
                st.get("orchestration_status"),
                (ORCH_STATUS_ELIGIBLE, "stale_refresh_needed"),
            )
            ids = {row["action_id"] for row in st.get("eligible_actions") or []}
            self.assertIn(ACTION_REFINEMENT_START_PRODUCT_SPEC, ids)
            wi = st.get("waiting_inputs") or []
            self.assertFalse(any(x.get("kind") == "import_first_pass" for x in wi))

    def test_no_import_state_skips_gating(self, mock_now) -> None:
        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_no_ist"
            pr = root / "products" / pid
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                """
id: p_no_ist
name: NoImportState
owner:
  team: test
lifecycle:
  stage: idea
metrics:
  local_paths: [metrics/]
  primary: []
cost:
  monthly_usd: 1
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/start.sh"
  stop: "./scripts/stop.sh"
  analyze: "./scripts/analyze.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            (pr / "scripts" / "start.sh").write_text("#!/bin/sh\necho x\n", encoding="utf-8")
            (pr / "scripts" / "stop.sh").write_text("#!/bin/sh\necho y\n", encoding="utf-8")
            (pr / "scripts" / "analyze.sh").write_text("#!/bin/sh\necho z\n", encoding="utf-8")
            (pr / "metrics").mkdir(parents=True)
            (pr / "metrics" / "dummy.txt").write_text("1", encoding="utf-8")
            _write_fresh_signals_findings_decisions(root, pid)
            st = evaluate_product_orchestration(root, pid)
            self.assertIsNone(st.get("import_health", {}).get("gating_tier"))
            self.assertIn("not enforced", st.get("readiness_reason") or "")


if __name__ == "__main__":
    unittest.main()
