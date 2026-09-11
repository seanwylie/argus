"""Product deprecation proposals (``argus.product_deprecation_proposals.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.strategy import PORTFOLIO_STRATEGY_SCHEMA
from argus.products.deprecation import (
    PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
    deprecation_proposals_dir,
    evaluate_deprecation_proposals,
    run_deprecation_proposals,
)


def _product(root: Path, pid: str, *, stage: str = "validate") -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: DeprecationTest
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
    for n in ("start.sh", "stop.sh", "analyze.sh"):
        (pr / "scripts" / n).write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "metrics").mkdir(parents=True)
    (pr / "metrics" / "d.txt").write_text("1", encoding="utf-8")


def _write_outcomes(root: Path, per_product: list[dict]) -> None:
    d = root / "runs" / "portfolio" / "outcomes"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PORTFOLIO_OUTCOMES_SCHEMA,
        "run_id": "t1",
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "per_product_outcomes": per_product,
        "portfolio_outcome_summary": {"products_evaluated": len(per_product)},
        "portfolio_mission_provenance": {"mission_mix_summary": {}},
        "mission_alignment_summary": {},
        "inputs": {},
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_strategy(root: Path, posture: str) -> None:
    d = root / "runs" / "portfolio" / "strategy"
    d.mkdir(parents=True, exist_ok=True)
    pl = {
        "schema": PORTFOLIO_STRATEGY_SCHEMA,
        "run_id": "s1",
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "strategic_posture": posture,
        "posture_scores": {},
        "rationale": [],
        "dominant_mission_mix": {},
        "portfolio_pressures": [],
        "top_opportunities": [],
        "top_risks": [],
        "recommended_next_portfolio_moves": [],
        "inputs": {},
    }
    (d / "latest.json").write_text(json.dumps(pl), encoding="utf-8")


class TestProductDeprecationProposals(unittest.TestCase):
    def test_schema_and_empty_portfolio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            payload = evaluate_deprecation_proposals(root)
            self.assertEqual(payload["schema"], PRODUCT_DEPRECATION_PROPOSALS_SCHEMA)
            self.assertEqual(payload["proposal_count"], 0)

    def test_decline_lifecycle_suggests_retire_without_outcomes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "sunset-a", stage="decline")
            payload = evaluate_deprecation_proposals(root)
            self.assertGreaterEqual(payload["proposal_count"], 1)
            p0 = payload["proposals"][0]
            self.assertEqual(p0["product_id"], "sunset-a")
            self.assertEqual(p0["deprecation_posture"], "retire")
            self.assertIn("proposal_id", p0)
            self.assertIn("supporting_evidence", p0)
            self.assertIn("recommended_next_step", p0)

    def test_negative_outcome_repair_instead_when_intervention_stress(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "stressed", stage="grow")
            _write_outcomes(
                root,
                [
                    {
                        "product_id": "stressed",
                        "overall_trajectory": "negative",
                        "intervention_pattern": "repeated",
                        "blocked_pattern": "none",
                        "mission_interpretation": {"mission_alignment": "negative"},
                        "reason_codes": ["x"],
                    },
                ],
            )
            payload = evaluate_deprecation_proposals(root)
            ids = {p["product_id"] for p in payload["proposals"]}
            self.assertIn("stressed", ids)
            prop = next(p for p in payload["proposals"] if p["product_id"] == "stressed")
            self.assertEqual(prop["deprecation_posture"], "repair_instead")

    def test_harvest_posture_and_positive_trajectory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "winner", stage="grow")
            _write_strategy(root, "harvest")
            _write_outcomes(
                root,
                [
                    {
                        "product_id": "winner",
                        "overall_trajectory": "positive",
                        "intervention_pattern": "none",
                        "blocked_pattern": "none",
                        "mission_interpretation": {"mission_alignment": "positive"},
                        "reason_codes": [],
                    },
                ],
            )
            payload = evaluate_deprecation_proposals(root)
            prop = next(p for p in payload["proposals"] if p["product_id"] == "winner")
            self.assertEqual(prop["deprecation_posture"], "harvest")

    def test_run_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "z", stage="decline")
            run_deprecation_proposals(root, write_artifacts=True)
            d = deprecation_proposals_dir(root)
            self.assertTrue((d / "latest.json").is_file())
            raw = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], PRODUCT_DEPRECATION_PROPOSALS_SCHEMA)

    def test_no_save_skips_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "z", stage="decline")
            run_deprecation_proposals(root, write_artifacts=False)
            self.assertFalse(deprecation_proposals_dir(root).exists())


if __name__ == "__main__":
    unittest.main()
