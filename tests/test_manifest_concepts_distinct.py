"""Guard-rail: product signal manifest vs local snapshot/parser wiring are separate mechanisms."""

from __future__ import annotations

import unittest

from argus.core.models.signal_manifest import PRODUCT_SIGNAL_MANIFEST_SCHEMA
from argus.signals.snapshots.registry import snapshot_type_catalog


class TestManifestConceptsDistinct(unittest.TestCase):
    def test_product_manifest_has_own_schema(self) -> None:
        self.assertEqual(PRODUCT_SIGNAL_MANIFEST_SCHEMA, "argus.product_signal_manifest.v1")

    def test_snapshot_registry_is_filename_dispatch_not_product_manifest(self) -> None:
        rows = snapshot_type_catalog()
        self.assertGreaterEqual(len(rows), 1)
        for r in rows:
            self.assertIn("filename_contains", r)
            self.assertIn("adapter_id", r)
            self.assertNotIn("argus.product_signal_manifest", str(r.values()))
