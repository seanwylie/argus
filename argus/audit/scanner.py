"""Deterministic bounded scan of a product directory."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from argus.core.models.product import ProductNode
from argus.products.inventory import ValidProductRecord

_SKIP_DIR_NAMES = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        "node_modules",
        "dist",
        "build",
        ".egg-info",
    }
)

_MAX_FILES_LISTED = 400
_MAX_DEPTH = 5


def _safe_size(p: Path) -> int:
    try:
        return p.stat().st_size if p.is_file() else 0
    except OSError:
        return 0


def _is_nontrivial_script(p: Path, *, min_bytes: int = 40) -> bool:
    if not p.is_file():
        return False
    n = _safe_size(p)
    if n < min_bytes:
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")[:2000]
    except OSError:
        return n >= min_bytes
    stripped = text.strip()
    if len(stripped) < min_bytes:
        return False
    if stripped in ("#!/bin/sh", "#!/bin/bash", "#!/usr/bin/env bash"):
        return False
    if re.match(r"^#\s*placeholder", stripped, re.I):
        return False
    return True


def bounded_walk(product_root: Path, *, max_files: int = _MAX_FILES_LISTED) -> tuple[list[str], list[str]]:
    """
    Walk declared scope under ``product_root`` (bounded file count).

    Returns (relative posix paths from product_root, exclusion notes).
    """
    scanned: list[str] = []
    excluded: list[str] = []
    roots = default_scope_roots(product_root)
    count = 0
    root_res = product_root.resolve()

    for rel in roots:
        base = (product_root / rel).resolve()
        try:
            if not str(base).startswith(str(root_res)):
                excluded.append(f"skip_outside:{rel}")
                continue
        except Exception:
            continue
        if base.is_file():
            try:
                scanned.append(base.relative_to(product_root).as_posix())
            except ValueError:
                scanned.append(rel)
            count += 1
            if count >= max_files:
                excluded.append(f"cap:max_files={max_files}")
                return scanned, excluded
            continue
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dp = Path(dirpath)
            try:
                rel_depth = len(dp.relative_to(product_root).parts)
            except ValueError:
                continue
            if rel_depth > _MAX_DEPTH:
                dirnames[:] = []
                continue
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIR_NAMES]
            for fn in filenames:
                fp = dp / fn
                try:
                    scanned.append(fp.relative_to(product_root).as_posix())
                except ValueError:
                    continue
                count += 1
                if count >= max_files:
                    excluded.append(f"cap:max_files={max_files}")
                    return scanned, excluded
    return scanned, excluded


def resolve_product_paths(
    repo_root: Path,
    record: ValidProductRecord,
) -> tuple[Path, ProductNode]:
    root = repo_root.resolve()
    node = record.node
    pr = node.product_root
    if not Path(pr).is_absolute():
        pr = root / pr
    return pr.resolve(), node


def default_scope_roots(product_root: Path) -> list[str]:
    """Relative paths to include in quick audit (bounded)."""
    candidates = [
        "product.yaml",
        "doctrine.yaml",
        "scripts",
        "metrics",
        "metrics/snapshots",
        "config",
        "app",
        "src",
    ]
    out: list[str] = []
    for c in candidates:
        p = product_root / c
        if p.exists():
            out.append(c)
    return out if out else ["."]


def fingerprint_inputs(repo_root: Path, product_root: Path, node: ProductNode) -> str:
    """Stable fingerprint for cache invalidation (mtimes + key sizes)."""
    parts: list[str] = []
    cfg = product_root / "product.yaml"
    if cfg.is_file():
        parts.append(f"py:{cfg.stat().st_mtime_ns}:{_safe_size(cfg)}")
    doc = product_root / "doctrine.yaml"
    if doc.is_file():
        parts.append(f"doc:{doc.stat().st_mtime_ns}")
    scripts = product_root / "scripts"
    if scripts.is_dir():
        sm = 0
        n = 0
        for p in sorted(scripts.glob("*")):
            if p.is_file():
                sm += p.stat().st_mtime_ns
                n += 1
        parts.append(f"scripts:{n}:{sm}")
    metrics = product_root / "metrics"
    if metrics.is_dir():
        sm = 0
        n = 0
        for p in metrics.rglob("*"):
            if p.is_file() and n < 200:
                sm += p.stat().st_mtime_ns
                n += 1
        parts.append(f"metrics:{n}:{sm}")
    raw = "|".join(parts) or "empty"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def is_nontrivial_script(path: Path) -> bool:
    """Public for capability map."""
    return _is_nontrivial_script(path)
