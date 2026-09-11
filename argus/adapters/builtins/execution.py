"""Execution outcomes under ``runs/execution/<product_id>/`` (layered API)."""

from __future__ import annotations

from argus.adapters.base import AdapterCategory
from argus.adapters.builtins._wrap import SignalAdapterWrapper
from argus.signals.adapters.execution import ExecutionAdapter


class ExecutionIntegrationAdapter(SignalAdapterWrapper):
    """Delegates to :class:`~argus.signals.adapters.execution.ExecutionAdapter`."""

    def __init__(self) -> None:
        super().__init__(ExecutionAdapter())

    @property
    def category(self) -> AdapterCategory:
        return AdapterCategory.EXECUTION
