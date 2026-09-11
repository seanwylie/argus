"""Built-in adapters (delegate to :mod:`argus.signals.adapters` implementations)."""

from __future__ import annotations

from argus.adapters.builtins.execution import ExecutionIntegrationAdapter
from argus.adapters.builtins.filesystem import FilesystemIntegrationAdapter
from argus.adapters.builtins.metrics import MetricsIntegrationAdapter
from argus.adapters.builtins.temporal import TemporalIntegrationAdapter

DEFAULT_BUILTIN_ADAPTERS: tuple[type, ...] = (
    ExecutionIntegrationAdapter,
    FilesystemIntegrationAdapter,
    MetricsIntegrationAdapter,
    TemporalIntegrationAdapter,
)

__all__ = [
    "DEFAULT_BUILTIN_ADAPTERS",
    "ExecutionIntegrationAdapter",
    "FilesystemIntegrationAdapter",
    "MetricsIntegrationAdapter",
    "TemporalIntegrationAdapter",
]
