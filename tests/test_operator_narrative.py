"""Tests for :mod:`argus.dashboard.narrative`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.dashboard.narrative import (
    OPERATOR_NARRATIVE_SCHEMA,
    _intervention_load_note,
    classify_overall_trajectory,
    evaluate_operator_narrative,
    run_operator_narrative,
)
from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA


def _hist_stub(
    *,
    steadily: int = 0,
    cooling: int = 0,
    rising: int = 0,
    blocked: int = 0,
    thrash: int = 0,
    cycle: int = 3,
    delta: int = 3,
) -> dict:
    return {
        "schema": "argus.portfolio_history.v1",
        "run_id": "h1",
        "loaded_artifact_counts": {
            "cycle": cycle,
            "delta_report": delta,
            "intervention": 3,
            "progression": 3,
            "quiescence": 3,
        },
        "trend_summaries": {
            "steadily_improving": [{"product_id": f"imp{i}", "signal": "x"} for i in range(steadily)],
            "cooling_down": [{"product_id": f"cool{i}", "signal": "tier_or_debt_regressed"} for i in range(cooling)],
            "rising_priority": [{"product_id": f"rise{i}", "signal": "queue_rank_priority_up"} for i in range(rising)],
            "chronically_blocked": [{"product_id": f"blk{i}", "blocked_ratio": 0.8} for i in range(blocked)],
            "thrashing_or_oscillating": [{"product_id": f"thr{i}"} for i in range(thrash)],
        },
    }


def _outcomes_stub(*, n: int, pos: int, neg: int, flat: int) -> dict:
    return {
        "schema": "argus.portfolio_outcomes.v1",
        "run_id": "o1",
        "portfolio_outcome_summary": {
            "products_evaluated": n,
            "positive_count": pos,
            "negative_count": neg,
            "no_meaningful_movement_count": flat,
        },
    }


def _patterns_stub(*, patterns: list | None = None) -> dict:
    return {
        "schema": "argus.portfolio_patterns.v1",
        "run_id": "p1",
        "detected_patterns": patterns or [],
        "isolated_negative_products": [],
    }


def _cycle_row(overall: str) -> tuple[str, dict]:
    return (
        "c1",
        {
            "schema": "argus.portfolio_cycle.v1",
            "summary": {"overall_operator_recommendation": overall},
        },
    )


def _delta_stub(pid_imp: str, pid_reg: str) -> tuple[str, dict]:
    return (
        "d1",
        {
            "schema": "argus.portfolio_delta_report.v1",
            "what_improved": [{"product_id": pid_imp}],
            "what_regressed": [{"product_id": pid_reg}],
        },
    )


def _iv_stub(flagged_n: int) -> tuple[str, dict]:
    return (
        "i1",
        {
            "schema": "argus.portfolio_intervention.v1",
            "flagged_products": [{"product_id": "x"}] * flagged_n,
        },
    )


class TestOperatorNarrative(unittest.TestCase):
    def test_classify_improving(self) -> None:
        traj = classify_overall_trajectory(
            portfolio_summary={
                "products_evaluated": 10,
                "positive_count": 6,
                "negative_count": 1,
                "no_meaningful_movement_count": 2,
            },
            trend_summaries=_hist_stub(steadily=2, cooling=0, rising=2)["trend_summaries"],
            cycle_counts={"run_again": 4, "wait": 1},
        )
        self.assertEqual(traj, "improving")

    def test_classify_degrading(self) -> None:
        traj = classify_overall_trajectory(
            portfolio_summary={
                "products_evaluated": 10,
                "positive_count": 1,
                "negative_count": 6,
                "no_meaningful_movement_count": 2,
            },
            trend_summaries=_hist_stub(steadily=0, cooling=4, rising=0)["trend_summaries"],
            cycle_counts={"request_human_review": 2},
        )
        self.assertEqual(traj, "degrading")

    def test_classify_stagnating(self) -> None:
        traj = classify_overall_trajectory(
            portfolio_summary={
                "products_evaluated": 10,
                "positive_count": 1,
                "negative_count": 1,
                "no_meaningful_movement_count": 7,
            },
            trend_summaries={},
            cycle_counts={"wait": 4, "run_again": 1},
        )
        self.assertEqual(traj, "stagnating")

    def test_classify_mixed(self) -> None:
        traj = classify_overall_trajectory(
            portfolio_summary={
                "products_evaluated": 10,
                "positive_count": 4,
                "negative_count": 4,
                "no_meaningful_movement_count": 2,
            },
            trend_summaries=_hist_stub(steadily=1, cooling=1)["trend_summaries"],
            cycle_counts={"run_again": 2, "wait": 2},
        )
        self.assertEqual(traj, "mixed")

    def test_intervention_load_note_rising(self) -> None:
        counts = [1, 1, 2, 2, 8, 9]
        note = _intervention_load_note(counts)
        self.assertIsNotNone(note)
        self.assertIn("up", note.lower())

    def test_end_to_end_with_mocks_improving(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)

            def _fake_load(_repo: Path, kind: str, *, limit: int = 30) -> list:
                if kind == "cycle":
                    return [_cycle_row("run_again")] * 3
                if kind == "delta_report":
                    return [_delta_stub("a", "b")] * 2
                if kind == "intervention":
                    return [_iv_stub(2)] * 4
                return []

            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(steadily=2, rising=2, cycle=5, delta=5),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=10, pos=7, neg=1, flat=1),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch(
                    "argus.dashboard.narrative.load_latest_n_artifacts",
                    side_effect=_fake_load,
                ),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={
                        "schema": "argus.portfolio_lifecycle.v1",
                        "run_id": "lc",
                        "lifecycle_counts": {},
                        "products_entering": [],
                        "products_exiting": [],
                        "portfolio_strategy_posture": None,
                    },
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={
                        "schema": "argus.operator_learning_synthesis.v1",
                        "run_id": "ls",
                        "top_lessons_so_far": ["Test lesson from synthesis."],
                        "sparse_signal_warnings": [],
                    },
                ),
            ):
                pl = evaluate_operator_narrative(root, limit_history=10)
            self.assertEqual(pl["schema"], OPERATOR_NARRATIVE_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            self.assertEqual(pl["sections"]["overall_trajectory"], "improving")
            self.assertTrue(pl["narrative_text"])
            self.assertIn("lifecycle_transitions", pl["sections"])
            self.assertIn("operator_learning", pl["sections"])
            self.assertIn("intervention_pressure", pl["sections"])

    def test_sparse_history_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(cycle=0, delta=0, steadily=0),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=5, pos=1, neg=1, flat=3),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch(
                    "argus.dashboard.narrative.load_latest_n_artifacts",
                    return_value=[],
                ),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={"schema": "argus.portfolio_lifecycle.v1", "run_id": "x"},
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={"schema": "argus.operator_learning_synthesis.v1", "run_id": "y"},
                ),
            ):
                pl = evaluate_operator_narrative(root, limit_history=5)
            self.assertTrue(pl.get("sparse_history_warning"))

    def test_heavy_intervention_recurring(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ivs = [_iv_stub(1), _iv_stub(1), _iv_stub(2), _iv_stub(10), _iv_stub(9)]

            def _fake_load(_repo: Path, kind: str, *, limit: int = 30) -> list:
                if kind == "cycle":
                    return [_cycle_row("run_again")]
                if kind == "intervention":
                    return ivs
                return []

            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(cycle=4, delta=4),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=8, pos=2, neg=3, flat=3),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch(
                    "argus.dashboard.narrative.load_latest_n_artifacts",
                    side_effect=_fake_load,
                ),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={"schema": "argus.portfolio_lifecycle.v1", "run_id": "x"},
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={"schema": "argus.operator_learning_synthesis.v1", "run_id": "y"},
                ),
            ):
                pl = evaluate_operator_narrative(root, limit_history=10)
            rec = " ".join(pl["sections"].get("recurring_issues") or [])
            self.assertTrue("Intervention load" in rec or "intervention" in pl["narrative_text"].lower())

    def test_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=0, pos=0, neg=0, flat=0),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch("argus.dashboard.narrative.load_latest_n_artifacts", return_value=[]),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={"schema": "argus.portfolio_lifecycle.v1", "run_id": "x"},
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={"schema": "argus.operator_learning_synthesis.v1", "run_id": "y"},
                ),
            ):
                run_operator_narrative(root, limit_history=5, write_artifacts=True)
            p = root / "runs" / "dashboard" / "narrative" / "latest.json"
            self.assertTrue(p.is_file())
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], OPERATOR_NARRATIVE_SCHEMA)

    def test_lifecycle_and_learning_wired_in_sections(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(cycle=4, delta=4),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=5, pos=2, neg=1, flat=2),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch("argus.dashboard.narrative.load_latest_n_artifacts", return_value=[]),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={
                        "schema": "argus.portfolio_lifecycle.v1",
                        "run_id": "lc",
                        "lifecycle_counts": {"repairing": 3, "proposed": 1},
                        "products_entering": ["proposal:creation_x"],
                        "products_exiting": [],
                        "portfolio_strategy_posture": "repair",
                    },
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={
                        "schema": "argus.operator_learning_synthesis.v1",
                        "run_id": "ls",
                        "top_lessons_so_far": ["Mission segment alpha shows higher improvement_rate."],
                        "sparse_signal_warnings": [],
                    },
                ),
            ):
                pl = evaluate_operator_narrative(root, limit_history=8)
            lt = pl["sections"]["lifecycle_transitions"]
            self.assertTrue(any("repair" in x.lower() for x in lt))
            ol = pl["sections"]["operator_learning"]
            self.assertTrue(any("improvement_rate" in x for x in ol))
            self.assertIn("Portfolio lifecycle", pl["narrative_text"])

    def test_substrate_integrity_caveat_when_coherence_invalid(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=5, pos=2, neg=1, flat=2),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch("argus.dashboard.narrative.load_latest_n_artifacts", return_value=[]),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={"schema": "argus.portfolio_lifecycle.v1", "run_id": "x"},
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={"schema": "argus.operator_learning_synthesis.v1", "run_id": "y"},
                ),
                patch(
                    "argus.dashboard.narrative.load_artifact_coherence_operational_snapshot",
                    return_value={
                        "present": True,
                        "overall_status": "invalid",
                        "run_id": "coh1",
                        "summary": "x",
                    },
                ),
            ):
                pl = evaluate_operator_narrative(root, limit_history=5)
            self.assertEqual(
                (pl["sections"].get("substrate_integrity") or {}).get("overall_status"),
                "invalid",
            )
            self.assertIn("Artifact coherence is invalid", pl["narrative_text"])
            notes = pl.get("notes") or []
            self.assertFalse(any("No durable" in n for n in notes))

    def test_substrate_note_when_coherence_artifact_missing(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with (
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_history",
                    return_value=_hist_stub(),
                ),
                patch(
                    "argus.dashboard.narrative.load_canonical_portfolio_outcomes",
                    return_value=_outcomes_stub(n=5, pos=2, neg=1, flat=2),
                ),
                patch(
                    "argus.dashboard.narrative.evaluate_portfolio_patterns",
                    return_value=_patterns_stub(),
                ),
                patch("argus.dashboard.narrative.load_latest_n_artifacts", return_value=[]),
                patch(
                    "argus.portfolio.lifecycle.evaluate_portfolio_lifecycle",
                    return_value={"schema": "argus.portfolio_lifecycle.v1", "run_id": "x"},
                ),
                patch(
                    "argus.policy.learning_synthesis.evaluate_operator_learning_synthesis",
                    return_value={"schema": "argus.operator_learning_synthesis.v1", "run_id": "y"},
                ),
                patch(
                    "argus.dashboard.narrative.load_artifact_coherence_operational_snapshot",
                    return_value={"present": False},
                ),
            ):
                pl = evaluate_operator_narrative(root, limit_history=5)
            notes = pl.get("notes") or []
            self.assertTrue(any("artifact_coherence/latest.json" in n for n in notes))
            self.assertNotIn("Substrate integrity is degraded", pl["narrative_text"])
