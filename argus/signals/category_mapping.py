"""Explicit mapping from product declarations to canonical ``category`` (deterministic)."""

from __future__ import annotations

from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import ProductSignalManifestEntry

# Provenance values stored on canonical rows (``provenance["category_source"]``).
CATEGORY_SOURCE_MANIFEST = "product_signal_manifest"
CATEGORY_SOURCE_SIGNAL_TYPE = "signal_type_enum_fallback"


def resolve_canonical_category(
    record: SignalRecord,
    manifest_entry: ProductSignalManifestEntry | None,
) -> tuple[str, str]:
    """
    Return ``(category_string, category_source)``.

    When a product signal manifest row matches, category is the manifest
    :class:`~argus.core.models.signal_manifest.SignalManifestCategory` value.
    Otherwise category falls back to :attr:`SignalRecord.signal_type` (transport enum),
    **not** a business taxonomy — callers should treat that as a weak label.
    """
    if manifest_entry is not None:
        return manifest_entry.category.value, CATEGORY_SOURCE_MANIFEST
    return record.signal_type.value, CATEGORY_SOURCE_SIGNAL_TYPE
