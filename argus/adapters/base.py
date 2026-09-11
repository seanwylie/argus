"""Base adapter interface: collect → normalize → :class:`~argus.core.models.signal.SignalRecord`."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.contract import ProductSignalContext

# Alias: adapter layer uses the same context as signal adapters.
AdapterContext = ProductSignalContext


class AdapterCategory(StrEnum):
    """High-level grouping for registration and configuration."""

    EXECUTION = "execution"
    FILESYSTEM = "filesystem"
    METRICS = "metrics"
    TEMPORAL = "temporal"
    API = "api"  # reserved — no network I/O in core Argus today


class Adapter(ABC):
    """
    Reusable integration boundary for local data sources.

    Pipeline (deterministic, no remote calls in core implementations):

    1. ``collect`` — read raw material (paths, JSON blobs, structured dicts).
    2. ``normalize`` — stable sort / canonical shape for tests and merges.
    3. ``to_signal_records`` — emit :class:`SignalRecord` instances.
    """

    #: Unique id (e.g. ``execution``) — matches signal adapter ids where wrapped.
    adapter_id: str = "adapter"

    @property
    @abstractmethod
    def category(self) -> AdapterCategory:
        """Registry bucket."""

    @abstractmethod
    def collect(self, ctx: AdapterContext) -> Any:
        """Load raw observations (JSON-friendly)."""

    @abstractmethod
    def normalize(self, raw: Any) -> Any:
        """Canonicalize ``raw`` for stable output (e.g. sort lists)."""

    @abstractmethod
    def to_signal_records(self, normalized: Any, ctx: AdapterContext) -> list[SignalRecord]:
        """Build signal records from normalized data."""

    def run_pipeline(self, ctx: AdapterContext) -> list[SignalRecord]:
        """Run collect → normalize → to_signal_records."""
        raw = self.collect(ctx)
        norm = self.normalize(raw)
        return self.to_signal_records(norm, ctx)


def stable_json_sort_key(obj: Any) -> str:
    """Deterministic key for sorting structured payloads."""
    try:
        return json.dumps(obj, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(obj)
