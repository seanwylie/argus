"""
Product discovery and inventory: scan ``products/*/product.yaml``, validate, normalize.

This package is intentionally separate from :mod:`argus.signals` (runtime signal collection).
"""

from argus.products.discovery import discover_candidate_roots, discover_product_yaml_files
from argus.products.inventory import (
    InvalidProductRecord,
    ProductInventory,
    ValidProductRecord,
    build_inventory,
    inventory_to_jsonable,
)
from argus.products.loader import attach_paths, load_yaml_file
from argus.products.reporting import format_inventory_text, format_product_summary
from argus.products.signal_instrumentation import (
    INSTRUMENTATION_PRESSURE_STATUSES,
    PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA,
    evaluate_product_signal_instrumentation,
    load_latest_signal_instrumentation_by_product,
    product_ids_under_instrumentation_pressure,
    run_product_signal_instrumentation,
    signal_instrumentation_latest_dir,
)
from argus.products.signal_manifest import (
    PRODUCT_SIGNAL_MANIFEST_SCHEMA,
    enabled_manifest_entries,
    load_product_signal_manifest,
    validate_signal_manifest_dict,
)
from argus.products.validate import (
    KNOWN_OPTIONAL_ROOT_KEYS,
    ManifestValidationResult,
    validate_manifest,
)

__all__ = [
    "InvalidProductRecord",
    "ManifestValidationResult",
    "ProductInventory",
    "ValidProductRecord",
    "attach_paths",
    "build_inventory",
    "PRODUCT_SIGNAL_MANIFEST_SCHEMA",
    "discover_candidate_roots",
    "discover_product_yaml_files",
    "enabled_manifest_entries",
    "format_inventory_text",
    "format_product_summary",
    "inventory_to_jsonable",
    "KNOWN_OPTIONAL_ROOT_KEYS",
    "load_product_signal_manifest",
    "load_yaml_file",
    "validate_manifest",
    "validate_signal_manifest_dict",
    "INSTRUMENTATION_PRESSURE_STATUSES",
    "PRODUCT_SIGNAL_INSTRUMENTATION_SCHEMA",
    "evaluate_product_signal_instrumentation",
    "load_latest_signal_instrumentation_by_product",
    "product_ids_under_instrumentation_pressure",
    "run_product_signal_instrumentation",
    "signal_instrumentation_latest_dir",
]
