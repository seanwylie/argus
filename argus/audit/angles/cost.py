"""Cost angle — economics registry vs declared product cost."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from argus.core.models.product import ProductNode
from argus.economics.registry import load_merged_resources


def fingerprint_cost_inputs(repo_root: Path, product_id: str, node: ProductNode) -> str:
    merged = load_merged_resources(repo_root)
    rows: list[str] = []
    for e in sorted(merged.values(), key=lambda x: x.id):
        if e.product_id != product_id:
            continue
        rows.append(f"{e.id}:{e.monthly_cost_usd:.4f}:{e.source}")
    declared = node.cost.monthly_usd
    raw = f"declared={declared}|" + "|".join(rows)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def run_cost_angle(repo_root: Path, product_id: str, node: ProductNode) -> tuple[dict[str, Any], str]:
    fp = fingerprint_cost_inputs(repo_root, product_id, node)
    merged = load_merged_resources(repo_root)
    mapped = [e for e in merged.values() if e.product_id == product_id]
    total_reg = sum(max(0.0, e.monthly_cost_usd) for e in mapped)
    declared = node.cost.monthly_usd

    lines: list[str] = [
        f"cost: resources_mapped={len(mapped)}",
    ]
    if declared is not None:
        lines.append(f"cost: ${declared:.2f}/mo (declared)")
    else:
        lines.append("cost: declared_monthly unknown (product.yaml)")
    lines.append(f"cost: registry_monthly_usd ~{total_reg:.2f}")
    if mapped and declared is not None and total_reg > 0:
        delta = abs(total_reg - float(declared))
        if delta > 0.01:
            lines.append(f"cost: declared_vs_registry delta ~${delta:.2f} (informational)")
    lines = lines[:8]

    payload: dict[str, Any] = {
        "schema": "argus.audit_angle.cost.v1",
        "angle_status": "active",
        "summary_lines": lines,
        "declared_monthly_usd": declared,
        "registry_monthly_usd_rounded": round(total_reg, 4),
        "resource_ids": sorted(e.id for e in mapped)[:40],
    }
    return payload, fp
