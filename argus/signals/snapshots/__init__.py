"""Local business snapshot ingestion (JSON/CSV) → normalized :class:`SignalRecord`."""

from argus.signals.snapshots.ingest import ingest_snapshots_for_product
from argus.signals.snapshots.registry import snapshot_type_catalog

__all__ = [
    "ingest_snapshots_for_product",
    "snapshot_type_catalog",
]
