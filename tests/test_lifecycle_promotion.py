"""Lifecycle promotion actions (``argus.lifecycle_promotion_action.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.creation import (
    PRODUCT_CREATION_PROPOSALS_SCHEMA,
    creation_proposals_dir,
    evaluate_creation_proposals,
)
from argus.products.deprecation import PRODUCT_DEPRECATION_PROPOSALS_SCHEMA
from argus.products.promotion import (
    LIFECYCLE_PROMOTION_ACTION_SCHEMA,
    promotion_actions_dir,
    run_promote_bootstrap,
    run_promote_creation,
    run_promote_deprecation,
)


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
    risk_posture: moderate
    weights:
      revenue_alignment: 1.0
""",
        encoding="utf-8",
    )


def _seed_creation_proposal(root: Path) -> str:
    _write_mission(root)
    cp = evaluate_creation_proposals(root)
    assert cp["schema"] == PRODUCT_CREATION_PROPOSALS_SCHEMA
    pid = str(cp["proposals"][0]["proposal_id"])
    d = creation_proposals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    (d / "latest.json").write_text(json.dumps(cp), encoding="utf-8")
    return pid


def _write_deprecation_bundle(root: Path, proposal_id: str, product_id: str) -> None:
    d = root / "runs" / "products" / "deprecation"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "proposal_count": 1,
        "proposals": [
            {
                "proposal_id": proposal_id,
                "product_id": product_id,
                "deprecation_posture": "retire",
                "rationale": "test",
                "supporting_evidence": {"portfolio_outcomes": {"overall_trajectory": "negative"}},
                "confidence": "medium",
            }
        ],
        "inputs": {},
    }
    (d / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


class TestLifecyclePromotion(unittest.TestCase):
    def test_promote_creation_dry_run_writes_promotion_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = _seed_creation_proposal(root)
            out = run_promote_creation(
                root,
                proposal_id=pid,
                product_id="promo-dry",
                dry_run=True,
                write_promotion_artifact=True,
                write_stage_artifacts=True,
            )
            self.assertEqual(out["schema"], LIFECYCLE_PROMOTION_ACTION_SCHEMA)
            self.assertEqual(out["result_status"], "success")
            self.assertTrue(out["dry_run"])
            pa = promotion_actions_dir(root) / "latest.json"
            self.assertTrue(pa.is_file())

    def test_promote_creation_scaffold_no_duplicate_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pid = _seed_creation_proposal(root)
            (root / "products" / "promo-dup").mkdir(parents=True)
            out = run_promote_creation(
                root,
                proposal_id=pid,
                product_id="promo-dup",
                dry_run=False,
                write_promotion_artifact=True,
                write_stage_artifacts=True,
            )
            self.assertEqual(out["result_status"], "failed")
            self.assertIn("nested_results", out)

    def test_promote_deprecation_dry_run(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_deprecation_bundle(root, "dep_promo_1", "p9")
            out = run_promote_deprecation(
                root,
                proposal_id="dep_promo_1",
                dry_run=True,
                write_promotion_artifact=True,
                write_stage_artifacts=False,
            )
            self.assertEqual(out["result_status"], "success")
            self.assertTrue(out["dry_run"])
            self.assertEqual(out["created_artifacts"], [])

    def test_promote_deprecation_plan_writes(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_deprecation_bundle(root, "dep_promo_2", "p8")
            out = run_promote_deprecation(
                root,
                proposal_id="dep_promo_2",
                dry_run=False,
                write_promotion_artifact=True,
                write_stage_artifacts=True,
            )
            self.assertEqual(out["result_status"], "success")
            self.assertIn("runs/products/deprecation_plan/latest.json", out["created_artifacts"])
            self.assertTrue((root / "runs" / "products" / "deprecation_plan" / "latest.json").is_file())

    def test_invalid_deprecation_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = run_promote_deprecation(
                root,
                proposal_id="does_not_exist",
                dry_run=False,
                write_promotion_artifact=True,
                write_stage_artifacts=True,
            )
            self.assertEqual(out["result_status"], "failed")

    def test_promote_bootstrap_no_save(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = run_promote_bootstrap(
                root,
                product_id="missing_product",
                dry_run=False,
                write_promotion_artifact=False,
                write_stage_artifacts=False,
            )
            self.assertEqual(out["result_status"], "failed")
            self.assertFalse((root / "runs" / "products" / "promotion_actions").exists())
