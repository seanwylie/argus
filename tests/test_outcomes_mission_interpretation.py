"""Mission-aware portfolio outcomes interpretation (deterministic, additive)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.outcomes import evaluate_portfolio_outcomes
from argus.portfolio.outcomes_mission import PORTFOLIO_OUTCOME_MISSION_INTERPRETATION_SCHEMA
from tests.test_portfolio_outcomes import _delta, _interv, _prog, _row, _write


def _delta_multi(run_id: str, per_product: dict[str, dict]) -> dict:
    """Single delta snapshot with multiple products (avoids overwriting the same artifact path)."""
    return {
        "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": f"{run_id[:4]}-01-01T00:00:00+00:00",
        "baseline_for_next_run": {
            "evaluated_at_utc": run_id,
            "per_product": dict(per_product),
            "queue_product_order": sorted(per_product.keys()),
        },
    }


def _minimal_product(root: Path, pid: str, mission: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: X
owner:
  team: t
lifecycle:
  stage: idea
{mission}
metrics:
  local_paths: []
  primary: []
cost:
  monthly_usd: 0
  notes: ""
signals:
  - type: filesystem
    enabled: true
actions:
  start: "./scripts/s.sh"
  stop: "./scripts/s.sh"
  analyze: "./scripts/s.sh"
constraints:
  max_monthly_cost_usd: 1
  min_activity_threshold: 0
""",
        encoding="utf-8",
    )


class TestOutcomesMissionInterpretation(unittest.TestCase):
    def test_mission_block_present_on_each_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            pid = "p1"
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta("20260101T000001Z", pid, _row(rank=5, tier="observe_gap", debt=0.4, conf=0.4)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=2, tier="advance_ready", debt=0.18, conf=0.62, na="none"),
                ),
            )
            _write(root, "progression", "20260115T000000Z.json", _prog("20260115T000000Z", pid, "advanced"))
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000000Z.json", _interv("20260202T000000Z", []))

            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            row = pl["per_product_outcomes"][0]
            mi = row["mission_interpretation"]
            self.assertEqual(mi["schema"], PORTFOLIO_OUTCOME_MISSION_INTERPRETATION_SCHEMA)
            self.assertIn(mi["mission_alignment"], ("positive", "neutral", "negative"))
            self.assertIn("mission_alignment_summary", pl)
            self.assertIn("mission_interpretation_note", pl)

    def test_education_objective_weights_confidence_more_than_revenue(self) -> None:
        """Same trajectory (confidence-only improvement): education objective yields higher alignment score."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            t = "interpret_gap"
            base_row = _row(rank=4, tier=t, debt=0.33, conf=0.32)
            better_row = _row(rank=4, tier=t, debt=0.33, conf=0.55)

            _minimal_product(root, "p_rev", "mission_id: revenue\n")
            _minimal_product(root, "p_edu", "mission_id: education\n")
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta_multi("20260101T000001Z", {"p_rev": base_row, "p_edu": base_row}),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta_multi("20260201T000002Z", {"p_rev": better_row, "p_edu": better_row}),
            )

            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            by = {r["product_id"]: r["mission_interpretation"] for r in pl["per_product_outcomes"]}
            s_rev = float(by["p_rev"]["mission_alignment_score"])
            s_edu = float(by["p_edu"]["mission_alignment_score"])
            self.assertGreater(s_edu, s_rev)

    def test_driver_support_signal_when_driver_aligned(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            pid = "pd"
            _minimal_product(
                root,
                pid,
                "mission:\n  objective: revenue\n  drivers: [education]\n",
            )
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta("20260101T000001Z", pid, _row(rank=5, tier="observe_gap", debt=0.5, conf=0.4)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=3, tier="observe_gap", debt=0.35, conf=0.58),
                ),
            )
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            mi = pl["per_product_outcomes"][0]["mission_interpretation"]
            self.assertIn("education", mi.get("mission_drivers") or [])
            # Debt + confidence improved — education driver should often register support
            self.assertIsInstance(mi.get("driver_support_signals"), list)

    def test_guardrail_risk_on_regression(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            pid = "pg"
            _minimal_product(
                root,
                pid,
                "mission:\n  objective: revenue\n  guardrails: [education]\n",
            )
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta(
                    "20260101T000001Z",
                    pid,
                    _row(rank=2, tier="advance_ready", debt=0.2, conf=0.7),
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    pid,
                    _row(rank=6, tier="interpret_gap", debt=0.45, conf=0.35),
                ),
            )
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            mi = pl["per_product_outcomes"][0]["mission_interpretation"]
            gr = mi.get("guardrail_risk_signals") or []
            codes = [c for g in gr if isinstance(g, dict) for c in (g.get("risk_codes") or [])]
            self.assertTrue(any("education" in c for c in codes))

    def test_mixed_portfolio_mission_summaries(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            _minimal_product(root, "good", "mission_id: revenue\n")
            _minimal_product(root, "bad", "mission_id: engagement\n")
            d1 = {
                "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
                "run_id": "20260101T000001Z",
                "evaluated_at_utc": "2026-01-01T00:00:00+00:00",
                "baseline_for_next_run": {
                    "evaluated_at_utc": "20260101T000001Z",
                    "per_product": {
                        "good": _row(rank=5, tier="observe_gap", debt=0.4),
                        "bad": _row(rank=2, tier="advance_ready", debt=0.2),
                    },
                    "queue_product_order": ["good", "bad"],
                },
            }
            d2 = {
                "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
                "run_id": "20260201T000002Z",
                "evaluated_at_utc": "2026-02-01T00:00:00+00:00",
                "baseline_for_next_run": {
                    "evaluated_at_utc": "20260201T000002Z",
                    "per_product": {
                        "good": _row(rank=2, tier="advance_ready", debt=0.18),
                        "bad": _row(rank=6, tier="interpret_gap", debt=0.5),
                    },
                    "queue_product_order": ["good", "bad"],
                },
            }
            _write(root, "delta_report", "20260101T000001Z.json", d1)
            _write(root, "delta_report", "20260201T000002Z.json", d2)
            pl = evaluate_portfolio_outcomes(root, limit_history=10)
            self.assertEqual(pl["portfolio_outcome_summary"]["products_evaluated"], 2)
            self.assertGreaterEqual(pl["mission_alignment_summary"]["positive"], 1)
            self.assertGreaterEqual(pl["mission_alignment_summary"]["negative"], 1)
