"""Build a normalized inventory of product nodes from disk."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from argus.core.models.product import ProductNode
from argus.core.serialize import to_jsonable
from argus.products.discovery import discover_product_yaml_files
from argus.products.loader import load_yaml_file
from argus.products.paths import ensure_posix_relative
from argus.products.validate import validate_manifest


@dataclass
class InvalidProductRecord:
    """A product directory that failed validation."""

    product_id: str | None
    product_root: str
    config_path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class ValidProductRecord:
    """A product that passed manifest validation."""

    node: ProductNode
    warnings: list[str] = field(default_factory=list)


@dataclass
class InventorySummary:
    """Aggregate counts for reporting."""

    total_candidates: int = 0
    valid_count: int = 0
    invalid_count: int = 0
    by_lifecycle_stage: dict[str, int] = field(default_factory=dict)
    by_status: dict[str, int] = field(default_factory=dict)


@dataclass
class ProductInventory:
    """Full scan result: valid nodes, invalid entries, and summary."""

    repo_root: str
    products_dir: str
    valid: dict[str, ValidProductRecord]
    invalid: list[InvalidProductRecord]
    warnings: list[str]
    summary: InventorySummary


def _summarize(valid: dict[str, ValidProductRecord]) -> InventorySummary:
    s = InventorySummary(
        total_candidates=0,
        valid_count=len(valid),
        invalid_count=0,
    )
    for rec in valid.values():
        n = rec.node
        stage = n.lifecycle.stage.value
        s.by_lifecycle_stage[stage] = s.by_lifecycle_stage.get(stage, 0) + 1
        status = "unknown"
        if n.type_info is not None:
            status = n.type_info.status or "unknown"
        s.by_status[status] = s.by_status.get(status, 0) + 1
    return s


def build_inventory(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> ProductInventory:
    """
    Scan ``products/*``, load each ``product.yaml``, validate, and return inventory.

    Duplicate ``id`` values: the first path wins; later duplicates are recorded as
    invalid with a clear error.
    """
    root = repo_root.resolve()
    pdir = (root / "products") if products_dir is None else products_dir.resolve()
    manifests = discover_product_yaml_files(pdir)
    invalid: list[InvalidProductRecord] = []
    valid: dict[str, ValidProductRecord] = {}
    global_warnings: list[str] = []

    for config_path in manifests:
        product_root = config_path.parent
        pr_rel = ensure_posix_relative(root, product_root)
        cfg_rel = ensure_posix_relative(root, config_path)

        raw, err = load_yaml_file(config_path)
        if err is not None:
            invalid.append(
                InvalidProductRecord(
                    product_id=None,
                    product_root=pr_rel,
                    config_path=cfg_rel,
                    errors=[err],
                )
            )
            continue
        assert raw is not None

        pid = raw.get("id")
        pid_s = str(pid).strip() if pid is not None else ""

        result = validate_manifest(
            raw,
            repo_root=root,
            product_root=product_root,
            config_path=config_path,
        )

        if result.errors or result.node is None:
            invalid.append(
                InvalidProductRecord(
                    product_id=pid_s or None,
                    product_root=pr_rel,
                    config_path=cfg_rel,
                    errors=list(result.errors),
                    warnings=list(result.warnings),
                )
            )
            continue

        node = result.node
        if node.id in valid:
            first = valid[node.id].node.config_path
            invalid.append(
                InvalidProductRecord(
                    product_id=node.id,
                    product_root=pr_rel,
                    config_path=cfg_rel,
                    errors=[
                        f"duplicate product id {node.id!r}: already loaded from {first}"
                    ],
                    warnings=list(result.warnings),
                )
            )
            continue

        valid[node.id] = ValidProductRecord(node=node, warnings=list(result.warnings))

    summary = _summarize(valid)
    summary.total_candidates = len(manifests)
    summary.invalid_count = len(invalid)
    summary.valid_count = len(valid)

    return ProductInventory(
        repo_root=str(root),
        products_dir=ensure_posix_relative(root, pdir),
        valid=valid,
        invalid=invalid,
        warnings=global_warnings,
        summary=summary,
    )


def inventory_to_jsonable(inv: ProductInventory) -> dict[str, object]:
    """Convert inventory to JSON-safe dicts (for CLI / snapshot files)."""
    return {
        "repo_root": inv.repo_root,
        "products_dir": inv.products_dir,
        "valid": {
            k: {
                "node": to_jsonable(v.node),
                "warnings": v.warnings,
            }
            for k, v in inv.valid.items()
        },
        "invalid": [
            {
                "product_id": x.product_id,
                "product_root": x.product_root,
                "config_path": x.config_path,
                "errors": x.errors,
                "warnings": x.warnings,
            }
            for x in inv.invalid
        ],
        "warnings": inv.warnings,
        "summary": to_jsonable(inv.summary),
    }
