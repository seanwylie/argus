"""Contract for signal adapters: collect raw observations → :class:`SignalRecord`."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from argus.core.models.enums import SignalType
from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord


@dataclass(frozen=True)
class ProductSignalContext:
    """
    Everything an adapter needs to read local product state.

    ``product_root`` is absolute and points at the product directory (parent of
    ``app/``, ``metrics/``, etc.).
    """

    repo_root: Path
    product: ProductNode
    product_root: Path

    @property
    def product_id(self) -> str:
        return self.product.id


class SignalAdapter(ABC):
    """
    Pluggable collector: reads local files/layout and emits normalized records.

    Adapters must not call remote APIs in this phase of Argus.
    """

    #: Short id for ``SignalRecord.source`` and CLI listings (e.g. ``filesystem``).
    adapter_id: str = "adapter"

    @property
    @abstractmethod
    def signal_type(self) -> SignalType:
        """Which :class:`SignalType` this adapter feeds."""

    def is_enabled_in_product(self, product: ProductNode) -> bool:
        """True if ``product.yaml`` or the product signal manifest enables this adapter's type."""
        want = self.signal_type.value
        for s in product.signals:
            if str(s.type).lower() == want and s.enabled:
                return True
        sm = product.signal_manifest
        if sm is not None:
            for e in sm.signals:
                if e.enabled and e.source_type == self.signal_type:
                    return True
        return False

    def describe_capabilities(self) -> str:
        """Human-readable note on what config paths or dirs this adapter uses."""
        return ""

    @abstractmethod
    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        """Emit zero or more :class:`SignalRecord` instances."""
