"""Built-in signal adapters (filesystem, local files, activity)."""

from __future__ import annotations

from argus.signals.adapters.analytics_file import AnalyticsFileAdapter
from argus.signals.adapters.cost_file import CostFileAdapter
from argus.signals.adapters.execution import ExecutionAdapter
from argus.signals.adapters.execution_outcomes import (
    dedupe_execution_outcome_signals,
    execution_outcome_signal_identity,
    generate_signals_from_execution_outcomes,
)
from argus.signals.adapters.filesystem import FilesystemAdapter
from argus.signals.adapters.heartbeat import HeartbeatAdapter
from argus.signals.adapters.local_snapshots import LocalSnapshotAdapter
from argus.signals.adapters.metrics_file import MetricsFileAdapter
from argus.signals.adapters.temporal_snapshots import TemporalSnapshotSignalAdapter
from argus.signals.contract import SignalAdapter


def default_builtin_adapters() -> list[SignalAdapter]:
    return [
        FilesystemAdapter(),
        MetricsFileAdapter(),
        CostFileAdapter(),
        AnalyticsFileAdapter(),
        HeartbeatAdapter(),
        ExecutionAdapter(),
        TemporalSnapshotSignalAdapter(),
        LocalSnapshotAdapter(),
    ]

__all__ = [
    "AnalyticsFileAdapter",
    "CostFileAdapter",
    "ExecutionAdapter",
    "dedupe_execution_outcome_signals",
    "execution_outcome_signal_identity",
    "generate_signals_from_execution_outcomes",
    "FilesystemAdapter",
    "HeartbeatAdapter",
    "LocalSnapshotAdapter",
    "MetricsFileAdapter",
    "TemporalSnapshotSignalAdapter",
    "default_builtin_adapters",
]
