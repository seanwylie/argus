"""Context packet assembly (deterministic; no network)."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from argus.context import (
    PACKET_SCHEMA,
    ContextPurpose,
    assemble_context_bundle,
    is_context_packets_enabled,
    refinement_grounded_bundle,
)
from argus.context.formatting import bundle_to_refinement_prompt_addon
from argus.refinement.models import ArtifactDraft, ArtifactType, GeneratedBy


def _minimal_product(root: Path, product_id: str = "ctx_p1") -> None:
    pr = root / "products" / product_id
    pr.mkdir(parents=True)
    (pr / "product.yaml").write_text(
        f"""
id: {product_id}
name: CtxTest
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
    (pr / "scripts").mkdir(exist_ok=True)
    (pr / "scripts" / "s.sh").write_text("#!/bin/sh\ntrue\n", encoding="utf-8")


class TestContextPackets(unittest.TestCase):
    def test_flag_default_off(self) -> None:
        self.assertFalse(is_context_packets_enabled())

    def test_bundle_schema_and_version(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            b = assemble_context_bundle(root, ContextPurpose.REFINEMENT_GROUNDED, "p1", draft=None)
            self.assertEqual(b.get("packet_schema"), PACKET_SCHEMA)
            self.assertEqual(b.get("packet_version"), 1)
            self.assertEqual(b.get("purpose"), "refinement_grounded")
            self.assertIn("context_sources", b)
            ps = b["product"]["system"]
            self.assertTrue(ps.get("present"))

    def test_refinement_bundle_with_draft(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            _minimal_product(root, "p1")
            d = ArtifactDraft(
                draft_id="d1",
                session_id="ref_x",
                round_number=0,
                artifact_type=ArtifactType.IDEA,
                title="T",
                content="body " * 100,
                structured_fields={},
                created_at_utc="2026-01-01T00:00:00+00:00",
                generated_by=GeneratedBy.DETERMINISTIC,
            )
            b = refinement_grounded_bundle(root, "p1", d)
            self.assertIn("artifact", b)
            self.assertEqual(b["artifact"]["draft"]["session_id"], "ref_x")
            text = bundle_to_refinement_prompt_addon(b)
            self.assertIn("CONTEXT PACKET", text)
            self.assertIn("END CONTEXT PACKET", text)

    def test_bundle_no_product(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            b = assemble_context_bundle(root, ContextPurpose.REFINEMENT_GROUNDED, None, draft=None)
            self.assertFalse(b["product"]["system"].get("present"))
