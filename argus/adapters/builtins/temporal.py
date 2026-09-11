"""Temporal snapshot files (adapter layer; delegates to :class:`TemporalSnapshotSignalAdapter`)."""

from __future__ import annotations

from argus.adapters.base import AdapterCategory
from argus.adapters.builtins._wrap import SignalAdapterWrapper
from argus.signals.adapters.temporal_snapshots import TemporalSnapshotSignalAdapter


class TemporalIntegrationAdapter(SignalAdapterWrapper):
    """Delegates to :class:`~argus.signals.adapters.temporal_snapshots.TemporalSnapshotSignalAdapter`."""

    def __init__(self) -> None:
        super().__init__(TemporalSnapshotSignalAdapter())
        self.adapter_id = "temporal"

    @property
    def category(self) -> AdapterCategory:
        return AdapterCategory.TEMPORAL
