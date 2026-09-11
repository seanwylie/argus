"""
Bounded retention for local Argus artifacts under ``runs/``.

Defaults favor **automatic cleanup** after writes so long-lived workspaces do not accumulate
unbounded timestamped copies. Tune with environment variables (see below).

This module does **not** delete ``runs/signals/latest/*.json`` or other canonical “latest” files.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Timestamp-prefixed collection file: 20260102T030405Z_product-id.json
_SIGNAL_COLLECTION_RE = re.compile(r"^(\d{8}T\d{6}Z)_(.+)\.json$")
# Stamped work order JSON only (not latest.json)
_WORK_ORDER_STAMP_RE = re.compile(r"^(\d{8}T\d{6}Z)\.json$")

_ENV_SIGNAL_KEEP = "ARGUS_SIGNALS_COLLECTIONS_KEEP_PER_PRODUCT"
_ENV_WORK_ORDER_KEEP = "ARGUS_BUILDER_WORK_ORDERS_KEEP_STAMPED"

# Reasonable defaults: limit history without deleting “latest” artifacts.
_DEFAULT_SIGNAL_KEEP = 32
_DEFAULT_WORK_ORDER_STAMPED_KEEP = 24


def signal_collections_keep_per_product() -> int:
    """
    Max timestamped files to retain per product under ``runs/signals/collections/``.

    ``0`` means **no limit** (skip pruning). When unset, ``32`` recent files per product are kept.
    """
    raw = os.environ.get(_ENV_SIGNAL_KEEP, "").strip()
    if not raw:
        return _DEFAULT_SIGNAL_KEEP
    try:
        n = int(raw, 10)
    except ValueError:
        return _DEFAULT_SIGNAL_KEEP
    return max(0, n)


def builder_work_orders_stamped_keep() -> int:
    """
    Max stamped ``*.json`` copies (excluding ``latest.json``) per product under
    ``runs/builder/work_orders/<product_id>/``.

    ``0`` = unlimited. Unset uses the module default (see ``_DEFAULT_WORK_ORDER_STAMPED_KEEP``).
    """
    raw = os.environ.get(_ENV_WORK_ORDER_KEEP, "").strip()
    if not raw:
        return _DEFAULT_WORK_ORDER_STAMPED_KEEP
    try:
        n = int(raw, 10)
    except ValueError:
        return _DEFAULT_WORK_ORDER_STAMPED_KEEP
    return max(0, n)


def prune_signal_collections(
    repo_root: Path,
    *,
    keep_per_product: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """
    Remove oldest timestamped collection files beyond the keep limit per product.

    Returns a summary dict with ``deleted_files`` (or ``would_delete_files`` when ``dry_run``),
    ``products_affected``, ``skipped_reason`` (optional).
    """
    root = repo_root.resolve()
    base = root / "runs" / "signals" / "collections"
    keep = signal_collections_keep_per_product() if keep_per_product is None else max(0, keep_per_product)
    if keep <= 0:
        return {"deleted_files": 0, "skipped_reason": "keep_per_product<=0 (unlimited)"}

    if not base.is_dir():
        return {"deleted_files": 0}

    by_product: dict[str, list[tuple[str, Path]]] = {}
    for p in base.iterdir():
        if not p.is_file() or not p.name.endswith(".json"):
            continue
        m = _SIGNAL_COLLECTION_RE.match(p.name)
        if not m:
            continue
        ts, pid = m.group(1), m.group(2)
        by_product.setdefault(pid, []).append((ts, p))

    deleted = 0
    products_touched = 0
    paths_removed: list[str] = []
    for pid, rows in by_product.items():
        rows.sort(key=lambda x: x[0], reverse=True)
        victims = [path for _ts, path in rows[keep:]]
        if not victims:
            continue
        products_touched += 1
        for path in victims:
            try:
                rel = str(path.relative_to(root))
            except ValueError:
                rel = str(path)
            if dry_run:
                deleted += 1
                paths_removed.append(rel)
                continue
            try:
                path.unlink()
                deleted += 1
                paths_removed.append(rel)
            except OSError as e:
                logger.warning("Retention: could not delete %s: %s", path, e)

    out: dict[str, Any] = {
        "products_affected": products_touched,
        "keep_per_product": keep,
        "dry_run": dry_run,
    }
    if dry_run:
        out["would_delete_files"] = deleted
        out["paths"] = paths_removed
    else:
        out["deleted_files"] = deleted
        out["paths"] = paths_removed
    return out


def prune_builder_work_orders_stamped(
    repo_root: Path,
    product_id: str,
    *,
    keep_stamped: int | None = None,
) -> dict[str, Any]:
    """
    Keep only the newest ``keep_stamped`` timestamped JSON files in one product work-orders dir.

    Never deletes ``latest.json`` or ``latest.md``.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    if not pid:
        return {"deleted_files": 0, "skipped_reason": "empty product_id"}

    d = root / "runs" / "builder" / "work_orders" / pid
    keep = builder_work_orders_stamped_keep() if keep_stamped is None else max(0, keep_stamped)
    if keep <= 0:
        return {"deleted_files": 0, "skipped_reason": "keep_stamped<=0 (unlimited)"}

    if not d.is_dir():
        return {"deleted_files": 0}

    stamped: list[tuple[str, Path]] = []
    for p in d.iterdir():
        if not p.is_file():
            continue
        if p.name in ("latest.json", "latest.md"):
            continue
        m = _WORK_ORDER_STAMP_RE.match(p.name)
        if not m:
            continue
        stamped.append((m.group(1), p))

    stamped.sort(key=lambda x: x[0], reverse=True)
    victims = [path for _ts, path in stamped[keep:]]
    deleted = 0
    for path in victims:
        try:
            path.unlink()
            deleted += 1
        except OSError as e:
            logger.warning("Retention: could not delete %s: %s", path, e)

    return {"deleted_files": deleted, "keep_stamped": keep, "product_id": pid}


def maybe_prune_signal_collections_after_save(repo_root: Path) -> None:
    """Best-effort prune after a successful collection write; logs warnings only."""
    try:
        summary = prune_signal_collections(repo_root)
        if summary.get("deleted_files"):
            logger.debug(
                "Signal collection retention: removed %s old file(s) (%s product dir(s))",
                summary["deleted_files"],
                summary.get("products_affected", 0),
            )
    except OSError as e:
        logger.warning("Signal collection retention skipped: %s", e)


def maybe_prune_work_orders_after_write(repo_root: Path, product_id: str) -> None:
    """Best-effort prune stamped work-order JSON after a write."""
    try:
        summary = prune_builder_work_orders_stamped(repo_root, product_id)
        if summary.get("deleted_files"):
            logger.debug(
                "Work order retention: removed %s stamped file(s) for %s",
                summary["deleted_files"],
                product_id,
            )
    except OSError as e:
        logger.warning("Work order retention skipped: %s", e)
