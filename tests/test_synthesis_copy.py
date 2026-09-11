"""Deterministic synthesis copy tightening (no LLM)."""

from __future__ import annotations

import unittest

from argus.idea_generation.synthesis_copy import finalize_synthesis_description
from argus.idea_generation.synthesis_grounding import build_grounding


class TestFinalizeSynthesisDescription(unittest.TestCase):
    def test_domain_fuse_single_product_framing(self) -> None:
        g = build_grounding(
            grounding_kind="file_context",
            grounding_strength="low",
            grounding_sources=[
                {"type": "repo_path", "path": "products/x"},
                {"type": "combinatorial", "pattern": "domain_fuse"},
            ],
        )
        raw = (
            "Pure combinatorial pad (low grounding): a × b; web / ads. "
            "Product tree: `products/x` — not tied to a specific finding or signal id."
        )
        out = finalize_synthesis_description(
            raw,
            g,
            pattern="domain_fuse",
            product_label="MyApp",
            product_id="x",
            product_root_rel="products/x",
            single_product=True,
        )
        self.assertIn("For MyApp (repo `products/x`), combinatorial sketch:", out)
        self.assertNotIn("Pure combinatorial pad (low grounding):", out)
        self.assertNotIn("Basis (inspectable):", out)

    def test_basis_appended_for_portfolio_pair(self) -> None:
        g = build_grounding(
            grounding_kind="weak",
            grounding_strength="low",
            grounding_sources=[{"type": "portfolio_pair", "product_a": "p1", "product_b": "p2"}],
        )
        raw = "Cross-pollinate. Anchor."
        out = finalize_synthesis_description(
            raw,
            g,
            pattern="product_pair",
            product_label=None,
            product_id=None,
            product_root_rel="",
            single_product=False,
        )
        self.assertIn("Basis (inspectable):", out)
        self.assertIn("`p1` × `p2`", out)

    def test_signal_lattice_path_fix(self) -> None:
        g = build_grounding(
            grounding_kind="signal_cluster",
            grounding_strength="low",
            grounding_sources=[{"type": "signal_type_only", "value": "metrics"}],
        )
        raw = (
            "Evidence: signal family name only (`metrics`) — inspect "
            "runs/signals/latest/<product_id>.json for concrete rows."
        )
        out = finalize_synthesis_description(
            raw,
            g,
            pattern="signal_monetization",
            product_label="SR",
            product_id="demo-content",
            product_root_rel="products/sr",
            single_product=True,
        )
        self.assertIn("runs/signals/latest/demo-content.json", out)
        self.assertIn("For SR:", out)

    def test_finding_grounded_gets_product_lead(self) -> None:
        g = build_grounding(
            grounding_kind="finding",
            grounding_strength="high",
            grounding_sources=[{"type": "finding", "id": "f1", "kind": "gap"}],
        )
        raw = "Grounded in finding `f1`."
        out = finalize_synthesis_description(
            raw,
            g,
            pattern="finding_grounded",
            product_label="P",
            product_id="p",
            product_root_rel="products/p",
            single_product=True,
        )
        self.assertTrue(out.startswith("For P: "))


if __name__ == "__main__":
    unittest.main()
