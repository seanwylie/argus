"""
Read-only synthesis: relate latest signal instrumentation scans to worker apply artifacts.

Used by portfolio lifecycle, operator queue, dashboards, and autonomy memory to distinguish
\"never instrumented\" pressure from \"worker applied contract; await richer signals / refresh\".
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.products.apply_signal_instrumentation import (
    PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA,
    signal_instrumentation_apply_latest_dir,
)
from argus.products.signal_instrumentation import (
    product_ids_under_instrumentation_pressure,
)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_latest_signal_instrumentation_apply_by_product(repo_root: Path) -> dict[str, dict[str, Any]]:
    """
    Read ``runs/products/signal_instrumentation_apply/latest/<product_id>.json`` for each file present.

    Returns per-product payloads matching :data:`PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA`.
    """
    root = Path(repo_root).resolve()
    ld = signal_instrumentation_apply_latest_dir(root)
    if not ld.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for path in sorted(ld.glob("*.json")):
        raw = _load_json(path)
        if not raw or str(raw.get("schema") or "") != PRODUCT_SIGNAL_INSTRUMENTATION_APPLY_SCHEMA:
            continue
        pid = str(raw.get("product_id") or "").strip()
        if pid:
            out[pid] = raw
    return out


def refine_instrumentation_pressure_with_apply_context(
    *,
    inst_by_product: dict[str, dict[str, Any]],
    apply_by_product: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Derive effective instrumentation pressure lists using latest apply artifacts.

    * ``products_under_instrumentation_pressure_effective`` — weak/sparse/missing per latest
      instrumentation scan, **excluding** products where a **successful** apply recorded
      ``post_apply.instrumentation_status == adequate`` (worker validation snapshot).

    * ``products_instrumentation_resolved_via_apply`` — ids removed from the raw pressure list
      for that reason.

    * ``products_instrumentation_apply_followup`` — still weak per scan **and** latest apply is
      ``partial``, or success without adequate post_apply snapshot (needs richer signals / refresh).
    """
    raw_sorted = product_ids_under_instrumentation_pressure(inst_by_product)
    raw_set = set(raw_sorted)

    resolved: set[str] = set()
    followup: set[str] = set()

    for pid, ap in apply_by_product.items():
        pa = ap.get("post_apply") if isinstance(ap.get("post_apply"), dict) else {}
        post_st = str(pa.get("instrumentation_status") or "").strip().lower()
        ast = str(ap.get("apply_status") or "").strip().lower()

        if ast == "success" and post_st == "adequate":
            if pid in raw_set:
                resolved.add(pid)
            continue

        if pid not in raw_set:
            continue

        if ast == "partial":
            followup.add(pid)
        elif ast == "success" and post_st and post_st != "adequate":
            followup.add(pid)

    effective = sorted(raw_set - resolved)
    return {
        "products_under_instrumentation_pressure_raw": raw_sorted,
        "products_under_instrumentation_pressure_effective": effective,
        "products_instrumentation_resolved_via_apply": sorted(resolved),
        "products_instrumentation_apply_followup": sorted(followup),
        "apply_artifacts_loaded_count": len(apply_by_product),
    }


__all__ = [
    "load_latest_signal_instrumentation_apply_by_product",
    "refine_instrumentation_pressure_with_apply_context",
]
