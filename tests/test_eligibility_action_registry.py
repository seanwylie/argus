"""Mechanical checks for orchestration ACTION_ELIGIBILITY_REGISTRY and watermark dispatch."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from argus.orchestrator import eligibility_watermarks as wm
from argus.orchestrator.eligibility import (
    _ORDERED_REGISTRY_ELIGIBLE_EMISSION,
    ACTION_ELIGIBILITY_REGISTRY,
    _relevant_product_evidence_watermark_utc,
)
from argus.orchestrator.state_models import ACTION_FINDINGS_GENERATE


class TestEligibilityActionRegistry(unittest.TestCase):
    def test_ordered_emission_matches_registry_keys(self) -> None:
        self.assertEqual(list(ACTION_ELIGIBILITY_REGISTRY.keys()), list(_ORDERED_REGISTRY_ELIGIBLE_EMISSION))

    def test_each_entry_has_required_fields(self) -> None:
        for aid, spec in ACTION_ELIGIBILITY_REGISTRY.items():
            with self.subTest(action_id=aid):
                self.assertIsNotNone(spec.get("gate_fn"))
                self.assertIsNotNone(spec.get("watermark_fn"))
                self.assertIsNotNone(spec.get("reason_code"))
                self.assertIsInstance(spec.get("reason_codes"), tuple)
                self.assertTrue(spec.get("eligible_flag_name"))
                self.assertIsInstance(spec.get("reason"), str)

    def test_watermark_fn_matches_dispatch_table(self) -> None:
        for aid, spec in ACTION_ELIGIBILITY_REGISTRY.items():
            with self.subTest(action_id=aid):
                self.assertIs(
                    spec["watermark_fn"],
                    wm.WATERMARK_FOR_ACTION_ID.get(aid),
                    msg="registry watermark_fn must match WATERMARK_FOR_ACTION_ID",
                )

    def test_watermark_delegate_matches_module_function(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ctx_like = SimpleNamespace(
                sig=SimpleNamespace(collected_at_utc=None),
                root=root,
                product_id="p",
            )
            w1 = wm.relevant_product_evidence_watermark_utc(ACTION_FINDINGS_GENERATE, ctx_like)
            w2 = _relevant_product_evidence_watermark_utc(ACTION_FINDINGS_GENERATE, ctx_like)
            self.assertEqual(w1, w2)


if __name__ == "__main__":
    unittest.main()
