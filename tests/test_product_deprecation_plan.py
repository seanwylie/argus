"""Product deprecation plan (``argus.product_deprecation_plan.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.products.deprecation import PRODUCT_DEPRECATION_PROPOSALS_SCHEMA
from argus.products.deprecation_plan import (
    PRODUCT_DEPRECATION_PLAN_SCHEMA,
    deprecation_plan_dir,
    evaluate_deprecation_plan,
    find_deprecation_proposal,
    run_deprecation_plan,
)


def _minimal_proposal(
    *,
    proposal_id: str,
    product_id: str,
    posture: str,
) -> dict:
    return {
        "proposal_id": proposal_id,
        "product_id": product_id,
        "deprecation_posture": posture,
        "rationale": "test rationale",
        "supporting_evidence": {
            "portfolio_outcomes": {"overall_trajectory": "negative"},
        },
        "confidence": "medium",
        "recommended_next_step": "next",
    }


def _write_proposals_bundle(root: Path, proposals: list[dict], *, name: str = "latest.json") -> None:
    d = root / "runs" / "products" / "deprecation"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
        "evaluated_at_utc": "2020-01-01T00:00:00Z",
        "proposal_count": len(proposals),
        "proposals": proposals,
        "inputs": {},
    }
    (d / name).write_text(json.dumps(payload), encoding="utf-8")


class TestDeprecationPlan(unittest.TestCase):
    def test_retire_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(
                root,
                [_minimal_proposal(proposal_id="deprecation_abc123", product_id="p1", posture="retire")],
            )
            pl = evaluate_deprecation_plan(root, "deprecation_abc123")
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl["schema"], PRODUCT_DEPRECATION_PLAN_SCHEMA)
            self.assertEqual(pl["deprecation_posture"], "retire")
            ids = [s["step_id"] for s in pl["plan_steps"]]
            self.assertTrue(any(x.startswith("dep_plan.retire.") for x in ids))

    def test_harvest_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(
                root,
                [_minimal_proposal(proposal_id="deprecation_h1", product_id="h1", posture="harvest")],
            )
            pl = evaluate_deprecation_plan(root, "deprecation_h1")
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl["deprecation_posture"], "harvest")
            self.assertTrue(any(s["step_id"].startswith("dep_plan.harvest.") for s in pl["plan_steps"]))

    def test_archive_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(
                root,
                [_minimal_proposal(proposal_id="deprecation_a1", product_id="a1", posture="archive")],
            )
            pl = evaluate_deprecation_plan(root, "deprecation_a1")
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl["deprecation_posture"], "archive")
            self.assertTrue(any(s["step_id"].startswith("dep_plan.archive.") for s in pl["plan_steps"]))

    def test_repair_instead_plan(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(
                root,
                [_minimal_proposal(proposal_id="deprecation_r1", product_id="r1", posture="repair_instead")],
            )
            pl = evaluate_deprecation_plan(root, "deprecation_r1")
            self.assertTrue(pl.get("ok"))
            self.assertEqual(pl["deprecation_posture"], "repair_instead")
            self.assertTrue(any(s["step_id"].startswith("dep_plan.repair.") for s in pl["plan_steps"]))
            self.assertIn("re-evaluated", pl["archive_or_tombstone_recommendation"].lower())

    def test_lookup_from_stamped_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            prop = _minimal_proposal(
                proposal_id="deprecation_stamp1",
                product_id="s1",
                posture="retire",
            )
            _write_proposals_bundle(root, [prop], name="20240101T000000Z.json")
            found = find_deprecation_proposal(root, "deprecation_stamp1")
            self.assertIsNotNone(found)
            self.assertEqual(found["proposal_id"], "deprecation_stamp1")
            pl = evaluate_deprecation_plan(root, "deprecation_stamp1")
            self.assertTrue(pl.get("ok"))

    def test_unknown_proposal(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(root, [])
            pl = evaluate_deprecation_plan(root, "missing_id")
            self.assertFalse(pl.get("ok"))
            self.assertIn("error", pl)

    def test_no_save_skips_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(
                root,
                [_minimal_proposal(proposal_id="deprecation_ns", product_id="n1", posture="retire")],
            )
            run_deprecation_plan(root, proposal_id="deprecation_ns", write_artifacts=False)
            self.assertFalse(deprecation_plan_dir(root).exists())

    def test_writes_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_proposals_bundle(
                root,
                [_minimal_proposal(proposal_id="deprecation_w", product_id="w1", posture="retire")],
            )
            run_deprecation_plan(root, proposal_id="deprecation_w", write_artifacts=True)
            d = deprecation_plan_dir(root)
            self.assertTrue((d / "latest.json").is_file())


if __name__ == "__main__":
    unittest.main()
