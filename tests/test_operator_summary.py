"""Tests for :mod:`argus.dashboard.operator_summary`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.dashboard.operator_summary import (
    OPERATOR_SUMMARY_SCHEMA,
    evaluate_operator_summary,
    render_operator_summary_markdown,
    run_operator_summary,
)
from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA
from argus.policy.learning_synthesis import OPERATOR_LEARNING_SYNTHESIS_SCHEMA
from argus.portfolio.intervention import (
    CHRONICITY_EMERGING,
    INTERVENTION_HUMAN_REVIEW,
    PORTFOLIO_INTERVENTION_SCHEMA,
    SEVERITY_HIGH,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA
from argus.products.creation import PRODUCT_CREATION_PROPOSALS_SCHEMA
from argus.products.deprecation import PRODUCT_DEPRECATION_PROPOSALS_SCHEMA
from argus.world_context.persist import WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


_LEARN_STUB: dict = {
    "schema": OPERATOR_LEARNING_SYNTHESIS_SCHEMA,
    "run_id": "learn_stub",
    "top_lessons_so_far": ["Stub lesson A.", "Stub lesson B."],
    "sparse_signal_warnings": [],
}


def _minimal_product(root: Path, pid: str, *, stage: str = "grow") -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
owner:
  team: test
lifecycle:
  stage: {stage}
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
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 10
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )
    (pr / "scripts").mkdir(parents=True)
    for n in ("s.sh",):
        (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")


def _queue_entry(pid: str, *, rank: int = 1, tier: str = "observe_gap", debt: float = 0.5) -> dict:
    return {
        "product_id": pid,
        "queue_rank": rank,
        "priority_score": 50.0,
        "readiness_tier": tier,
        "understanding_debt": debt,
        "next_action": "none",
    }


class TestOperatorSummary(unittest.TestCase):
    def test_empty_system(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertEqual(pl["schema"], OPERATOR_SUMMARY_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            self.assertIn(
                pl["headline_status"],
                ("healthy", "active", "degraded", "attention_needed", "portfolio_empty"),
            )
            self.assertEqual(pl["confidence_level"], "low")
            ls = pl.get("lifecycle_snapshot") or {}
            self.assertTrue(ls.get("present"))
            self.assertEqual(ls.get("lifecycle_counts", {}).get("proposed"), 0)
            ln = pl.get("learning_snapshot") or {}
            self.assertTrue(ln.get("present"))
            ac = pl.get("artifact_coherence") or {}
            self.assertFalse(ac.get("present"))
            self.assertFalse((pl.get("inputs") or {}).get("artifact_coherence_artifact_present"))
            bas = pl.get("builder_activity_snapshot") or {}
            self.assertFalse(bas.get("artifact_present"))
            self.assertIn("operator_hint", bas)

    def test_creation_heavy_lifecycle_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "early1", stage="idea")
            d = root / "runs" / "products" / "creation"
            d.mkdir(parents=True)
            (d / "latest.json").write_text(
                json.dumps(
                    {
                        "schema": PRODUCT_CREATION_PROPOSALS_SCHEMA,
                        "proposal_count": 2,
                        "proposals": [
                            {"proposal_id": "creation_aaaaaaaaaaaa", "concept_title": "A"},
                            {"proposal_id": "creation_bbbbbbbbbbbb", "concept_title": "B"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=_LEARN_STUB,
            ):
                pl = evaluate_operator_summary(root, limit_history=5)
            lc = pl["lifecycle_snapshot"]["lifecycle_counts"]
            self.assertGreaterEqual(lc.get("proposed", 0), 2)
            self.assertGreaterEqual(lc.get("incubating", 0), 1)
            ent = pl["lifecycle_snapshot"]["products_entering"]
            self.assertTrue(any("proposal:" in x for x in ent))

    def test_repair_heavy_lifecycle_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "r1", stage="grow")
            dep = root / "runs" / "products" / "deprecation"
            dep.mkdir(parents=True)
            (dep / "latest.json").write_text(
                json.dumps(
                    {
                        "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
                        "evaluated_at_utc": "2020-01-01T00:00:00Z",
                        "proposal_count": 1,
                        "proposals": [
                            {
                                "proposal_id": "d1",
                                "product_id": "r1",
                                "deprecation_posture": "repair_instead",
                                "rationale": "",
                                "supporting_evidence": {},
                                "confidence": "high",
                                "recommended_next_step": "",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=_LEARN_STUB,
            ):
                pl = evaluate_operator_summary(root, limit_history=5)
            self.assertEqual(pl["lifecycle_snapshot"]["lifecycle_counts"].get("repairing"), 1)
            self.assertIn("r1", pl["lifecycle_snapshot"]["products_under_repair_pressure"])

    def test_retirement_heavy_lifecycle_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "x1", stage="grow")
            _minimal_product(root, "x2", stage="maintain")
            dep = root / "runs" / "products" / "deprecation"
            dep.mkdir(parents=True)
            (dep / "latest.json").write_text(
                json.dumps(
                    {
                        "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
                        "evaluated_at_utc": "2020-01-01T00:00:00Z",
                        "proposal_count": 2,
                        "proposals": [
                            {
                                "proposal_id": "d1",
                                "product_id": "x1",
                                "deprecation_posture": "retire",
                                "rationale": "",
                                "supporting_evidence": {},
                                "confidence": "high",
                                "recommended_next_step": "",
                            },
                            {
                                "proposal_id": "d2",
                                "product_id": "x2",
                                "deprecation_posture": "harvest",
                                "rationale": "",
                                "supporting_evidence": {},
                                "confidence": "high",
                                "recommended_next_step": "",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=_LEARN_STUB,
            ):
                pl = evaluate_operator_summary(root, limit_history=5)
            self.assertEqual(pl["lifecycle_snapshot"]["lifecycle_counts"].get("retiring"), 1)
            self.assertEqual(pl["lifecycle_snapshot"]["lifecycle_counts"].get("harvesting"), 1)
            ex = set(pl["lifecycle_snapshot"]["products_exiting"])
            self.assertTrue({"x1", "x2"}.issubset(ex))

    def test_learning_strong_takeaways_and_sparse_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = {
                "schema": OPERATOR_LEARNING_SYNTHESIS_SCHEMA,
                "run_id": "z",
                "top_lessons_so_far": [
                    "Strong lesson one from mission objective.",
                    "Strong lesson two from driver correlation.",
                ],
                "sparse_signal_warnings": [
                    "policy_feedback: sparse_history_warning",
                    "conflicting_signal:foo:detail",
                ],
            }
            with patch(
                "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                return_value=fake,
            ):
                pl = evaluate_operator_summary(root, limit_history=5)
            ln = pl["learning_snapshot"]
            self.assertEqual(len(ln["top_lessons"]), 2)
            self.assertEqual(
                ln["strongest_sparse_signal_warning"],
                "conflicting_signal:foo:detail",
            )

    def test_active_progressing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "operator_queue" / "latest.json",
                {
                    "schema": OPERATOR_QUEUE_SCHEMA,
                    "entries": [
                        _queue_entry("a", tier="advance_ready", debt=0.2),
                        _queue_entry("b", rank=2, tier="interpret_gap", debt=0.3),
                    ],
                },
            )
            _write(
                root / "runs" / "portfolio" / "outcomes" / "latest.json",
                {
                    "schema": PORTFOLIO_OUTCOMES_SCHEMA,
                    "run_id": "x",
                    "portfolio_outcome_summary": {
                        "products_evaluated": 2,
                        "positive_count": 2,
                        "negative_count": 0,
                        "no_meaningful_movement_count": 0,
                        "mixed_count": 0,
                    },
                    "per_product_outcomes": [
                        {
                            "product_id": "a",
                            "overall_trajectory": "positive",
                            "blocked_pattern": "never_blocked",
                        },
                        {
                            "product_id": "b",
                            "overall_trajectory": "positive",
                            "blocked_pattern": "never_blocked",
                        },
                    ],
                },
            )
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertGreaterEqual(len(pl["top_products_to_advance"]), 1)
            self.assertEqual(pl["outcome_summary"]["positive_count"], 2)

    def test_heavily_blocked(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "outcomes" / "latest.json",
                {
                    "schema": PORTFOLIO_OUTCOMES_SCHEMA,
                    "run_id": "x",
                    "portfolio_outcome_summary": {
                        "products_evaluated": 3,
                        "positive_count": 0,
                        "negative_count": 0,
                        "no_meaningful_movement_count": 0,
                        "mixed_count": 0,
                    },
                    "per_product_outcomes": [
                        {
                            "product_id": "a",
                            "overall_trajectory": "no_meaningful_movement",
                            "blocked_pattern": "persisted",
                        },
                        {
                            "product_id": "b",
                            "overall_trajectory": "mixed",
                            "blocked_pattern": "persisted",
                        },
                        {
                            "product_id": "c",
                            "overall_trajectory": "mixed",
                            "blocked_pattern": "persisted",
                        },
                    ],
                },
            )
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertGreaterEqual(len(pl["blocked_products"]), 3)
            self.assertIn(pl["headline_status"], ("degraded", "attention_needed"))

    def test_mixed_health(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "outcomes" / "latest.json",
                {
                    "schema": PORTFOLIO_OUTCOMES_SCHEMA,
                    "run_id": "x",
                    "portfolio_outcome_summary": {
                        "products_evaluated": 4,
                        "positive_count": 2,
                        "negative_count": 2,
                        "no_meaningful_movement_count": 0,
                        "mixed_count": 0,
                    },
                    "per_product_outcomes": [],
                },
            )
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertEqual(pl["outcome_summary"]["negative_count"], 2)

    def test_intervention_heavy(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            flagged = []
            for i in range(5):
                flagged.append(
                    {
                        "product_id": f"p{i}",
                        "intervention_category": INTERVENTION_HUMAN_REVIEW,
                        "severity": SEVERITY_HIGH,
                        "chronicity": CHRONICITY_EMERGING,
                        "detection_reason_codes": [f"test.intervention.{i}"],
                        "evidence_summary": "test",
                        "recommended_operator_action": "Human review.",
                    }
                )
            _write(
                root / "runs" / "portfolio" / "intervention" / "latest.json",
                {
                    "schema": PORTFOLIO_INTERVENTION_SCHEMA,
                    "run_id": "20260101T000000Z",
                    "evaluated_at_utc": "2026-01-01T00:00:00Z",
                    "inputs": {},
                    "thresholds": {},
                    "flagged_products": flagged,
                    "stable_benign_products": [],
                },
            )
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertGreaterEqual(pl["intervention_inbox_summary"]["active_items"], 4)
            self.assertIn(pl["headline_status"], ("degraded", "attention_needed"))

    def test_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = run_operator_summary(root, limit_history=5, write_artifacts=True)
            self.assertTrue((root / "runs" / "dashboard" / "operator_summary" / "latest.json").is_file())
            self.assertEqual(pl["schema"], OPERATOR_SUMMARY_SCHEMA)

    def test_patterns_snapshot(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write(
                root / "runs" / "portfolio" / "patterns" / "latest.json",
                {
                    "schema": PORTFOLIO_PATTERNS_SCHEMA,
                    "run_id": "p1",
                    "detected_patterns": [
                        {
                            "pattern_id": "test",
                            "title": "Many products stuck",
                            "severity": "high",
                            "affected_products": ["a", "b"],
                            "recommended_systemic_action": "fix",
                            "evidence": {},
                        }
                    ],
                },
            )
            pl = evaluate_operator_summary(root, limit_history=5)
            self.assertEqual(pl["systemic_patterns_summary"]["pattern_count"], 1)


class TestRenderOperatorSummaryMarkdown(unittest.TestCase):
    def test_zero_state_external_context_hierarchy_order(self) -> None:
        """Synthetic payload: brief → creation bullets → full narrative → detail before **Next step:**."""
        payload: dict = {
            "headline_status": "portfolio_empty",
            "confidence_level": "low",
            "zero_state": True,
            "recommended_next_step": "Add a product when ready.",
            "external_context_advisory": "LONG_ADVISORY_NARRATIVE_UNIQUE_MARKER",
            "world_context_interpretation": {
                "per_entity": {"ent_a": {"summary_lines": ["Grounded line."]}},
                "entities_ordered": ["ent_a"],
                "limitations": ["interpretation limitation"],
            },
            "world_context_creation_candidates": {
                "schema": WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
                "situation_summary": "BRIEF_SITUATION_UNIQUE",
                "primary_candidate_id": "c_primary",
                "candidates": [
                    {
                        "candidate_id": "c_primary",
                        "title": "Primary title",
                        "rationale": "Because signals.",
                        "entities": ["ent_a"],
                        "interpretation_patterns": ["p1"],
                        "evidence_summary": "evidence text",
                        "strength": "low",
                        "next_steps": ["First move only."],
                    }
                ],
                "limitations": ["creation candidates limitation"],
            },
            "lifecycle_snapshot": {},
            "learning_snapshot": {},
            "outcome_summary": {},
            "intervention_inbox_summary": {},
            "systemic_patterns_summary": {},
            "artifact_coherence": {},
            "signal_instrumentation_snapshot": {},
            "blocked_products": [],
            "top_products_to_watch": [],
            "top_products_to_advance": [],
        }
        md = render_operator_summary_markdown(payload)
        self.assertLess(
            md.index("## Portfolio zero-state"),
            md.index("## External world context (advisory)"),
        )
        self.assertLess(
            md.index("**Situation (brief):**"),
            md.index("**Creation directions (advisory, not decisions):**"),
        )
        self.assertLess(
            md.index("**Creation directions (advisory, not decisions):**"),
            md.index("**Full advisory narrative:**"),
        )
        self.assertLess(
            md.index("**Full advisory narrative:**"),
            md.index("## Advisory creation directions (hypothesis-level — full detail)"),
        )
        self.assertLess(
            md.index("## Advisory creation directions (hypothesis-level — full detail)"),
            md.index("**Next step:**"),
        )


if __name__ == "__main__":
    unittest.main()
