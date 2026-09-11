"""Human-readable summaries for product inventory."""

from __future__ import annotations

from argus.core.models.product import ProductNode
from argus.products.inventory import ProductInventory


def format_product_summary(node: ProductNode) -> str:
    """Single-block summary for one valid product."""
    lines: list[str] = []
    tid = node.type_info.type if node.type_info else "unknown"
    status = node.type_info.status if node.type_info else "unknown"
    state = node.type_info.state if node.type_info and node.type_info.state else node.lifecycle.stage.value
    lines.append(f"id:          {node.id}")
    lines.append(f"name:        {node.name}")
    lines.append(f"type:        {tid}")
    lines.append(f"state:       {state}")
    lines.append(f"status:      {status}")
    lines.append(f"lifecycle:   {node.lifecycle.stage.value}")
    if node.signal_manifest is not None:
        n = len(node.signal_manifest.signals)
        lines.append(f"signal manifest: {n} entr{'y' if n == 1 else 'ies'} ({node.signal_manifest.schema})")
    enabled = [s.type for s in node.signals if s.enabled]
    disabled = [s.type for s in node.signals if not s.enabled]
    lines.append(f"signals on:  {', '.join(enabled) if enabled else '(none)'}")
    if disabled:
        lines.append(f"signals off: {', '.join(disabled)}")
    actions: list[str] = []
    if node.actions.start:
        actions.append("start")
    if node.actions.stop:
        actions.append("stop")
    if node.actions.analyze:
        actions.append("analyze")
    actions.extend(sorted(node.actions.extra.keys()))
    lines.append(f"actions:     {', '.join(actions) if actions else '(none)'}")
    return "\n".join(lines)


def format_inventory_text(inv: ProductInventory) -> str:
    """Multi-product listing with summary counts."""
    blocks: list[str] = []
    blocks.append(f"Products directory: {inv.products_dir}")
    blocks.append(
        f"Summary: {inv.summary.valid_count} valid, {inv.summary.invalid_count} invalid "
        f"of {inv.summary.total_candidates} candidates"
    )
    if inv.summary.by_lifecycle_stage:
        parts = [f"{k}={v}" for k, v in sorted(inv.summary.by_lifecycle_stage.items())]
        blocks.append("By lifecycle stage: " + ", ".join(parts))
    if inv.summary.by_status:
        parts = [f"{k}={v}" for k, v in sorted(inv.summary.by_status.items())]
        blocks.append("By status: " + ", ".join(parts))
    blocks.append("")

    for pid in sorted(inv.valid.keys()):
        blocks.append(format_product_summary(inv.valid[pid].node))
        w = inv.valid[pid].warnings
        if w:
            blocks.append("warnings:")
            for line in w:
                blocks.append(f"  - {line}")
        blocks.append("")

    for rec in inv.invalid:
        blocks.append(f"[invalid] {rec.config_path}")
        if rec.product_id:
            blocks.append(f"  id: {rec.product_id}")
        for e in rec.errors:
            blocks.append(f"  error: {e}")
        for w in rec.warnings:
            blocks.append(f"  warning: {w}")
        blocks.append("")

    return "\n".join(blocks).rstrip() + "\n"
