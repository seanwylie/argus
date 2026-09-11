"""Reconcile product signal manifest declarations with collected adapter rows."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import ProductSignalManifestEntry
from argus.products.signal_manifest import enabled_manifest_entries
from argus.signals.manifest_bridge import match_manifest_entry
from argus.signals.registry import AdapterRegistry

_PLACEHOLDER_OBSERVED_AT = datetime(1970, 1, 1, tzinfo=timezone.utc)
PLACEHOLDER_SOURCE = "manifest_declaration"


def placeholder_signal_id(product_id: str, manifest_signal_id: str) -> str:
    """Stable id for synthetic declaration rows (no randomness)."""
    h = hashlib.sha256(f"{product_id}\0{manifest_signal_id}".encode("utf-8")).hexdigest()[:20]
    return f"sig-decl-{h}"


def registry_supports_signal_type(registry: AdapterRegistry, st: object) -> bool:
    """True if at least one registered adapter collects this :class:`~argus.core.models.enums.SignalType`."""
    return any(a.signal_type == st for a in registry.all_adapters())


def _record_matches_entry(
    record: SignalRecord,
    entry: ProductSignalManifestEntry,
    product_root: Path,
) -> bool:
    m = match_manifest_entry(
        record,
        entries=[entry],
        product_root=product_root,
    )
    return m is not None and m.id == entry.id


def reconcile_manifest_declarations(
    records: list[SignalRecord],
    product: ProductNode,
    product_root: Path,
    registry: AdapterRegistry,
) -> list[SignalRecord]:
    """
    Ensure every enabled manifest declaration has a matching collected row or an explicit gap row.

    Appends deterministic synthetic :class:`SignalRecord` rows (source ``manifest_declaration``)
    when no emitted record matches the declaration. ``collection_status`` in payload is
    ``unsupported_source_type`` when no adapter is registered for that ``source_type``, else
    ``missing``.
    """
    entries = enabled_manifest_entries(product)
    if not entries:
        return list(records)

    sorted_entries = sorted(entries, key=lambda e: e.id)
    sorted_records = sorted(records, key=lambda r: r.id)
    used: set[str] = set()
    out_extra: list[SignalRecord] = []

    for entry in sorted_entries:
        matched = False
        for r in sorted_records:
            if r.id in used:
                continue
            if r.signal_type != entry.source_type:
                continue
            if _record_matches_entry(r, entry, product_root):
                used.add(r.id)
                matched = True
                break
        if matched:
            continue

        if not registry_supports_signal_type(registry, entry.source_type):
            status = "unsupported_source_type"
            reason = "no_local_adapter_registered_for_signal_type"
        else:
            status = "missing"
            reason = "no_collected_record_matched_declaration"

        out_extra.append(
            SignalRecord(
                id=placeholder_signal_id(product.id, entry.id),
                product_id=product.id,
                signal_type=entry.source_type,
                source=PLACEHOLDER_SOURCE,
                observed_at=_PLACEHOLDER_OBSERVED_AT,
                payload={
                    "collection_status": status,
                    "manifest_signal_id": entry.id,
                    "reason": reason,
                    **({"path": entry.path} if entry.path and str(entry.path).strip() else {}),
                    **(
                        {"source_ref": entry.source_ref}
                        if entry.source_ref and str(entry.source_ref).strip()
                        else {}
                    ),
                },
                tags=sorted({"manifest_declaration", status}),
            )
        )

    return list(records) + out_extra
