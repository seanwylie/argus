"""Execution feedback: read structured results from ``runs/execution/<product_id>/*.json``."""

from __future__ import annotations

from argus.core.models.enums import SignalType
from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.signals.adapters.execution_outcomes import (
    EXECUTION_ROOT,
    generate_signals_from_execution_outcomes,
)
from argus.signals.contract import ProductSignalContext, SignalAdapter

ADAPTER_ID = "execution"

# Back-compat for imports: ``from argus.signals.adapters.execution import EXECUTION_ROOT``
__all__ = [
    "ADAPTER_ID",
    "EXECUTION_ROOT",
    "ExecutionAdapter",
]


class ExecutionAdapter(SignalAdapter):
    """Loads execution outcome signals via :func:`generate_signals_from_execution_outcomes`."""

    adapter_id = ADAPTER_ID

    def is_enabled_in_product(self, product: ProductNode) -> bool:
        # Always on when artifacts exist: bridge real outcomes into the same pipeline as other signals.
        return True

    @property
    def signal_type(self) -> SignalType:
        return SignalType.EXECUTION

    def describe_capabilities(self) -> str:
        return (
            "Reads `runs/execution/<product_id>/*.json` (subprocess runs, experiments, and "
            "`orchestration_feedback_*.json` from the orchestration step executor). "
            "Emitted as execution-outcome signals (:mod:`argus.signals.adapters.execution_outcomes`)."
        )

    def collect(self, ctx: ProductSignalContext) -> list[SignalRecord]:
        return generate_signals_from_execution_outcomes(ctx.repo_root, ctx.product_id)
