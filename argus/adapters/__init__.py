"""
Adapter layer: structured ``collect`` → ``normalize`` → ``to_signal_records`` for integrations.

Classic signal collection remains in :mod:`argus.signals`; this package wraps those
implementations with an explicit pipeline and optional config under ``config/adapters.json``.
"""

from argus.adapters.base import Adapter, AdapterCategory, AdapterContext
from argus.adapters.loader import load_adapter_config, load_adapters_for_repo
from argus.adapters.pipeline import merge_adapter_layer, run_adapter_layer
from argus.adapters.registry import registered_adapters, summary_table

__all__ = [
    "Adapter",
    "AdapterCategory",
    "AdapterContext",
    "load_adapter_config",
    "load_adapters_for_repo",
    "merge_adapter_layer",
    "registered_adapters",
    "run_adapter_layer",
    "summary_table",
]
