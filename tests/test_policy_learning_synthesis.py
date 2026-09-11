"""Tests for :mod:`argus.policy.learning_synthesis`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.experiment import MISSION_EXPERIMENT_SCHEMA
from argus.policy.experiment import OPERATOR_POLICY_EXPERIMENT_SCHEMA
from argus.policy.learning_synthesis import (
    OPERATOR_LEARNING_SYNTHESIS_SCHEMA,
    build_by_driver_lessons,
    build_by_guardrail_lessons,
    build_by_objective_lessons,
    build_mission_experiment_takeaways,
    build_repeated_policy_tuning_signals,
    build_sparse_signal_warnings,
    build_systemic_learning_patterns,
    build_top_lessons_so_far,
    evaluate_operator_learning_synthesis,
    run_operator_learning_synthesis,
)


class TestByObjectiveLessons(unittest.TestCase):
    def test_objective_level_extraction(self) -> None:
        eff = {
            "current": {
                "by_objective": {
                    "revenue": {
                        "products_n": 3,
                        "improvement_rate": 0.33,
                        "stagnation_rate": 0.33,
                        "negative_rate": 0.34,
                    },
                },
            },
        }
        rows = build_by_objective_lessons(eff)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mission_objective"], "revenue")
        self.assertEqual(rows[0]["products_n"], 3)
        self.assertIn("associative only", rows[0]["lesson_summary"])


class TestDriverLessons(unittest.TestCase):
    def test_driver_support_lesson(self) -> None:
        eff = {
            "current": {
                "by_driver": {
                    "conversion and retention": {
                        "products_n": 2,
                        "improvement_rate": 0.5,
                        "driver_support_any_rate": 0.5,
                    },
                },
                "driver_support_frequency_by_id": {
                    "conversion and retention": {
                        "products_with_driver_n": 2,
                        "driver_support_signal_rate": 0.6,
                    },
                },
            },
        }
        rows = build_by_driver_lessons(eff)
        self.assertTrue(any(r.get("correlates_support_signals") for r in rows))


class TestGuardrailLessons(unittest.TestCase):
    def test_guardrail_pressure_lesson(self) -> None:
        eff = {
            "current": {
                "by_guardrail": {
                    "no_dark_patterns": {
                        "products_n": 2,
                        "negative_rate": 0.5,
                        "guardrail_risk_any_rate": 0.5,
                    },
                },
                "guardrail_risk_frequency_by_id": {
                    "no_dark_patterns": {
                        "products_with_guardrail_n": 2,
                        "guardrail_risk_signal_rate": 0.55,
                    },
                },
            },
        }
        rows = build_by_guardrail_lessons(eff)
        self.assertTrue(any(r.get("correlates_pressure_or_stagnation") for r in rows))


class TestRepeatedRecommendations(unittest.TestCase):
    def test_repeated_policy_areas(self) -> None:
        rec = {
            "recommendations": [
                {"affected_policy_area": "quiescence", "recommendation_id": "a"},
                {"affected_policy_area": "quiescence", "recommendation_id": "b"},
                {"affected_policy_area": "intervention", "recommendation_id": "c"},
            ],
        }
        rows = build_repeated_policy_tuning_signals(rec)
        by_area = {r["affected_policy_area"]: r["recommendation_count"] for r in rows}
        self.assertEqual(by_area.get("quiescence"), 2)


class TestMissionExperimentTakeaways(unittest.TestCase):
    def test_mission_experiment_difference_takeaway(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "mission" / "experiments"
            d.mkdir(parents=True)
            payload = {
                "schema": MISSION_EXPERIMENT_SCHEMA,
                "run_id": "m1",
                "evaluated_at_utc": "2020-01-01T00:00:00Z",
                "experiment_variant_kind": "compositions",
                "compared_profiles": ["c1", "c2"],
                "behavior_change_summary": ["Queue ranking changed under alternate missions."],
                "queue_top_slice_order_deltas_vs_reference": [
                    {"profiles": ["c1", "c2"], "top_slice_order_equal": False},
                ],
                "recommendation_deltas_vs_reference": [
                    {"only_in_first": ["r1"], "only_in_second": ["r2"]},
                ],
            }
            (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
            tw = build_mission_experiment_takeaways(root)
            self.assertTrue(tw["artifact_present"])
            self.assertEqual(tw["variant_kind"], "compositions")
            self.assertTrue(any("recommendation ids differed" in x for x in tw["takeaways"]))
            self.assertTrue(any("Structured mission compositions" in x for x in tw["takeaways"]))


class TestSparseAndConflicting(unittest.TestCase):
    def test_sparse_warnings_merge(self) -> None:
        eff = {"caveats": ["sparse_segment: objective=x has only one product"]}
        fb = {"sparse_history_warning": True, "metrics_over_time": []}
        rec = {
            "sparse_signal_warning": True,
            "conflicting_signals": [{"code": "c1", "detail": "d"}],
        }
        w = build_sparse_signal_warnings(eff, fb, rec)
        self.assertTrue(any("sparse" in x.lower() for x in w))
        self.assertTrue(any("conflicting_signal" in x for x in w))


class TestSystemicPatterns(unittest.TestCase):
    def test_merges_effectiveness_and_patterns(self) -> None:
        eff = {"notable_patterns": ["descriptive: improvement_rate higher for objective=a"]}
        pat = {
            "detected_patterns": [
                {"pattern_id": "p1", "title": "T", "severity": "high", "recommended_systemic_action": "act"},
            ],
        }
        rows = build_systemic_learning_patterns(eff, pat)
        self.assertTrue(any(r.get("source") == "operator_policy_effectiveness" for r in rows))
        self.assertTrue(any(r.get("pattern_id") == "p1" for r in rows))


class TestTopLessons(unittest.TestCase):
    def test_top_lessons_aggregate(self) -> None:
        top = build_top_lessons_so_far(
            by_obj=[{"lesson_summary": "obj lesson"}],
            by_drv=[{"correlates_support_signals": True, "lesson_summary": "drv"}],
            by_gr=[],
            repeated=[{"affected_policy_area": "x", "recommendation_count": 2}],
            mission_exp={"takeaways": ["exp"]},
            systemic=[{"pattern": "eff pattern"}],
            sparse=["warn"],
        )
        self.assertTrue(any("obj lesson" in t for t in top))
        self.assertTrue(any("Policy area" in t for t in top))


class TestEvaluateIntegration(unittest.TestCase):
    def test_schema_and_optional_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            payload = evaluate_operator_learning_synthesis(root, limit_history=5)
            self.assertEqual(payload["schema"], OPERATOR_LEARNING_SYNTHESIS_SCHEMA)
            self.assertIn("by_objective_lessons", payload)
            self.assertFalse(payload["mission_experiment_takeaways"]["artifact_present"])

    def test_run_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            run_operator_learning_synthesis(root, limit_history=5, write_artifacts=True)
            p = root / "runs" / "policy" / "learning_synthesis" / "latest.json"
            self.assertTrue(p.is_file())
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], OPERATOR_LEARNING_SYNTHESIS_SCHEMA)


class TestPolicyExperimentContext(unittest.TestCase):
    def test_policy_experiment_loaded(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            d = root / "runs" / "policy" / "experiments"
            d.mkdir(parents=True)
            pl = {
                "schema": OPERATOR_POLICY_EXPERIMENT_SCHEMA,
                "run_id": "p1",
                "per_profile": [{"profile_id": "default", "quiescence_recommendation": "wait"}],
            }
            (d / "latest.json").write_text(json.dumps(pl), encoding="utf-8")
            from argus.policy.learning_synthesis import build_policy_experiment_takeaway

            ctx = build_policy_experiment_takeaway(root)
            self.assertTrue(ctx["artifact_present"])


if __name__ == "__main__":
    unittest.main()
