"""Discover candidate product roots under ``products/*``."""

from __future__ import annotations

from pathlib import Path

PRODUCT_FILENAME = "product.yaml"


def discover_product_yaml_files(products_dir: Path) -> list[Path]:
    """
    Return sorted paths to ``product.yaml`` for each direct child of ``products_dir``
    that contains that file.
    """
    if not products_dir.is_dir():
        return []
    found: list[Path] = []
    for child in sorted(products_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest = child / PRODUCT_FILENAME
        if manifest.is_file():
            found.append(manifest)
    return found


def discover_candidate_roots(products_dir: Path) -> list[Path]:
    """Return product root directories (parent of each ``product.yaml``)."""
    return [p.parent for p in discover_product_yaml_files(products_dir)]
