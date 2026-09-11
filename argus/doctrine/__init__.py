"""Product doctrine: optional ``products/<id>/doctrine.yaml`` policy + scoring nudges."""

from __future__ import annotations

from argus.doctrine.load import (
    load_doctrine_for_product,
    load_doctrine_for_product_root,
    load_doctrine_yaml,
)
from argus.doctrine.models import DoctrineConstraints, DoctrineScoring, ProductDoctrine
from argus.doctrine.validate import DoctrineValidationError, validate_doctrine_raw

__all__ = [
    "DoctrineConstraints",
    "DoctrineScoring",
    "DoctrineValidationError",
    "ProductDoctrine",
    "load_doctrine_for_product",
    "load_doctrine_for_product_root",
    "load_doctrine_yaml",
    "validate_doctrine_raw",
]
