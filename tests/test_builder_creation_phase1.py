"""Builder Phase 1: creation proposal + bounded scaffold (hermetic)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.builder.creation_phase1 import (
    BUILDER_CREATION_PROPOSAL_SCHEMA,
    BUILDER_CREATION_RESULT_SCHEMA,
    apply_creation_proposal,
    build_creation_proposal,
    load_latest_creation_candidates,
    render_creation_proposal_markdown,
    write_creation_proposal_artifacts,
)
from argus.products.inventory import build_inventory
from argus.world_context.persist import (
    WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
    write_creation_candidates_artifact,
)


def _minimal_cc_payload() -> dict:
    return {
        "schema": WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
        "generated_at_utc": "2026-01-01T00:00:00Z",
        "advisory_only": True,
        "disclaimer": "d",
        "candidates": [
            {
                "candidate_id": "conversion_wrapper_around_spike",
                "candidate_kind": "action",
                "title": "Capture traffic spike",
                "rationale": "Spike with no conversion yet.",
                "entities": ["demo-product"],
                "evidence_summary": "traffic high",
                "interpretation_patterns": ["traffic_contrast", "spike_contrast"],
                "strength": "medium",
            }
        ],
        "limitations": [],
    }


class TestBuilderCreationPhase1(unittest.TestCase):
    def test_build_proposal_from_candidate(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_creation_candidates_artifact(root, _minimal_cc_payload())
            prop = build_creation_proposal(root, candidate_id="conversion_wrapper_around_spike")
            self.assertEqual(prop["schema"], BUILDER_CREATION_PROPOSAL_SCHEMA)
            self.assertEqual(prop["resolved_product_id"], "demo-product")
            self.assertIn("products/demo-product/product.yaml", prop["files_to_create"])
            self.assertIn("import_plan_notes", prop)
            md = render_creation_proposal_markdown(prop)
            self.assertIn("Builder Phase 1", md)
            self.assertIn("demo-product", md)

    def test_proposal_explicit_product_id(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_creation_candidates_artifact(root, _minimal_cc_payload())
            prop = build_creation_proposal(
                root,
                candidate_id="conversion_wrapper_around_spike",
                operator_product_id="my-demo-app",
            )
            self.assertEqual(prop["resolved_product_id"], "my-demo-app")

    def test_dry_run_apply_writes_no_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_creation_candidates_artifact(root, _minimal_cc_payload())
            prop = build_creation_proposal(root, candidate_id="conversion_wrapper_around_spike")
            res = apply_creation_proposal(root, prop, write=False)
            self.assertEqual(res["schema"], BUILDER_CREATION_RESULT_SCHEMA)
            self.assertTrue(res.get("dry_run"))
            self.assertTrue(res.get("ok"))
            self.assertFalse((root / "products").exists())

    def test_write_creates_valid_inventory(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdir = root / "products"
            pdir.mkdir(parents=True)
            write_creation_candidates_artifact(root, _minimal_cc_payload())
            prop = build_creation_proposal(root, candidate_id="conversion_wrapper_around_spike")
            res = apply_creation_proposal(root, prop, write=True, products_dir=pdir)
            self.assertTrue(res.get("ok"), msg=str(res))
            inv = build_inventory(root, products_dir=pdir)
            self.assertIn("demo-product", inv.valid)
            py = pdir / "demo-product" / "product.yaml"
            self.assertTrue(py.is_file())

    def test_load_candidates_roundtrip(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_creation_candidates_artifact(root, _minimal_cc_payload())
            cc = load_latest_creation_candidates(root)
            assert cc is not None
            self.assertEqual(cc["schema"], WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA)

    def test_write_proposal_artifacts(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_creation_candidates_artifact(root, _minimal_cc_payload())
            prop = build_creation_proposal(root, candidate_id="conversion_wrapper_around_spike")
            jp, mp = write_creation_proposal_artifacts(root, prop)
            self.assertTrue(jp.is_file())
            self.assertTrue(mp.is_file())
            data = json.loads(jp.read_text(encoding="utf-8"))
            self.assertEqual(data["schema"], BUILDER_CREATION_PROPOSAL_SCHEMA)

    def test_missing_creation_candidates_errors(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                build_creation_proposal(root, candidate_id="x")


if __name__ == "__main__":
    unittest.main()
