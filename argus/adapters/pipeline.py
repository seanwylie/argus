"""Run the adapter layer for one product (optional merge with classic signal collection)."""

from __future__ import annotations

from pathlib import Path

from argus.adapters.base import Adapter
from argus.adapters.loader import load_adapters_for_repo
from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_signal_record
from argus.signals.runner import build_context


def run_adapter_layer(
    repo_root: Path,
    product: ProductNode,
    *,
    adapters: list[Adapter] | None = None,
) -> list[SignalRecord]:
    """
    Execute every supplied adapter's pipeline (or load from repo config if ``adapters is None``).
    """
    repo_root = repo_root.resolve()
    if adapters is None:
        adapters, _cfg = load_adapters_for_repo(repo_root)
    ctx = build_context(repo_root, product)
    out: list[SignalRecord] = []
    for a in adapters:
        out.extend(a.run_pipeline(ctx))
    for r in out:
        validate_signal_record(r)
    return out


def merge_adapter_layer(
    base_records: list[SignalRecord],
    layer_records: list[SignalRecord],
) -> list[SignalRecord]:
    """Append layer records after base (caller may dedupe by policy)."""
    return list(base_records) + list(layer_records)
