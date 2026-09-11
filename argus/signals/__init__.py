"""
Signal collection framework: adapters normalize local observations into :class:`SignalRecord`.

Use :class:`AdapterRegistry` with built-ins from :mod:`argus.signals.adapters`, or register
your own implementations of :class:`argus.signals.contract.SignalAdapter`.
"""

from argus.signals.contract import ProductSignalContext, SignalAdapter
from argus.signals.ids import new_signal_id
from argus.signals.persistence import (
    SignalCollectionBundle,
    load_bundle_file,
    load_latest_bundle,
    save_collection,
)
from argus.signals.registry import AdapterRegistry, adapters_grouped_by_type
from argus.signals.runner import (
    build_context,
    collect_for_product,
    collect_inventory,
    product_root_path,
)

__all__ = [
    "AdapterRegistry",
    "ProductSignalContext",
    "SignalAdapter",
    "SignalCollectionBundle",
    "adapters_grouped_by_type",
    "build_context",
    "collect_for_product",
    "collect_inventory",
    "load_bundle_file",
    "load_latest_bundle",
    "new_signal_id",
    "product_root_path",
    "save_collection",
]
