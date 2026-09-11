"""Tests for :mod:`argus.portfolio.cycle`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from argus.mission.provenance import PORTFOLIO_MISSION_PROVENANCE_SCHEMA
from argus.portfolio.cycle import (
    OVERALL_HUMAN_REVIEW,
    OVERALL_INSPECT_PRODUCTS,
    OVERALL_REPAIR_IMPORTS,
    OVERALL_RUN_AGAIN,
    OVERALL_WAIT,
    PORTFOLIO_CYCLE_SCHEMA,
    portfolio_cycle_dir,
    run_portfolio_cycle,
    synthesize_overall_operator_recommendation,
)
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.intervention import (
    INTERVENTION_EVIDENCE_REFRESH,
    INTERVENTION_HUMAN_REVIEW,
    PORTFOLIO_INTERVENTION_SCHEMA,
)
from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA


def _minimal_product(root: Path, pid: str) -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "scripts").mkdir(parents=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\necho\n", encoding="utf-8")
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: T
owner:
  team: test
lifecycle:
  stage: idea
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


class TestPortfolioCycle(unittest.TestCase):
    def test_happy_path_bounded_cycle_writes_bundle(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(root, limit=0, write_cycle_artifacts=True, write_stage_artifacts=True)
            self.assertEqual(pl.get("schema"), PORTFOLIO_CYCLE_SCHEMA)
            self.assertEqual(
                (pl.get("portfolio_mission_provenance") or {}).get("schema"),
                PORTFOLIO_MISSION_PROVENANCE_SCHEMA,
            )
            self.assertTrue(pl.get("ok"))
            cdir = portfolio_cycle_dir(root)
            self.assertTrue((cdir / "latest.json").is_file())
            self.assertTrue((cdir / "latest.md").is_file())
            raw = json.loads((cdir / "latest.json").read_text(encoding="utf-8"))
            self.assertIn("stages", raw)
            self.assertEqual(raw["stages"]["operator_queue"]["status"], "ok")

    def test_post_progression_queue_realign_applied_when_execute(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(root, limit=0, dry_run=False, write_cycle_artifacts=True, write_stage_artifacts=True)
            oq = pl["stages"]["operator_queue"]
            self.assertEqual(oq.get("post_progression_realign", {}).get("applied"), True)

    def test_post_progression_queue_realign_skipped_on_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(root, limit=0, dry_run=True, write_cycle_artifacts=False, write_stage_artifacts=True)
            oq = pl["stages"]["operator_queue"]
            self.assertIsNone(oq.get("post_progression_realign"))

    def test_satellite_refresh_writes_outcomes_strategy_inbox_escalation(self) -> None:
        """After a successful cycle, satellite artifacts align lifecycle ``outcomes_artifact_present`` with on-disk truth."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(root, limit=0, write_cycle_artifacts=True, write_stage_artifacts=True)
            sr = pl.get("satellite_refresh") or {}
            self.assertEqual(sr.get("portfolio_outcomes", {}).get("status"), "ok")
            self.assertEqual(sr.get("portfolio_patterns", {}).get("status"), "ok")
            self.assertEqual(sr.get("intervention_inbox", {}).get("status"), "ok")
            self.assertEqual(sr.get("portfolio_strategy", {}).get("status"), "ok")
            self.assertEqual(sr.get("escalation_inbox", {}).get("status"), "ok")
            self.assertTrue((root / "runs" / "portfolio" / "outcomes" / "latest.json").is_file())
            pat_latest = root / "runs" / "portfolio" / "patterns" / "latest.json"
            self.assertTrue(pat_latest.is_file())
            raw_p = json.loads(pat_latest.read_text(encoding="utf-8"))
            self.assertEqual(raw_p.get("schema"), PORTFOLIO_PATTERNS_SCHEMA)
            self.assertTrue((root / "runs" / "portfolio" / "strategy" / "latest.json").is_file())
            raw_s = json.loads((root / "runs" / "portfolio" / "strategy" / "latest.json").read_text(encoding="utf-8"))
            self.assertTrue((raw_s.get("inputs") or {}).get("canonical_artifacts", {}).get("portfolio_patterns_latest"))
            self.assertTrue((root / "runs" / "portfolio" / "intervention_inbox" / "latest.json").is_file())
            self.assertTrue((root / "runs" / "portfolio" / "escalation_inbox" / "latest.json").is_file())
            lc = evaluate_portfolio_lifecycle(root)
            self.assertTrue((lc.get("inputs") or {}).get("outcomes_artifact_present"))

    def test_dry_run_sets_progression_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(root, limit=0, dry_run=True, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertTrue(pl["summary"]["progression"].get("dry_run"))
            self.assertTrue(pl["inputs"]["dry_run"])

    def test_missing_prior_artifacts_cycle_completes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            pl = run_portfolio_cycle(root, limit=0, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertTrue(pl.get("ok"))
            self.assertIn("portfolio_quiescence", pl["stages"])
            self.assertEqual(pl["stages"]["portfolio_quiescence"]["status"], "ok")

    def test_one_failed_stage_usable_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")

            def boom(*_a: object, **_k: object) -> dict[str, object]:
                raise RuntimeError("progression failed")

            with patch("argus.portfolio.cycle.run_portfolio_progression", side_effect=boom):
                pl = run_portfolio_cycle(root, limit=1, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertFalse(pl.get("ok"))
            self.assertEqual(pl["stages"]["portfolio_progression"]["status"], "error")
            self.assertIn("summary", pl)
            self.assertIn("overall_operator_recommendation", pl["summary"])

    def test_cycle_recommends_wait(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            quies = {
                "schema": PORTFOLIO_QUIESCENCE_SCHEMA,
                "recommendation": "wait",
                "portfolio_quiescent": True,
                "products_with_material_change": [],
            }
            delta = {
                "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
                "run_id": "dr",
                "recommended_next_portfolio_action": "continue_monitoring",
                "products_with_material_change": [],
            }
            inv = {
                "schema": PORTFOLIO_INTERVENTION_SCHEMA,
                "run_id": "x",
                "flagged_products": [],
                "stable_benign_products": ["a"],
            }
            with patch("argus.portfolio.cycle.run_portfolio_quiescence", return_value=quies):
                with patch("argus.portfolio.cycle.run_portfolio_delta_report", return_value=delta):
                    with patch("argus.portfolio.cycle.run_portfolio_intervention", return_value=inv):
                        pl = run_portfolio_cycle(root, limit=0, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertEqual(pl["summary"]["overall_operator_recommendation"], OVERALL_WAIT)

    def test_cycle_recommends_human_review(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            inv = {
                "schema": PORTFOLIO_INTERVENTION_SCHEMA,
                "run_id": "x",
                "flagged_products": [
                    {
                        "product_id": "a",
                        "intervention_category": INTERVENTION_HUMAN_REVIEW,
                        "severity": "medium",
                        "detection_reason_codes": ["x"],
                        "evidence_summary": "e",
                        "recommended_operator_action": "act",
                        "chronicity": "emerging",
                    }
                ],
                "stable_benign_products": [],
            }
            with patch("argus.portfolio.cycle.run_portfolio_intervention", return_value=inv):
                pl = run_portfolio_cycle(root, limit=0, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertEqual(pl["summary"]["overall_operator_recommendation"], OVERALL_HUMAN_REVIEW)

    def test_cycle_recommends_inspect_from_intervention(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            inv = {
                "schema": PORTFOLIO_INTERVENTION_SCHEMA,
                "run_id": "x",
                "flagged_products": [
                    {
                        "product_id": "a",
                        "intervention_category": INTERVENTION_EVIDENCE_REFRESH,
                        "severity": "medium",
                        "detection_reason_codes": ["y"],
                        "evidence_summary": "e",
                        "recommended_operator_action": "act",
                        "chronicity": "emerging",
                    }
                ],
                "stable_benign_products": [],
            }
            with patch("argus.portfolio.cycle.run_portfolio_intervention", return_value=inv):
                pl = run_portfolio_cycle(root, limit=0, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertEqual(pl["summary"]["overall_operator_recommendation"], OVERALL_INSPECT_PRODUCTS)

    def test_cycle_recommends_repair_imports(self) -> None:
        q = {
            "schema": PORTFOLIO_QUIESCENCE_SCHEMA,
            "recommendation": "import_refresh",
            "portfolio_quiescent": False,
        }
        d = {
            "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
            "recommended_next_portfolio_action": "continue_monitoring",
        }
        rec, codes = synthesize_overall_operator_recommendation(quiescence=q, delta=d, intervention=None)
        self.assertEqual(rec, OVERALL_REPAIR_IMPORTS)
        self.assertTrue(any("import" in c for c in codes))

    def test_synthesize_run_again(self) -> None:
        q = {
            "schema": PORTFOLIO_QUIESCENCE_SCHEMA,
            "recommendation": "run_again",
            "portfolio_quiescent": False,
        }
        rec, _ = synthesize_overall_operator_recommendation(quiescence=q, delta=None, intervention=None)
        self.assertEqual(rec, OVERALL_RUN_AGAIN)

    def test_no_save_skips_cycle_dir_only(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "a")
            run_portfolio_cycle(root, limit=0, write_cycle_artifacts=False, write_stage_artifacts=True)
            self.assertFalse(portfolio_cycle_dir(root).exists())
            oq = root / "runs" / "portfolio" / "operator_queue" / "latest.json"
            self.assertTrue(oq.is_file())


if __name__ == "__main__":
    unittest.main()
