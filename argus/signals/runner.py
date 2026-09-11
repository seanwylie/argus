"""Run enabled adapters for products and validate emitted records."""

from __future__ import annotations

from pathlib import Path

from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_signal_record
from argus.products.inventory import ProductInventory, build_inventory
from argus.signals.adapters.execution_outcomes import dedupe_execution_outcome_signals
from argus.signals.contract import ProductSignalContext
from argus.signals.manifest_collect import reconcile_manifest_declarations
from argus.signals.registry import AdapterRegistry


def product_root_path(repo_root: Path, product: ProductNode) -> Path:
    """Absolute path to the product directory."""
    return (repo_root / product.product_root).resolve()


def build_context(repo_root: Path, product: ProductNode) -> ProductSignalContext:
    return ProductSignalContext(
        repo_root=repo_root.resolve(),
        product=product,
        product_root=product_root_path(repo_root, product),
    )


def collect_for_product(
    repo_root: Path,
    product: ProductNode,
    registry: AdapterRegistry,
    *,
    merge_adapter_layer: bool = False,
) -> list[SignalRecord]:
    """
    Invoke all adapters enabled for ``product`` and validate each record.

    If ``merge_adapter_layer`` is True, append records from the :mod:`argus.adapters`
    pipeline (see ``config/adapters.json`` / :func:`argus.adapters.pipeline.run_adapter_layer`).
    Layer records are tagged ``adapter_layer``; classic and layer may overlap for the
    same source — use for diagnostics or gradual migration.
    """
    ctx = build_context(repo_root, product)
    out: list[SignalRecord] = []
    for adapter in registry.enabled_for_product(product):
        out.extend(adapter.collect(ctx))
    if merge_adapter_layer:
        from argus.adapters.pipeline import run_adapter_layer

        out.extend(run_adapter_layer(repo_root, product))
    out = reconcile_manifest_declarations(out, product, ctx.product_root, registry)
    out = dedupe_execution_outcome_signals(out)
    for r in out:
        validate_signal_record(r)
    return out


def collect_inventory(
    repo_root: Path,
    registry: AdapterRegistry,
    *,
    products_dir: Path | None = None,
    merge_adapter_layer: bool = False,
) -> tuple[ProductInventory, dict[str, list[SignalRecord]]]:
    """
    Load inventory, collect for every **valid** product, return inventory + signals map.

    Skips invalid inventory entries (same as no product node).
    """
    inv = build_inventory(repo_root, products_dir=products_dir)
    by_id: dict[str, list[SignalRecord]] = {}
    for pid, rec in inv.valid.items():
        by_id[pid] = collect_for_product(
            repo_root,
            rec.node,
            registry,
            merge_adapter_layer=merge_adapter_layer,
        )
    return inv, by_id
