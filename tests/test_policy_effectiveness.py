"""Tests for mission-segmented operator policy effectiveness (argus.operator_policy_effectiveness.v1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.policy.effectiveness import (
    OPERATOR_POLICY_EFFECTIVENESS_SCHEMA,
    evaluate_operator_policy_effectiveness,
    run_operator_policy_effectiveness,
)
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.outcomes import evaluate_portfolio_outcomes, portfolio_outcomes_dir
from tests.test_outcomes_mission_interpretation import _delta_multi
from tests.test_portfolio_outcomes import _delta, _interv, _prog, _row, _write


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


def _stamp_outcomes(root: Path, name: str, payload: dict) -> None:
    d = portfolio_outcomes_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


class TestPolicyEffectiveness(unittest.TestCase):
    def test_schema_and_segments(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            _minimal_product(root, "pa", "mission_id: revenue\n")
            _minimal_product(root, "pb", "mission_id: education\n")
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta_multi(
                    "20260101T000001Z",
                    {
                        "pa": _row(rank=5, tier="observe_gap", debt=0.4, conf=0.4),
                        "pb": _row(rank=5, tier="observe_gap", debt=0.4, conf=0.4),
                    },
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta_multi(
                    "20260201T000002Z",
                    {
                        "pa": _row(rank=2, tier="advance_ready", debt=0.18, conf=0.62),
                        "pb": _row(rank=2, tier="advance_ready", debt=0.18, conf=0.62),
                    },
                ),
            )
            _write(root, "progression", "20260115T000000Z.json", _prog("20260115T000000Z", "pa", "advanced"))
            _write(root, "progression", "20260116T000000Z.json", _prog("20260116T000000Z", "pb", "advanced"))
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000000Z.json", _interv("20260202T000000Z", []))

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            self.assertEqual(pl["schema"], OPERATOR_POLICY_EFFECTIVENESS_SCHEMA)
            cur = pl["current"]
            self.assertIn("revenue", cur["by_objective"])
            self.assertIn("education", cur["by_objective"])
            self.assertEqual(cur["by_objective"]["revenue"]["products_n"], 1)
            self.assertEqual(cur["by_objective"]["education"]["products_n"], 1)

    def test_objective_level_difference_in_improvement_rates(self) -> None:
        """Revenue product regresses; education product improves — segment rates diverge."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            _minimal_product(root, "p_rev", "mission_id: revenue\n")
            _minimal_product(root, "p_edu", "mission_id: education\n")
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta_multi(
                    "20260101T000001Z",
                    {
                        "p_rev": _row(rank=2, tier="advance_ready", debt=0.2, conf=0.7),
                        "p_edu": _row(rank=5, tier="observe_gap", debt=0.4, conf=0.4),
                    },
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta_multi(
                    "20260201T000002Z",
                    {
                        "p_rev": _row(rank=6, tier="interpret_gap", debt=0.5, conf=0.35),
                        "p_edu": _row(rank=2, tier="advance_ready", debt=0.18, conf=0.65),
                    },
                ),
            )
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            bo = pl["current"]["by_objective"]
            self.assertLess(bo["revenue"]["improvement_rate"], bo["education"]["improvement_rate"])
            self.assertGreater(bo["revenue"]["negative_rate"], bo["education"]["negative_rate"])

    def test_driver_segment_and_support_frequency(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            _minimal_product(
                root,
                "pd",
                "mission:\n  objective: revenue\n  drivers: [education]\n",
            )
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta("20260101T000001Z", "pd", _row(rank=5, tier="observe_gap", debt=0.5, conf=0.4)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    "pd",
                    _row(rank=3, tier="observe_gap", debt=0.35, conf=0.58),
                ),
            )
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            bd = pl["current"]["by_driver"]
            self.assertIn("education", bd)
            self.assertEqual(bd["education"]["products_n"], 1)
            dsf = pl["current"]["driver_support_frequency_by_id"]
            self.assertIn("education", dsf)

    def test_guardrail_risk_frequency_table(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            _minimal_product(
                root,
                "pg",
                "mission:\n  objective: revenue\n  guardrails: [education]\n",
            )
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta(
                    "20260101T000001Z",
                    "pg",
                    _row(rank=2, tier="advance_ready", debt=0.2, conf=0.7),
                ),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta(
                    "20260201T000002Z",
                    "pg",
                    _row(rank=6, tier="interpret_gap", debt=0.45, conf=0.35),
                ),
            )
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            gr = pl["current"]["guardrail_risk_frequency_by_id"]
            self.assertIn("education", gr)
            self.assertGreaterEqual(gr["education"]["guardrail_risk_signal_rate"], 1.0)

    def test_sparse_caveats(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            pid = "only"
            _minimal_product(root, pid, "mission_id: engagement\n")
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta("20260101T000001Z", pid, _row(rank=4, tier="interpret_gap", debt=0.33, conf=0.4)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta("20260201T000002Z", pid, _row(rank=4, tier="interpret_gap", debt=0.33, conf=0.41)),
            )
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            caveats = " ".join(pl.get("caveats") or [])
            self.assertIn("sparse_stamped_outcomes", caveats)
            self.assertIn("sparse_products", caveats)

    def test_mixed_portfolio_notable_patterns(self) -> None:
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
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            bo = pl["current"]["by_objective"]
            self.assertNotEqual(
                bo.get("revenue", {}).get("improvement_rate"),
                bo.get("engagement", {}).get("improvement_rate"),
            )
            self.assertIsInstance(pl.get("notable_patterns"), list)

    def test_write_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            pid = "p1"
            _minimal_product(root, pid, "mission_id: revenue\n")
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
                _delta("20260201T000002Z", pid, _row(rank=2, tier="advance_ready", debt=0.2, conf=0.6)),
            )
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            run_operator_policy_effectiveness(root, limit_history=10, write_artifacts=True)
            d = root / "runs" / "policy" / "effectiveness"
            self.assertTrue((d / "latest.json").is_file())
            self.assertTrue((d / "latest.md").is_file())
            raw = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], OPERATOR_POLICY_EFFECTIVENESS_SCHEMA)

    def test_stamped_series_includes_objective_rates(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            _minimal_product(root, "px", "mission_id: revenue\n")
            _write(
                root,
                "delta_report",
                "20260101T000001Z.json",
                _delta("20260101T000001Z", "px", _row(rank=5, tier="observe_gap", debt=0.4, conf=0.4)),
            )
            _write(
                root,
                "delta_report",
                "20260201T000002Z.json",
                _delta("20260201T000002Z", "px", _row(rank=2, tier="advance_ready", debt=0.2, conf=0.6)),
            )
            _write(root, "intervention", "20260120T000000Z.json", _interv("20260120T000000Z", []))
            _write(root, "intervention", "20260202T000002Z.json", _interv("20260202T000002Z", []))

            live = evaluate_portfolio_outcomes(root, limit_history=10)
            live["run_id"] = "stamped1"
            _stamp_outcomes(root, "20260301T000001Z.json", live)

            pl = evaluate_operator_policy_effectiveness(root, limit_history=10)
            self.assertGreaterEqual(pl["inputs"]["stamped_outcomes_loaded"], 1)
            series = pl.get("stamped_outcomes_series") or []
            self.assertTrue(any("by_objective_improvement_rate" in s for s in series if isinstance(s, dict)))


if __name__ == "__main__":
    unittest.main()
