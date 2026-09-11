"""Unit tests for orchestration readiness (argus.orchestration_readiness.v1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.readiness import (
    ORCHESTRATION_READINESS_SCHEMA,
    RC_DECISION_CONFIDENCE_LOW,
    RC_FIRST_PASS_PARTIAL,
    RC_NO_IMPORT_STATE,
    RC_SIGNALS_ABSENT_OR_STALE,
    RC_SPINE_INCOMPLETE,
    build_readiness_section,
)


def _base_artifacts() -> dict:
    return {"signals": {"phase": "present"}}


class TestReadinessSection(unittest.TestCase):
    def test_legacy_no_import_state(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = build_readiness_section(
                product_id="p_legacy",
                root=root,
                import_state=None,
                import_readiness_tier=None,
                import_health={
                    "findings_present": False,
                    "decisions_present": False,
                    "signals_present": False,
                    "signals_stale": False,
                },
                artifacts=_base_artifacts(),
                eligibility_facts={
                    "signals_collection_time_stale": False,
                    "signals_refresh_needed": False,
                    "temporal_freshness_stale": False,
                    "audit_product_gap_incomplete": False,
                    "audit_security_stub": False,
                },
                orchestration_status="eligible",
                next_action="none",
                waiting_inputs=[],
            )
            self.assertEqual(r["schema"], ORCHESTRATION_READINESS_SCHEMA)
            self.assertEqual(r["readiness_tier"], "unprofiled")
            self.assertIn(RC_NO_IMPORT_STATE, r["readiness_reason_codes"])
            self.assertEqual(r["confidence_gate"], "caution")

    def test_newly_imported_weak_understanding(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = build_readiness_section(
                product_id="p_new",
                root=root,
                import_state={"schema": "argus.import_state.v1", "first_pass_status": "success"},
                import_readiness_tier="complete",
                import_health={
                    "findings_present": False,
                    "decisions_present": False,
                    "signals_present": True,
                    "signals_stale": True,
                },
                artifacts={"signals": {"phase": "absent"}},
                eligibility_facts={
                    "signals_collection_time_stale": True,
                    "signals_refresh_needed": False,
                    "temporal_freshness_stale": False,
                    "audit_product_gap_incomplete": False,
                    "audit_security_stub": False,
                },
                orchestration_status="eligible",
                next_action="signals_collect",
                waiting_inputs=[],
            )
            self.assertEqual(r["readiness_tier"], "observe_gap")
            self.assertGreater(r["understanding_debt"], 0.2)
            self.assertIn(RC_SIGNALS_ABSENT_OR_STALE, r["readiness_reason_codes"])
            self.assertIn(RC_SPINE_INCOMPLETE, r["readiness_reason_codes"])
            self.assertIn(r["confidence_gate"], ("caution", "blocked"))

    def test_partial_first_pass_sparse_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            r = build_readiness_section(
                product_id="p_partial",
                root=root,
                import_state={"schema": "argus.import_state.v1", "first_pass_status": "partial"},
                import_readiness_tier="partial",
                import_health={
                    "findings_present": True,
                    "decisions_present": False,
                    "signals_present": True,
                    "signals_stale": False,
                },
                artifacts=_base_artifacts(),
                eligibility_facts={
                    "signals_collection_time_stale": False,
                    "signals_refresh_needed": False,
                    "temporal_freshness_stale": False,
                    "audit_product_gap_incomplete": False,
                    "audit_security_stub": False,
                },
                orchestration_status="eligible",
                next_action="none",
                waiting_inputs=[],
            )
            self.assertEqual(r["readiness_tier"], "import_incomplete")
            self.assertIn(RC_FIRST_PASS_PARTIAL, r["readiness_reason_codes"])
            self.assertEqual(r["confidence_gate"], "caution")

    def test_strong_mature_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_strong"
            dec_path = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dec_path.parent.mkdir(parents=True, exist_ok=True)
            dec_path.write_text(
                json.dumps(
                    {
                        "candidates": [
                            {"rank": 1, "confidence": 0.9, "title": "A"},
                            {"rank": 2, "confidence": 0.5, "title": "B"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            r = build_readiness_section(
                product_id=pid,
                root=root,
                import_state={"schema": "argus.import_state.v1", "first_pass_status": "success"},
                import_readiness_tier="complete",
                import_health={
                    "findings_present": True,
                    "decisions_present": True,
                    "signals_present": True,
                    "signals_stale": False,
                },
                artifacts=_base_artifacts(),
                eligibility_facts={
                    "signals_collection_time_stale": False,
                    "signals_refresh_needed": False,
                    "temporal_freshness_stale": False,
                    "audit_product_gap_incomplete": False,
                    "audit_security_stub": False,
                },
                orchestration_status="eligible",
                next_action="none",
                waiting_inputs=[],
            )
            self.assertEqual(r["readiness_tier"], "advance_ready")
            self.assertEqual(r["confidence_gate"], "open")
            self.assertLess(r["understanding_debt"], 0.25)
            self.assertEqual(r["metrics"]["top_decision_confidence"], 0.9)

    def test_low_decision_confidence_caution(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_low_conf"
            dec_path = root / "runs" / "decisions" / "latest" / f"{pid}.json"
            dec_path.parent.mkdir(parents=True, exist_ok=True)
            dec_path.write_text(
                json.dumps({"candidates": [{"rank": 1, "confidence": 0.2, "title": "weak"}]}),
                encoding="utf-8",
            )
            r = build_readiness_section(
                product_id=pid,
                root=root,
                import_state={"schema": "argus.import_state.v1", "first_pass_status": "success"},
                import_readiness_tier="complete",
                import_health={
                    "findings_present": True,
                    "decisions_present": True,
                    "signals_present": True,
                    "signals_stale": False,
                },
                artifacts=_base_artifacts(),
                eligibility_facts={
                    "signals_collection_time_stale": False,
                    "signals_refresh_needed": False,
                    "temporal_freshness_stale": False,
                    "audit_product_gap_incomplete": False,
                    "audit_security_stub": False,
                },
                orchestration_status="eligible",
                next_action="none",
                waiting_inputs=[],
            )
            self.assertEqual(r["confidence_gate"], "caution")
            self.assertIn(RC_DECISION_CONFIDENCE_LOW, r["readiness_reason_codes"])
            self.assertEqual(r["readiness_tier"], "advance_ready")


@patch("argus.orchestrator.eligibility.utc_now")
class TestReadinessIntegration(unittest.TestCase):
    def test_orchestration_payload_includes_readiness(self, mock_now) -> None:
        from datetime import datetime, timezone

        mock_now.return_value = datetime(2026, 4, 13, 12, 0, 0, tzinfo=timezone.utc)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = "p_orch_readiness"
            pr = root / "products" / pid
            pr.mkdir(parents=True)
            (pr / "product.yaml").write_text(
                f"""
id: {pid}
name: Readiness
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
raw_extensions:
  import_state:
    schema: argus.import_state.v1
    first_pass_status: success
    imported_at_utc: 2026-04-13T12:00:00Z
""",
                encoding="utf-8",
            )
            (pr / "scripts").mkdir(parents=True)
            for n in ("start.sh", "stop.sh", "analyze.sh"):
                (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
            (pr / "metrics").mkdir(parents=True)
            (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")
            ts = "2026-04-13T11:00:00Z"
            sig_p = root / "runs" / "signals" / "latest" / f"{pid}.json"
            sig_p.parent.mkdir(parents=True, exist_ok=True)
            sig_p.write_text(
                json.dumps({"collected_at_utc": ts, "record_count": 1, "records": []}),
                encoding="utf-8",
            )
            for sub, body in (
                ("findings", {"finding_count": 1, "findings": []}),
                ("decisions", {"candidates": []}),
            ):
                p = root / "runs" / sub / "latest" / f"{pid}.json"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(json.dumps(body), encoding="utf-8")
            st = evaluate_product_orchestration(root, pid)
            self.assertIn("readiness", st)
            rd = st["readiness"]
            self.assertEqual(rd.get("schema"), ORCHESTRATION_READINESS_SCHEMA)
            self.assertIn("readiness_tier", rd)
            self.assertIn("understanding_debt", rd)
            self.assertIn("confidence_gate", rd)
            self.assertIn("readiness_reason_codes", rd)


if __name__ == "__main__":
    unittest.main()
