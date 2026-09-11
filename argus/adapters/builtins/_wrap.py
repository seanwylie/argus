"""Shared helper: wrap a :class:`~argus.signals.contract.SignalAdapter` in the layered API."""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from argus.adapters.base import Adapter, AdapterCategory, AdapterContext, stable_json_sort_key
from argus.core.models.signal import SignalRecord
from argus.core.serialize import signal_record_from_dict, signal_record_to_dict
from argus.signals.contract import SignalAdapter


class SignalAdapterWrapper(Adapter):
    """
    collect → list[dict] → sorted dicts → SignalRecords (round-trip via serialize).

    Abstract base: set :attr:`category` in a concrete subclass. Known implementations:
    :class:`~argus.adapters.builtins.execution.ExecutionIntegrationAdapter`,
    :class:`~argus.adapters.builtins.filesystem.FilesystemIntegrationAdapter`,
    :class:`~argus.adapters.builtins.metrics.MetricsIntegrationAdapter`,
    :class:`~argus.adapters.builtins.temporal.TemporalIntegrationAdapter`.
    """

    def __init__(self, inner: SignalAdapter) -> None:
        self._inner = inner
        self.adapter_id = inner.adapter_id

    @property
    @abstractmethod
    def category(self) -> AdapterCategory:
        """Registry bucket; implemented by each integration adapter subclass."""

    def collect(self, ctx: AdapterContext) -> Any:
        recs = self._inner.collect(ctx)
        return [signal_record_to_dict(r) for r in recs]

    def normalize(self, raw: Any) -> Any:
        if not isinstance(raw, list):
            return raw
        return sorted(raw, key=stable_json_sort_key)

    def to_signal_records(self, normalized: Any, ctx: AdapterContext) -> list[SignalRecord]:
        if not isinstance(normalized, list):
            return []
        out: list[SignalRecord] = []
        for item in normalized:
            if not isinstance(item, dict):
                continue
            r = signal_record_from_dict(item)
            if "adapter_layer" not in r.tags:
                r.tags = list(r.tags) + ["adapter_layer"]
            out.append(r)
        return out
