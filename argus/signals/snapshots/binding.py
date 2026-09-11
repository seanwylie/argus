"""Resolve ``product_id`` from snapshot path, file contents, or product.yaml hints."""

from __future__ import annotations

import csv
import io
from pathlib import Path
from typing import Any


class BindingError(ValueError):
    """Explicit product_id in file disagrees with path convention."""


def product_id_from_signals_run_path(snapshot_path: Path, repo_root: Path) -> str | None:
    """
    ``runs/signals/snapshots/products/<product_id>/<file>`` — repo-local snapshot drop.
    """
    try:
        rel = snapshot_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    for i in range(len(parts) - 4):
        if (
            parts[i : i + 4] == ("runs", "signals", "snapshots", "products")
            and i + 4 < len(parts)
        ):
            return parts[i + 4]
    return None


def product_id_from_path(snapshot_path: Path, repo_root: Path) -> str | None:
    """
    Conventions:

    - ``.../products/<product_id>/metrics/snapshots/<file>``
    - ``.../fixtures/business_snapshots/<product_id>/<file>``
    """
    try:
        rel = snapshot_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    for i, p in enumerate(parts):
        if p == "products" and i + 1 < len(parts):
            return parts[i + 1]
        if p == "business_snapshots" and i + 1 < len(parts):
            return parts[i + 1]
    return None


def product_id_from_csv_row(row: dict[str, str]) -> str | None:
    for key in ("product_id", "productId", "product"):
        v = row.get(key)
        if v and str(v).strip():
            return str(v).strip()
    return None


def resolve_product_id(
    snapshot_path: Path,
    repo_root: Path,
    data: dict[str, Any] | None,
    *,
    csv_first_row: dict[str, str] | None = None,
) -> str:
    """
    Prefer explicit ``product_id`` in JSON/CSV; fall back to path convention.

    Raises :class:`BindingError` if both exist and disagree.
    """
    explicit: str | None = None
    if isinstance(data, dict):
        raw = data.get("product_id")
        if raw is not None and str(raw).strip():
            explicit = str(raw).strip()
    if explicit is None and csv_first_row:
        explicit = product_id_from_csv_row(csv_first_row)

    from_path = product_id_from_path(snapshot_path, repo_root)
    if from_path is None:
        from_path = product_id_from_signals_run_path(snapshot_path, repo_root)

    if explicit and from_path and explicit != from_path:
        raise BindingError(
            f"product_id mismatch: file says {explicit!r} but path implies {from_path!r} ({snapshot_path})"
        )
    if explicit:
        return explicit
    if from_path:
        return from_path
    raise BindingError(
        f"Cannot resolve product_id: set product_id in the snapshot or place file under "
        f"products/<id>/metrics/snapshots/ or runs/signals/snapshots/products/<id>/ ({snapshot_path})"
    )


def product_id_from_temporal_path(snapshot_path: Path, repo_root: Path) -> str | None:
    """
    Conventions under repo root:

    - ``runs/temporal/snapshots/global/…`` → ``_global_``
    - ``runs/temporal/snapshots/portfolio/…`` → ``_portfolio_``
    - ``runs/temporal/snapshots/products/<id>/…`` → ``<id>``
    - Same layout allowed under ``fixtures/temporal/snapshots/…``.
    """
    try:
        rel = snapshot_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    parts = rel.parts
    for i, p in enumerate(parts):
        if p != "snapshots":
            continue
        if i == 0 or parts[i - 1] != "temporal":
            continue
        if i + 1 >= len(parts):
            return None
        seg = parts[i + 1]
        if seg == "global":
            return "_global_"
        if seg == "portfolio":
            return "_portfolio_"
        if seg == "products" and i + 2 < len(parts):
            return parts[i + 2]
    return None


def resolve_temporal_product_id(
    snapshot_path: Path,
    repo_root: Path,
    data: dict[str, Any] | None,
    *,
    csv_first_row: dict[str, str] | None = None,
) -> tuple[str, str]:
    """
    Return ``(product_id, scope)`` where scope is ``global`` | ``portfolio`` | ``product``.

    Prefer explicit ``scope`` / ``product_id`` in JSON or CSV; then temporal path
    conventions; then standard ``products/<id>/metrics/snapshots`` paths.
    """
    explicit_pid: str | None = None
    scope_raw: str | None = None
    if isinstance(data, dict):
        raw = data.get("product_id")
        if raw is not None and str(raw).strip():
            explicit_pid = str(raw).strip()
        s = data.get("scope")
        if s is not None and str(s).strip():
            scope_raw = str(s).strip().lower()
    if explicit_pid is None and csv_first_row:
        explicit_pid = product_id_from_csv_row(csv_first_row)
    if scope_raw is None and csv_first_row:
        s = csv_first_row.get("scope") or csv_first_row.get("Scope")
        if s and str(s).strip():
            scope_raw = str(s).strip().lower()

    if scope_raw == "global":
        return "_global_", "global"
    if scope_raw in ("portfolio", "portfolio_wide"):
        return "_portfolio_", "portfolio"
    if scope_raw == "product":
        if not explicit_pid:
            raise BindingError("scope=product requires product_id in temporal snapshot")
        return explicit_pid, "product"

    from_temporal = product_id_from_temporal_path(snapshot_path, repo_root)
    if from_temporal:
        if from_temporal == "_global_":
            return "_global_", "global"
        if from_temporal == "_portfolio_":
            return "_portfolio_", "portfolio"
        return from_temporal, "product"

    from_std = product_id_from_path(snapshot_path, repo_root)
    if explicit_pid and from_std and explicit_pid != from_std:
        raise BindingError(
            f"product_id mismatch: file says {explicit_pid!r} but path implies {from_std!r} ({snapshot_path})"
        )
    if explicit_pid:
        return explicit_pid, "product"
    if from_std:
        return from_std, "product"

    raise BindingError(
        f"Cannot resolve temporal snapshot binding: set scope/product_id or place under "
        f"runs/temporal/snapshots/{{global|portfolio|products/<id>}}/ ({snapshot_path})"
    )


def parse_csv_dicts(text: str) -> list[dict[str, str]]:
    """Parse CSV text into list of row dicts (header row required)."""
    f = io.StringIO(text)
    r = csv.DictReader(f)
    rows: list[dict[str, str]] = []
    for row in r:
        rows.append({k: (v or "").strip() for k, v in row.items() if k})
    return rows
