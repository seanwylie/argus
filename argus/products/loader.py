"""Load ``product.yaml`` into plain dicts for validation and :class:`ProductNode` construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from argus.products.paths import ensure_posix_relative


def load_yaml_file(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """
    Parse YAML from ``path``.

    Returns ``(dict, None)`` on success, or ``(None, error_message)``.
    """
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        return None, f"PyYAML is required to load product manifests: {e}"
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, f"Cannot read {path}: {e}"
    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        return None, f"Invalid YAML in {path}: {e}"
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, f"product.yaml must be a mapping at top level, got {type(data).__name__}"
    # Copy so callers may mutate
    return dict(data), None


def attach_paths(
    data: Mapping[str, Any],
    *,
    repo_root: Path,
    product_root: Path,
    config_path: Path,
) -> dict[str, Any]:
    """
    Merge path metadata expected by :func:`argus.core.serialize.product_node_from_dict`.

    ``product_root`` and ``config_path`` are stored as repo-relative POSIX paths.
    """
    merged: dict[str, Any] = dict(data)
    merged["product_root"] = ensure_posix_relative(repo_root, product_root)
    merged["config_path"] = ensure_posix_relative(repo_root, config_path)
    return merged
