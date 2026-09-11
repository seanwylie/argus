"""Register and resolve signal adapters by :class:`SignalType`."""

from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from argus.core.models.enums import SignalType
from argus.core.models.product import ProductNode
from argus.signals.contract import SignalAdapter


class AdapterRegistry:
    """
    Lightweight registry: multiple adapters may share a :class:`SignalType`
    (e.g. future variants); the runner invokes all that are enabled for the product.
    """

    def __init__(self, adapters: Iterable[SignalAdapter] | None = None) -> None:
        self._adapters: list[SignalAdapter] = []
        if adapters:
            for a in adapters:
                self.register(a)

    def register(self, adapter: SignalAdapter) -> None:
        self._adapters.append(adapter)

    def all_adapters(self) -> list[SignalAdapter]:
        return list(self._adapters)

    def by_signal_type(self, signal_type: SignalType) -> list[SignalAdapter]:
        return [a for a in self._adapters if a.signal_type == signal_type]

    def enabled_for_product(self, product: ProductNode) -> list[SignalAdapter]:
        """Adapters whose signal type is listed and enabled in ``product.signals``."""
        out: list[SignalAdapter] = []
        for a in self._adapters:
            if a.is_enabled_in_product(product):
                out.append(a)
        return out

    def summary_table(self) -> dict[str, dict[str, str]]:
        """``adapter_id`` → metadata for CLI."""
        rows: dict[str, dict[str, str]] = {}
        for a in self._adapters:
            rows[a.adapter_id] = {
                "signal_type": a.signal_type.value,
                "capabilities": a.describe_capabilities(),
            }
        return rows


def adapters_grouped_by_type(registry: AdapterRegistry) -> dict[SignalType, list[SignalAdapter]]:
    g: dict[SignalType, list[SignalAdapter]] = defaultdict(list)
    for a in registry.all_adapters():
        g[a.signal_type].append(a)
    return dict(g)
