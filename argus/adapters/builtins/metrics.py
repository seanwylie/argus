"""Local metrics JSON/JSONL files (layered API)."""

from __future__ import annotations

from argus.adapters.base import AdapterCategory
from argus.adapters.builtins._wrap import SignalAdapterWrapper
from argus.signals.adapters.metrics_file import MetricsFileAdapter


class MetricsIntegrationAdapter(SignalAdapterWrapper):
    """Delegates to :class:`~argus.signals.adapters.metrics_file.MetricsFileAdapter`."""

    def __init__(self) -> None:
        super().__init__(MetricsFileAdapter())
        # Stable layer id (config ``enabled_ids`` uses ``metrics``).
        self.adapter_id = "metrics"

    @property
    def category(self) -> AdapterCategory:
        return AdapterCategory.METRICS
