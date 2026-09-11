"""Tests for :mod:`argus.policy.recommendations`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA
from argus.policy.operator_policy import default_operator_policy
from argus.policy.recommendations import (
    OPERATOR_POLICY_RECOMMENDATIONS_SCHEMA,
    _build_recommendations,
    _observed_inefficiencies,
    evaluate_operator_policy_recommendations,
    render_operator_policy_recommendations_markdown,
    run_operator_policy_recommendations,
)


def _fb(
    *,
    products_n: int = 10,
    interpretation: str = "healthy_mixed",
    flat: float = 0.5,
    pos_ratio: float = 0.4,
    readiness_delta: float = 0.0,
    nm_delta: float = 0.0,
    suspicious: list | None = None,
    sparse: bool = False,
) -> dict:
    rates = {
        "no_movement_frequency": flat,
        "portfolio_positive_ratio": pos_ratio,
    }
    return {
        "metrics_over_time": [{"run_id": "x", "rates": rates}],
        "policy_effectiveness_indicators": {
            "products_evaluated": products_n,
            "interpretation": interpretation,
            "stagnation_pressure": flat,
        },
        "trend_summaries": {
            "readiness_improvement_rate": {"delta": readiness_delta},
            "no_movement_frequency": {"delta": nm_delta},
        },
        "suspicious_correlations": suspicious or [],
        "sparse_history_warning": sparse,
    }


class TestPolicyRecommendations(unittest.TestCase):
    def test_stagnating_portfolio_suggests_lower_debt_materiality(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.5, pos_ratio=0.3, interpretation="stressed")
        pat = {"detected_patterns": []}
        iv = {"trend": "flat"}
        recs = _build_recommendations(pol, fb, pat, iv)
        ids = [r["recommendation_id"] for r in recs]
        self.assertIn("rec.quiescence.debt_delta_material.decrease_for_stagnation", ids)
        row = next(r for r in recs if r["recommendation_id"].endswith("decrease_for_stagnation"))
        self.assertTrue(row["safe_to_try"])
        self.assertLess(float(row["suggested_value"]), float(row["current_value"]))

    def test_improving_portfolio_suggests_slight_increase(self) -> None:
        pol = default_operator_policy()
        fb = _fb(
            products_n=10,
            flat=0.2,
            pos_ratio=0.5,
            readiness_delta=0.06,
            nm_delta=0.0,
            interpretation="healthy_mixed",
        )
        pat = {"detected_patterns": []}
        iv = {"trend": "flat"}
        recs = _build_recommendations(pol, fb, pat, iv)
        ids = [r["recommendation_id"] for r in recs]
        self.assertIn("rec.quiescence.debt_delta_material.slight_increase_when_improving", ids)

    def test_weak_signal_lowers_confidence(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=2, flat=0.5, interpretation="neutral_or_unknown")
        pat = {"detected_patterns": []}
        iv = {"trend": "unknown"}
        recs = _build_recommendations(pol, fb, pat, iv)
        stag = [r for r in recs if "decrease_for_stagnation" in r["recommendation_id"]]
        self.assertTrue(stag)
        self.assertLess(float(stag[0]["confidence"]), 0.5)

    def test_strong_signal_higher_confidence_than_weak(self) -> None:
        pol = default_operator_policy()
        fb_w = _fb(products_n=2, flat=0.5, interpretation="neutral_or_unknown")
        fb_s = _fb(products_n=12, flat=0.5, interpretation="stressed")
        pat: dict = {"detected_patterns": []}
        iv = {"trend": "flat"}
        c_w = float(
            next(
                r["confidence"]
                for r in _build_recommendations(pol, fb_w, pat, iv)
                if "decrease_for_stagnation" in r["recommendation_id"]
            )
        )
        c_s = float(
            next(
                r["confidence"]
                for r in _build_recommendations(pol, fb_s, pat, iv)
                if "decrease_for_stagnation" in r["recommendation_id"]
            )
        )
        self.assertGreater(c_s, c_w)

    def test_no_recommendations_empty_signals(self) -> None:
        pol = default_operator_policy()
        fb = {
            "metrics_over_time": [{"run_id": "x", "rates": {}}],
            "policy_effectiveness_indicators": {"products_evaluated": 0, "interpretation": "neutral_or_unknown"},
            "trend_summaries": {},
            "suspicious_correlations": [],
            "sparse_history_warning": True,
        }
        pat = {"detected_patterns": []}
        iv = {"trend": "unknown"}
        recs = _build_recommendations(pol, fb, pat, iv)
        self.assertEqual(recs, [])

    def test_conflicting_signals_adds_review_rec(self) -> None:
        pol = default_operator_policy()
        fb = _fb(
            products_n=10,
            flat=0.3,
            readiness_delta=0.05,
            suspicious=[{"code": "feedback.suspicious.x"}],
        )
        pat = {"detected_patterns": []}
        iv = {"trend": "flat"}
        recs = _build_recommendations(pol, fb, pat, iv)
        ids = [r["recommendation_id"] for r in recs]
        self.assertIn("rec.meta.run_policy_experiment_before_yaml_change", ids)
        self.assertIn("rec.meta.low_confidence_review_conflicting_signals", ids)

    def test_high_severity_pattern_suggests_queue_debt_scale(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.25, interpretation="healthy_mixed")
        pat = {
            "detected_patterns": [
                {"severity": "high", "pattern_id": "p.x", "title": "Systemic import backlog"},
            ]
        }
        iv = {"trend": "flat"}
        recs = _build_recommendations(pol, fb, pat, iv)
        ids = [r["recommendation_id"] for r in recs]
        self.assertIn("rec.queue_scoring.debt_scale.increase_under_systemic_pressure", ids)
        row = next(r for r in recs if "debt_scale" in r["recommendation_id"])
        self.assertFalse(row["safe_to_try"])

    def test_end_to_end_empty_repo(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pl = evaluate_operator_policy_recommendations(root, limit_history=5)
            self.assertEqual(pl["schema"], OPERATOR_POLICY_RECOMMENDATIONS_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            self.assertIsInstance(pl.get("recommendations"), list)

    def test_run_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_operator_policy_recommendations(root, limit_history=5, write_artifacts=True)
            self.assertTrue((root / "runs" / "policy" / "recommendations" / "latest.json").is_file())
            raw = json.loads(
                (root / "runs" / "policy" / "recommendations" / "latest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(raw["schema"], OPERATOR_POLICY_RECOMMENDATIONS_SCHEMA)

    def test_markdown_sections(self) -> None:
        md = render_operator_policy_recommendations_markdown(
            {
                "schema": OPERATOR_POLICY_RECOMMENDATIONS_SCHEMA,
                "run_id": "r",
                "evaluated_at_utc": "t",
                "recommendations": [
                    {
                        "recommendation_id": "a",
                        "affected_policy_area": "quiescence.x",
                        "current_value": 1,
                        "suggested_value": 2,
                        "rationale": "r",
                        "expected_effect": "e",
                        "confidence": 0.8,
                        "safe_to_try": True,
                    },
                    {
                        "recommendation_id": "b",
                        "affected_policy_area": "confidence.x",
                        "current_value": 0.4,
                        "suggested_value": 0.5,
                        "rationale": "r2",
                        "expected_effect": "e2",
                        "confidence": 0.4,
                        "safe_to_try": False,
                    },
                ],
                "observed_inefficiencies": ["Something inefficient"],
                "conflicting_signals": [],
                "notes": [],
            }
        )
        self.assertIn("## Safe adjustments", md)
        self.assertIn("## High-impact but risky adjustments", md)
        self.assertIn("## Observed inefficiencies", md)
        self.assertIn("Something inefficient", md)


def _eff() -> dict:
    return {
        "current": {
            "by_objective": {},
            "by_driver": {},
            "by_risk_posture": {},
            "guardrail_risk_frequency_by_id": {},
            "driver_support_frequency_by_id": {},
        },
        "caveats": [],
        "notable_patterns": [],
    }


class TestMissionAwareRecommendations(unittest.TestCase):
    def test_revenue_objective_negative_mix(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["current"]["by_objective"]["revenue"] = {
                "products_n": 3,
                "negative_rate": 0.5,
                "stagnation_rate": 0.1,
            }
            recs = _build_recommendations(
                pol, fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff, repo_root=root
            )
            row = next(
                r
                for r in recs
                if r["recommendation_id"] == "rec.mission.objective.revenue.review_confidence_when_negative_mix"
            )
            self.assertEqual(row["mission_scope"], "objective")
            self.assertEqual(row["mission_scope_detail"], "revenue")
            self.assertIn("revenue", row["applicable_mission_profiles"])

    def test_education_objective_stagnation(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["current"]["by_objective"]["education"] = {
                "products_n": 3,
                "stagnation_rate": 0.5,
                "negative_rate": 0.1,
            }
            recs = _build_recommendations(
                pol, fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff, repo_root=root
            )
            self.assertTrue(
                any(
                    r["recommendation_id"] == "rec.mission.objective.education.tighten_confidence_delta_when_stagnant"
                    for r in recs
                )
            )

    def test_guardrail_pressure_recommendation(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["current"]["guardrail_risk_frequency_by_id"]["education"] = {
                "products_with_guardrail_n": 2,
                "guardrail_risk_signal_rate": 0.6,
            }
            recs = _build_recommendations(
                pol, fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff, repo_root=root
            )
            self.assertTrue(
                any(
                    "rec.mission.guardrail." in r["recommendation_id"] and "widen_intervention" in r["recommendation_id"]
                    for r in recs
                )
            )
            row = next(
                r
                for r in recs
                if "rec.mission.guardrail." in r["recommendation_id"] and "widen_intervention" in r["recommendation_id"]
            )
            self.assertEqual(row["mission_scope"], "guardrail")

    def test_driver_support_gap(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["current"]["by_driver"]["education"] = {"products_n": 3, "stagnation_rate": 0.2}
            eff["current"]["driver_support_frequency_by_id"]["education"] = {
                "driver_support_signal_rate": 0.2,
            }
            recs = _build_recommendations(
                pol, fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff, repo_root=root
            )
            self.assertTrue(
                any(
                    r["recommendation_id"]
                    == "rec.mission.driver.education.lower_debt_delta_when_support_signals_sparse"
                    for r in recs
                )
            )

    def test_risk_posture_mismatch(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["current"]["by_risk_posture"] = {
                "conservative": {"products_n": 3, "stagnation_rate": 0.6},
                "moderate": {"products_n": 3, "stagnation_rate": 0.2},
            }
            recs = _build_recommendations(
                pol, fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff, repo_root=root
            )
            self.assertTrue(
                any(
                    r["recommendation_id"] == "rec.mission.risk_posture.conservative_vs_moderate_stagnation_review"
                    for r in recs
                )
            )

    def test_mixed_objective_gap_meta(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed")
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["notable_patterns"] = [
                "descriptive: improvement_rate higher for objective=revenue (0.80) than objective=education (0.10); associative not causal"
            ]
            recs = _build_recommendations(
                pol, fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff, repo_root=root
            )
            self.assertTrue(
                any(r["recommendation_id"] == "rec.mission.mixed.objective_gap_review_via_experiment" for r in recs)
            )

    def test_sparse_caveats_in_observed_and_payload(self) -> None:
        fb = _fb(products_n=10, flat=0.2, interpretation="healthy_mixed", sparse=True)
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir(parents=True, exist_ok=True)
            eff = _eff()
            eff["caveats"] = ["sparse_stamped_outcomes: only one snapshot"]
            ineff = _observed_inefficiencies(fb, {"detected_patterns": []}, {"trend": "flat"}, effectiveness=eff)
            self.assertTrue(any("sparse_stamped_outcomes" in x for x in ineff))

    def test_portfolio_wide_has_mission_fields(self) -> None:
        pol = default_operator_policy()
        fb = _fb(products_n=10, flat=0.5, interpretation="stressed")
        recs = _build_recommendations(pol, fb, {"detected_patterns": []}, {"trend": "flat"})
        stag = next(r for r in recs if "decrease_for_stagnation" in r["recommendation_id"])
        self.assertEqual(stag.get("mission_scope"), "portfolio_wide")
        self.assertIn("recommendation_strength", stag)
        self.assertIn("sparse_signal_warning", stag)
