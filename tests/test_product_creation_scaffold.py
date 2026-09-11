"""Product creation scaffold (``argus.product_creation_scaffold.v1``)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import yaml

from argus.products.creation import (
    PRODUCT_CREATION_PROPOSALS_SCHEMA,
    creation_proposals_dir,
    evaluate_creation_proposals,
)
from argus.products.creation_scaffold import (
    PRODUCT_CREATION_SCAFFOLD_SCHEMA,
    evaluate_product_creation_scaffold,
    run_product_creation_scaffold,
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
  education:
    id: education
    primary_objective: Maximize learning outcomes.
    drivers:
      - pedagogical quality
    risk_posture: conservative
    weights:
      learning_quality: 1.0
""",
        encoding="utf-8",
    )


class TestProductCreationScaffold(unittest.TestCase):
    def _seed_proposals(self, root: Path) -> tuple[dict, str]:
        _write_mission(root, mission_id="revenue")
        cp = evaluate_creation_proposals(root)
        self.assertEqual(cp["schema"], PRODUCT_CREATION_PROPOSALS_SCHEMA)
        self.assertGreater(len(cp.get("proposals") or []), 0)
        prop = cp["proposals"][0]
        pid = str(prop["proposal_id"])
        d = creation_proposals_dir(root)
        d.mkdir(parents=True, exist_ok=True)
        (d / "latest.json").write_text(json.dumps(cp), encoding="utf-8")
        return cp, pid

    def test_scaffold_from_proposal_minimal_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, pid = self._seed_proposals(root)
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id_override="scaffolded-alpha",
                dry_run=False,
            )
            self.assertTrue(out.get("ok"), msg=out.get("error"))
            self.assertEqual(out["schema"], PRODUCT_CREATION_SCAFFOLD_SCHEMA)
            pr = root / "products" / "scaffolded-alpha"
            self.assertTrue((pr / "product.yaml").is_file())
            self.assertTrue((pr / "signals.yaml").is_file())
            self.assertTrue((pr / "doctrine.yaml").is_file())
            self.assertTrue((pr / "notes" / "CREATION.md").is_file())
            self.assertTrue((pr / "scripts" / "start.sh").is_file())

    def test_dry_run_lists_planned_files(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, pid = self._seed_proposals(root)
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id_override="dry-only",
                dry_run=True,
            )
            self.assertTrue(out.get("ok"))
            self.assertTrue(out.get("planned_files"))
            self.assertFalse((root / "products").exists())

    def test_duplicate_product_id_rejected(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, pid = self._seed_proposals(root)
            (root / "products" / "taken").mkdir(parents=True)
            (root / "products" / "taken" / ".gitkeep").write_text("x", encoding="utf-8")
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id_override="taken",
                dry_run=False,
            )
            self.assertFalse(out.get("ok"))
            self.assertIn("existing", str(out.get("error") or "").lower())

    def test_mission_block_objective_and_risk(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, pid = self._seed_proposals(root)
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id_override="mission-check",
                dry_run=False,
            )
            self.assertTrue(out.get("ok"))
            raw = yaml.safe_load((root / "products" / "mission-check" / "product.yaml").read_text())
            self.assertEqual(raw["mission"]["objective"], "revenue")
            self.assertEqual(raw["mission"]["risk_posture"], "moderate")
            self.assertIsInstance(raw["mission"]["drivers"], list)
            self.assertIsInstance(raw["mission"]["guardrails"], list)
            # v1 proposals emit registry profile ids; scaffold copies them without dropping to empty.
            self.assertEqual(raw["mission"]["drivers"], ["education"])
            self.assertEqual(raw["mission"]["guardrails"], [])

    def test_legacy_proposal_filters_non_id_driver_strings(self) -> None:
        """Without v1 registry-id contract, prose in drivers is dropped; known ids kept."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            cp = evaluate_creation_proposals(root)
            self.assertGreater(len(cp.get("proposals") or []), 0)
            prop = dict(cp["proposals"][0])
            sm = dict(prop.get("initial_suggested_mission") or {})
            sm.pop("schema", None)
            sm.pop("mission_profile_fields_are_registry_ids", None)
            sm["drivers"] = ["not a registry id", "education", "  EDUCATION  "]
            sm["guardrails"] = ["bogus", "revenue"]
            prop["initial_suggested_mission"] = sm
            cp["proposals"] = [prop]
            pid = str(prop["proposal_id"])
            d = creation_proposals_dir(root)
            d.mkdir(parents=True, exist_ok=True)
            (d / "latest.json").write_text(json.dumps(cp), encoding="utf-8")
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id_override="legacy-mission",
                dry_run=False,
            )
            self.assertTrue(out.get("ok"), msg=out.get("error"))
            raw = yaml.safe_load((root / "products" / "legacy-mission" / "product.yaml").read_text())
            self.assertEqual(raw["mission"]["objective"], "revenue")
            self.assertEqual(raw["mission"]["drivers"], ["education"])
            # revenue duplicates objective; filtered from guardrails
            self.assertEqual(raw["mission"]["guardrails"], [])

    def test_creation_mission_in_payload(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, pid = self._seed_proposals(root)
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id_override="cm-payload",
                dry_run=True,
            )
            self.assertTrue(out.get("ok"))
            cms = out.get("creation_mission_at_scaffold") or {}
            self.assertEqual(cms.get("resolved_mission_id"), "revenue")

    def test_unknown_proposal_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, _pid = self._seed_proposals(root)
            out = evaluate_product_creation_scaffold(
                root,
                proposal_id="creation_does_not_exist",
                product_id_override="x",
                dry_run=True,
            )
            self.assertFalse(out.get("ok"))
            self.assertIn("not found", str(out.get("error") or "").lower())

    def test_writes_artifact(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_mission(root)
            _cp, pid = self._seed_proposals(root)
            run_product_creation_scaffold(
                root,
                proposal_id=pid,
                product_id="art-write",
                dry_run=True,
                write_artifacts=True,
            )
            p = root / "runs" / "products" / "creation_scaffold" / "latest.json"
            self.assertTrue(p.is_file())
            raw = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(raw["schema"], PRODUCT_CREATION_SCAFFOLD_SCHEMA)


if __name__ == "__main__":
    unittest.main()
