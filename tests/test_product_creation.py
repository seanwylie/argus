"""Product creation proposals (``argus.product_creation_proposals.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.creation import (
    INITIAL_SUGGESTED_MISSION_SCHEMA,
    PRODUCT_CREATION_PROPOSALS_SCHEMA,
    evaluate_creation_proposals,
    render_creation_proposals_markdown,
    run_creation_proposals,
)


def _product(root: Path, pid: str, *, stage: str = "validate", ptype: str = "saas") -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: {pid.title()}
type: {ptype}
status: beta
state: {stage}
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


def _write_mission(root: Path, *, mission_id: str = "revenue") -> None:
    cfg = root / "config" / "mission_profiles.yaml"
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(
        f"""schema: argus.mission_registry.v1
default_mission_id: {mission_id}
profiles:
  revenue:
    id: revenue
    primary_objective: Maximize sustainable revenue.
    drivers:
      - conversion and retention
      - margin and cost discipline
    risk_posture: moderate
    weights:
      revenue_alignment: 1.0
  education:
    id: education
    primary_objective: Maximize learning outcomes.
    drivers:
      - pedagogical quality
      - accessibility
    risk_posture: conservative
    weights:
      learning_quality: 1.0
""",
        encoding="utf-8",
    )


def _write_outcomes(root: Path, per_product: list[dict]) -> None:
    d = root / "runs" / "portfolio" / "outcomes"
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(
        json.dumps({
            "schema": "argus.portfolio_outcomes.v1",
            "per_product": per_product,
        }),
        encoding="utf-8",
    )


class TestEmptyPortfolio(unittest.TestCase):
    def test_empty_portfolio_proposes_greenfield(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            payload = evaluate_creation_proposals(root)
            self.assertEqual(payload["schema"], PRODUCT_CREATION_PROPOSALS_SCHEMA)
            self.assertEqual(payload["portfolio_snapshot"]["valid_product_count"], 0)
            self.assertTrue(len(payload["detected_gaps"]) > 0)
            gap_ids = [g["gap_id"] for g in payload["detected_gaps"]]
            self.assertIn("gap.empty_portfolio", gap_ids)
            self.assertTrue(len(payload["proposals"]) > 0)
            for p in payload["proposals"]:
                self.assertIn("proposal_id", p)
                self.assertIn("concept_title", p)
                self.assertIn("initial_suggested_mission", p)
                self.assertIn("creation_mission_used", p)


class TestStagnantPortfolio(unittest.TestCase):
    def test_stagnant_outcomes_propose_growth(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _product(root, "alpha", stage="grow")
            _product(root, "beta", stage="grow")
            _write_outcomes(root, [
                {"product_id": "alpha", "overall_trajectory": "regressing"},
                {"product_id": "beta", "overall_trajectory": "regressing"},
            ])
            payload = evaluate_creation_proposals(root)
            gap_ids = [g["gap_id"] for g in payload["detected_gaps"]]
            self.assertIn("gap.portfolio_stagnation", gap_ids)
            self.assertTrue(any(
                p["evidence_summary"]["opportunity_type"] == "growth_injection"
                for p in payload["proposals"]
            ))


class TestMissionDrivenProposalDifferences(unittest.TestCase):
    def test_different_mission_changes_proposals(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root, mission_id="revenue")
            p1 = evaluate_creation_proposals(root)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root, mission_id="education")
            p2 = evaluate_creation_proposals(root)

        self.assertEqual(p1["creation_mission_id"], "revenue")
        self.assertEqual(p2["creation_mission_id"], "education")
        self.assertNotEqual(
            p1["creation_mission_objective"],
            p2["creation_mission_objective"],
        )
        if p1["proposals"] and p2["proposals"]:
            self.assertNotEqual(
                p1["proposals"][0]["creation_mission_used"],
                p2["proposals"][0]["creation_mission_used"],
            )


class TestProposalIncludesMissionSuggestion(unittest.TestCase):
    def test_proposals_carry_initial_mission(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root, mission_id="revenue")
            payload = evaluate_creation_proposals(root)
            mmc = payload.get("mission_machine_contract") or {}
            self.assertEqual(
                mmc.get("initial_suggested_mission_schema"),
                INITIAL_SUGGESTED_MISSION_SCHEMA,
            )
            self.assertTrue(mmc.get("fields_are_registry_profile_ids"))
            for p in payload["proposals"]:
                sm = p["initial_suggested_mission"]
                self.assertEqual(sm.get("schema"), INITIAL_SUGGESTED_MISSION_SCHEMA)
                self.assertTrue(sm.get("mission_profile_fields_are_registry_ids"))
                self.assertIn("objective", sm)
                self.assertIn("risk_posture", sm)
                self.assertIn("drivers", sm)
                self.assertIn("guardrails", sm)
                self.assertIsInstance(sm["drivers"], list)
                self.assertIsInstance(sm["guardrails"], list)
                self.assertEqual(sm["objective"], "revenue")
                for d in sm["drivers"]:
                    self.assertIsInstance(d, str)
                    self.assertNotIn(" ", d)
                    self.assertIn(d, ("revenue", "education"))
                mh = p.get("mission_human_context") or {}
                self.assertIn("creation_mission_driver_phrases", mh)
                self.assertEqual(mh.get("structured_mission_ruleset"), "creation_gap_mapping_v1")


class TestSingleProductDiversification(unittest.TestCase):
    def test_single_product_detects_diversification_gap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _product(root, "only-one")
            payload = evaluate_creation_proposals(root)
            gap_ids = [g["gap_id"] for g in payload["detected_gaps"]]
            self.assertIn("gap.single_product_portfolio", gap_ids)


class TestWellCoveredPortfolio(unittest.TestCase):
    def test_diverse_portfolio_fewer_gaps(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _product(root, "alpha", stage="validate", ptype="saas")
            _product(root, "beta", stage="grow", ptype="marketplace")
            _product(root, "gamma", stage="build", ptype="content_stream")
            _write_outcomes(root, [
                {"product_id": "alpha", "overall_trajectory": "improving"},
                {"product_id": "beta", "overall_trajectory": "stable"},
                {"product_id": "gamma", "overall_trajectory": "improving"},
            ])
            payload = evaluate_creation_proposals(root)
            gap_ids = [g["gap_id"] for g in payload["detected_gaps"]]
            self.assertNotIn("gap.empty_portfolio", gap_ids)
            self.assertNotIn("gap.single_product_portfolio", gap_ids)
            self.assertNotIn("gap.portfolio_stagnation", gap_ids)
            self.assertNotIn("gap.no_early_stage_products", gap_ids)


class TestArtifactWriting(unittest.TestCase):
    def test_run_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            run_creation_proposals(root, write_artifacts=True)
            d = root / "runs" / "products" / "creation"
            self.assertTrue((d / "latest.json").exists())
            self.assertTrue((d / "latest.md").exists())
            raw = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], PRODUCT_CREATION_PROPOSALS_SCHEMA)

    def test_no_save_skips_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            run_creation_proposals(root, write_artifacts=False)
            d = root / "runs" / "products" / "creation"
            self.assertFalse(d.exists())

    def test_markdown_renders(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_mission(root)
            payload = evaluate_creation_proposals(root)
            md = render_creation_proposals_markdown(payload)
            self.assertIn("# Product creation proposals", md)
            self.assertIn("argus.product_creation_proposals.v1", md)
            self.assertIn("Suggested mission (machine)", md)
            self.assertIn("guardrails=", md)


if __name__ == "__main__":
    unittest.main()
