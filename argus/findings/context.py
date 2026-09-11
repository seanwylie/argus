"""Evaluation context for finding rules."""

from __future__ import annotations

from dataclasses import dataclass

from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord


@dataclass(frozen=True)
class RuleContext:
    """Product plus normalized signals from a collection pass."""

    product: ProductNode
    signals: list[SignalRecord]
