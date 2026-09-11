"""Load ``doctrine.yaml`` from a product directory."""

from __future__ import annotations

from pathlib import Path

from argus.doctrine.models import ProductDoctrine
from argus.doctrine.validate import DoctrineValidationError, validate_doctrine_raw
from argus.products.loader import load_yaml_file


def doctrine_yaml_path(product_root: Path) -> Path:
    return product_root.resolve() / "doctrine.yaml"


def load_doctrine_yaml(path: Path) -> tuple[ProductDoctrine | None, str | None]:
    """
    Read and validate ``path``.

    Returns ``(None, None)`` if the file does not exist; ``(None, err)`` on parse/validation error.
    """
    path = path.resolve()
    if not path.is_file():
        return None, None
    raw, err = load_yaml_file(path)
    if err is not None:
        return None, err
    assert raw is not None
    try:
        return validate_doctrine_raw(raw), None
    except DoctrineValidationError as e:
        return None, str(e)


def load_doctrine_for_product_root(product_root: Path) -> tuple[ProductDoctrine | None, str | None]:
    """Load doctrine for a product given its product root directory."""
    return load_doctrine_yaml(doctrine_yaml_path(product_root))


def load_doctrine_for_product(repo_root: Path, product_id: str, *, product_root: str) -> tuple[ProductDoctrine | None, str | None]:
    """Resolve ``product_root`` (posix path from inventory) under ``repo_root`` and load doctrine."""
    root = repo_root.resolve()
    pr = (root / product_root.strip().lstrip("/")).resolve()
    try:
        pr.relative_to(root)
    except ValueError:
        return None, f"invalid product_root for doctrine load: {product_root!r}"
    return load_doctrine_for_product_root(pr)
