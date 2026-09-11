"""Tests for :mod:`argus.portfolio.lifecycle`."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.portfolio.lifecycle import (
    LIFECYCLE_STATUSES,
    PORTFOLIO_LIFECYCLE_SCHEMA,
    evaluate_portfolio_lifecycle,
    render_portfolio_lifecycle_markdown,
    run_portfolio_lifecycle,
)
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.strategy import PORTFOLIO_STRATEGY_SCHEMA
from argus.products.creation import PRODUCT_CREATION_PROPOSALS_SCHEMA
from argus.products.creation_bootstrap import PRODUCT_CREATION_BOOTSTRAP_SCHEMA
from argus.products.creation_scaffold import PRODUCT_CREATION_SCAFFOLD_SCHEMA
from argus.products.deprecation import PRODUCT_DEPRECATION_PROPOSALS_SCHEMA


def _product(root: Path, pid: str, *, stage: str = "validate") -> None:
    pr = root / "products" / pid
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {pid}
name: LcTest
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


def _write_deprecation(root: Path, proposals: list[dict]) -> None:
    d = root / "runs" / "products" / "deprecation"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "proposal_count": len(proposals),
        "proposals": proposals,
        "inputs": {},
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_creation(root: Path, proposals: list[dict]) -> None:
    d = root / "runs" / "products" / "creation"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PRODUCT_CREATION_PROPOSALS_SCHEMA,
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "proposal_count": len(proposals),
        "proposals": proposals,
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_scaffold(root: Path, *, proposal_id: str, product_id: str) -> None:
    d = root / "runs" / "products" / "creation_scaffold"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PRODUCT_CREATION_SCAFFOLD_SCHEMA,
        "run_id": "sc1",
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "ok": True,
        "dry_run": False,
        "proposal_id": proposal_id,
        "product_id": product_id,
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_bootstrap(root: Path, *, product_id: str) -> None:
    d = root / "runs" / "products" / "creation_bootstrap"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PRODUCT_CREATION_BOOTSTRAP_SCHEMA,
        "run_id": "bs1",
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "ok": True,
        "product_id": product_id,
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _write_outcomes_min(root: Path, *, per: list[dict], negative: list[str] | None = None) -> None:
    d = root / "runs" / "portfolio" / "outcomes"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PORTFOLIO_OUTCOMES_SCHEMA,
        "run_id": "o1",
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "per_product_outcomes": per,
        "products_with_negative_trajectory": negative or [],
        "portfolio_mission_provenance": {},
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
        "inputs": {},
    }
    (d / "latest.json").write_text(json.dumps(pl), encoding="utf-8")


class PortfolioLifecycleTests(unittest.TestCase):
    def test_empty_portfolio(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            payload = evaluate_portfolio_lifecycle(root)
            self.assertEqual(payload["schema"], PORTFOLIO_LIFECYCLE_SCHEMA)
            self.assertEqual(payload["per_product_lifecycle"], [])
            for s in LIFECYCLE_STATUSES:
                self.assertEqual(payload["lifecycle_counts"].get(s), 0)

    def test_active_healthy_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "steady", stage="grow")
            payload = evaluate_portfolio_lifecycle(root)
            row = next(r for r in payload["per_product_lifecycle"] if r["product_id"] == "steady")
            self.assertEqual(row["lifecycle_status"], "active")

    def test_incubating_after_scaffold_and_bootstrap(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_creation(
                root,
                [{"proposal_id": "creation_abcd12345678", "concept_title": "NewCo"}],
            )
            _product(root, "newco", stage="idea")
            _write_scaffold(root, proposal_id="creation_abcd12345678", product_id="newco")
            _write_bootstrap(root, product_id="newco")
            payload = evaluate_portfolio_lifecycle(root)
            row = next(r for r in payload["per_product_lifecycle"] if r.get("product_id") == "newco")
            self.assertEqual(row["lifecycle_status"], "incubating")
            self.assertIn("inventory.stage.early", row["reason_codes"])

    def test_proposed_only_creation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "products").mkdir()
            _write_creation(
                root,
                [{"proposal_id": "creation_deadbeef0001", "concept_title": "Ghost"}],
            )
            payload = evaluate_portfolio_lifecycle(root)
            prop_rows = [r for r in payload["per_product_lifecycle"] if r.get("lifecycle_status") == "proposed"]
            self.assertEqual(len(prop_rows), 1)
            self.assertEqual(prop_rows[0]["creation_proposal_id"], "creation_deadbeef0001")
            self.assertIn("proposal:creation_deadbeef0001", payload["products_entering"])

    def test_repair_instead(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "stressed", stage="grow")
            _write_deprecation(
                root,
                [
                    {
                        "proposal_id": "dep_x",
                        "product_id": "stressed",
                        "deprecation_posture": "repair_instead",
                        "rationale": "test",
                        "supporting_evidence": {},
                        "confidence": "high",
                        "recommended_next_step": "fix",
                    },
                ],
            )
            payload = evaluate_portfolio_lifecycle(root)
            row = next(r for r in payload["per_product_lifecycle"] if r["product_id"] == "stressed")
            self.assertEqual(row["lifecycle_status"], "repairing")
            self.assertIn("stressed", payload["products_under_repair_pressure"])

    def test_retire_harvest_archive_from_deprecation(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "r1", stage="grow")
            _product(root, "h1", stage="maintain")
            _product(root, "a1", stage="validate")
            _write_deprecation(
                root,
                [
                    {
                        "proposal_id": "d1",
                        "product_id": "r1",
                        "deprecation_posture": "retire",
                        "rationale": "",
                        "supporting_evidence": {},
                        "confidence": "high",
                        "recommended_next_step": "",
                    },
                    {
                        "proposal_id": "d2",
                        "product_id": "h1",
                        "deprecation_posture": "harvest",
                        "rationale": "",
                        "supporting_evidence": {},
                        "confidence": "high",
                        "recommended_next_step": "",
                    },
                    {
                        "proposal_id": "d3",
                        "product_id": "a1",
                        "deprecation_posture": "archive",
                        "rationale": "",
                        "supporting_evidence": {},
                        "confidence": "high",
                        "recommended_next_step": "",
                    },
                ],
            )
            payload = evaluate_portfolio_lifecycle(root)
            by_id = {r["product_id"]: r["lifecycle_status"] for r in payload["per_product_lifecycle"] if r.get("product_id")}
            self.assertEqual(by_id["r1"], "retiring")
            self.assertEqual(by_id["h1"], "harvesting")
            self.assertEqual(by_id["a1"], "archived_candidate")
            ex = set(payload["products_exiting"])
            self.assertTrue({"r1", "h1", "a1"}.issubset(ex))

    def test_mixed_or_unclear_kill_vs_repair(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "odd", stage="kill")
            _write_deprecation(
                root,
                [
                    {
                        "proposal_id": "d1",
                        "product_id": "odd",
                        "deprecation_posture": "repair_instead",
                        "rationale": "",
                        "supporting_evidence": {},
                        "confidence": "low",
                        "recommended_next_step": "",
                    },
                ],
            )
            payload = evaluate_portfolio_lifecycle(root)
            row = next(r for r in payload["per_product_lifecycle"] if r["product_id"] == "odd")
            self.assertEqual(row["lifecycle_status"], "mixed_or_unclear")

    def test_mixed_portfolio_summary(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "active_p", stage="maintain")
            _product(root, "early_p", stage="idea")
            _product(root, "dep_p", stage="grow")
            _write_deprecation(
                root,
                [
                    {
                        "proposal_id": "dx",
                        "product_id": "dep_p",
                        "deprecation_posture": "retire",
                        "rationale": "",
                        "supporting_evidence": {},
                        "confidence": "high",
                        "recommended_next_step": "",
                    },
                ],
            )
            _write_outcomes_min(
                root,
                per=[
                    {"product_id": "active_p", "overall_trajectory": "positive"},
                    {"product_id": "early_p", "overall_trajectory": "no_meaningful_movement"},
                    {"product_id": "dep_p", "overall_trajectory": "negative"},
                ],
                negative=["dep_p"],
            )
            _write_strategy(root, "retire")
            payload = evaluate_portfolio_lifecycle(root)
            counts = payload["lifecycle_counts"]
            self.assertEqual(counts.get("active"), 1)
            self.assertEqual(counts.get("incubating"), 1)
            self.assertEqual(counts.get("retiring"), 1)
            summ = payload["portfolio_lifecycle_summary"]
            self.assertIsInstance(summ, dict)
            self.assertIn("narrative", summ)
            self.assertGreaterEqual(len(payload["recommended_lifecycle_attention"]), 1)
            self.assertIsInstance(payload["recommended_lifecycle_attention"][0], dict)
            md = render_portfolio_lifecycle_markdown(payload)
            self.assertIn("Portfolio lifecycle synthesis", md)

    def test_run_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _product(root, "z", stage="grow")
            run_portfolio_lifecycle(root, write_artifacts=True)
            d = root / "runs" / "portfolio" / "lifecycle"
            self.assertTrue((d / "latest.json").is_file())
            raw = json.loads((d / "latest.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], PORTFOLIO_LIFECYCLE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
