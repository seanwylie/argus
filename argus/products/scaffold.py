"""
Deterministic product node scaffolding (directories, product.yaml, stub scripts).

No network access; no language-specific app code — layout and metadata only.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from argus.products.git_lifecycle import init_argus_product_git
from argus.products.loader import attach_paths, load_yaml_file
from argus.products.validate import validate_manifest
from argus.project_permissions.load import write_default_policy_file

# Future: optional default `mission_id` in generated `product.yaml` may use
# `argus.mission.mission.resolve_creation_mission(repo_root)` (creation bias — not operational policy).

# Supported template keys (CLI --type); values drive metrics and lifecycle hints.
TEMPLATE_TYPES: frozenset[str] = frozenset(
    {
        "content_stream",
        "micro_saas",
        "static_site",
        "utility_api",
        "mobile_companion",
    }
)

_DEFAULT_TYPE = "content_stream"

# Template-specific defaults: primary metric names, gates, and light constraint hints.
_TEMPLATE_SPECS: dict[str, dict[str, Any]] = {
    "content_stream": {
        "metrics_primary": ["clips_per_day", "views"],
        "next_gate": "establish a repeatable publishing loop and baseline metrics",
        "max_monthly_cost_usd": 25.0,
        "min_activity_threshold": 1.0,
        "cost_monthly_usd": 5.0,
        "cost_notes": "Placeholder estimate for hosting and tooling",
    },
    "micro_saas": {
        "metrics_primary": ["signups", "activation_rate", "mrr"],
        "next_gate": "validate activation and first revenue or strong proxy metric",
        "max_monthly_cost_usd": 150.0,
        "min_activity_threshold": 2.0,
        "cost_monthly_usd": 25.0,
        "cost_notes": "Placeholder estimate (infra + third-party services)",
    },
    "static_site": {
        "metrics_primary": ["pageviews", "ctr", "affiliate_revenue"],
        "next_gate": "ship MVP pages and confirm traffic + conversion instrumentation",
        "max_monthly_cost_usd": 40.0,
        "min_activity_threshold": 1.0,
        "cost_monthly_usd": 8.0,
        "cost_notes": "Placeholder estimate (hosting, CDN, domain)",
    },
    "utility_api": {
        "metrics_primary": ["requests_per_day", "error_rate", "monthly_cost_usd"],
        "next_gate": "define SLOs, error budgets, and monthly cost envelope",
        "max_monthly_cost_usd": 200.0,
        "min_activity_threshold": 10.0,
        "cost_monthly_usd": 35.0,
        "cost_notes": "Placeholder estimate (compute + egress)",
    },
    "mobile_companion": {
        "metrics_primary": ["dau", "retention_d7", "conversion_rate"],
        "next_gate": "instrument core funnel and first retention cohort",
        "max_monthly_cost_usd": 120.0,
        "min_activity_threshold": 3.0,
        "cost_monthly_usd": 20.0,
        "cost_notes": "Placeholder estimate (build pipeline, store fees, backend)",
    },
}


def normalize_product_slug(raw: str) -> str:
    """
    Turn a user-provided name into a filesystem-safe product id / directory name.

    Lowercase, ``[a-z0-9-]``, single hyphens, no leading/trailing hyphen.
    """
    s = raw.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    if not s:
        raise ValueError("invalid name: use letters, numbers, spaces, or hyphens")
    if not re.match(r"^[a-z0-9][a-z0-9-]*[a-z0-9]$|^[a-z0-9]$", s):
        raise ValueError(
            "invalid name after normalization: use a non-empty alphanumeric slug (e.g. my-app)"
        )
    return s


def display_name_from_slug(slug: str) -> str:
    """Human-readable product title for ``name:`` and README (deterministic)."""
    parts = re.split(r"[-_]+", slug)
    return " ".join(p.capitalize() for p in parts if p)


def _signals_block() -> list[dict[str, Any]]:
    return [
        {"type": "filesystem", "enabled": True},
        {"type": "metrics", "enabled": True},
        {"type": "analytics", "enabled": True},
        {"type": "cost", "enabled": True},
        {"type": "health", "enabled": True},
        {"type": "execution", "enabled": True},
        {"type": "custom", "enabled": True},
    ]


def build_product_yaml_payload(
    *,
    product_id: str,
    display_name: str,
    template_type: str,
) -> dict[str, Any]:
    """Build the manifest dict (before ``product_root`` / ``config_path``)."""
    spec = _TEMPLATE_SPECS[template_type]
    stage = "build"
    return {
        "id": product_id,
        "name": display_name,
        "type": template_type,
        "status": "experimental",
        "state": stage,
        "owner": {
            "team": "argus",
            "operator": "",
        },
        "metrics": {
            "local_paths": ["metrics/"],
            "primary": list(spec["metrics_primary"]),
        },
        "cost": {
            "monthly_usd": spec["cost_monthly_usd"],
            "notes": spec["cost_notes"],
        },
        "signals": _signals_block(),
        "actions": {
            "start": "./scripts/start.sh",
            "stop": "./scripts/stop.sh",
            "analyze": "./scripts/analyze.sh",
        },
        "constraints": {
            "max_monthly_cost_usd": spec["max_monthly_cost_usd"],
            "min_activity_threshold": spec["min_activity_threshold"],
        },
        "lifecycle": {
            "stage": stage,
            "next_gate": spec["next_gate"],
        },
    }


def _dump_yaml(data: dict[str, Any]) -> str:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        raise ImportError("YAML support requires the 'pyyaml' package.") from e
    return yaml.safe_dump(
        data,
        sort_keys=True,
        default_flow_style=False,
        allow_unicode=True,
    )


def _script_stub(action: str, product_id: str) -> str:
    # ARGUS-STUB:intentional — operators replace scaffold scripts (docs/stub-inventory.md)
    return (
        f"#!/usr/bin/env sh\n"
        f"# Argus scaffold: {action} hook for {product_id}.\n"
        f"# Local-safe: no network. External calls require capability approval (docs/execution.md).\n"
        f"set -eu\n"
        f": \"${{ARGUS_PRODUCT_ID:={product_id}}}\"\n"
        f"exit 0\n"
    )


def _readme_text(*, product_id: str, display_name: str, template_type: str) -> str:
    return f"""# {display_name}

This directory is an **Argus product node**: a bounded unit in the monorepo that Argus can observe
(metrics, signals, cost) and reason about (findings, decisions).

## Layout

| Path | Role |
|------|------|
| `app/` | Application source for this product (language-agnostic; not generated beyond this tree). |
| `scripts/` | Operator hooks referenced from `product.yaml` (`start`, `stop`, `analyze`). |
| `config/` | Product-specific configuration files Argus or adapters may read. |
| `metrics/` | Files or exports referenced by `metrics.local_paths` in `product.yaml`. |
| `product.yaml` | **Canonical manifest** Argus loads for inventory, signals, and lifecycle. |
| `argus.policy.yaml` | **Phase 1 permissions** (yes / no / confirm) — what Argus may attempt for this product. |

## Template

Scaffold type: **`{template_type}`** (see `type:` in `product.yaml`).

## Argus

- Metadata and signals: read from **`product.yaml`** at the repo root of this node.
- After editing the manifest, run `argus products validate` from the monorepo root.

Product id: `{product_id}`
"""


def _touch_gitkeep(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Placeholder so empty directories are tracked by git.\n",
        encoding="utf-8",
    )


def create_product_scaffold(
    repo_root: Path,
    raw_name: str,
    *,
    template_type: str = _DEFAULT_TYPE,
    products_dir: Path | None = None,
    force: bool = False,
    init_git: bool = True,
) -> tuple[int, str, dict[str, Any]]:
    """
    Create ``products/<slug>/`` with layout, ``product.yaml``, scripts, and README.

    When ``init_git`` is True (default), best-effort ``git init`` + initial commit under the
    product directory (no remote).

    Returns ``(exit_code, message, git_info)``. On failure ``git_info`` is ``{}``.
    On success ``git_info`` contains :func:`init_argus_product_git` fields.
    """
    root = repo_root.resolve()
    pdir = (root / "products") if products_dir is None else products_dir.resolve()

    if template_type not in TEMPLATE_TYPES:
        return (
            2,
            f"unknown template type {template_type!r}; expected one of: {sorted(TEMPLATE_TYPES)}",
            {},
        )

    try:
        slug = normalize_product_slug(raw_name)
    except ValueError as e:
        return 2, str(e), {}

    product_root = pdir / slug
    config_path = product_root / "product.yaml"

    if product_root.exists():
        if not force:
            return (
                1,
                f"refusing to create {product_root}: already exists (use --force to replace)",
                {},
            )
        shutil.rmtree(product_root)

    display_name = display_name_from_slug(slug)
    payload = build_product_yaml_payload(
        product_id=slug,
        display_name=display_name,
        template_type=template_type,
    )

    # Directories
    (product_root / "app").mkdir(parents=True)
    (product_root / "config").mkdir(parents=True)
    (product_root / "metrics").mkdir(parents=True)
    (product_root / "scripts").mkdir(parents=True)

    _touch_gitkeep(product_root / "app" / ".gitkeep")
    _touch_gitkeep(product_root / "config" / ".gitkeep")

    yaml_text = _dump_yaml(payload)
    config_path.write_text(yaml_text, encoding="utf-8")
    write_default_policy_file(product_root)

    for action in ("start", "stop", "analyze"):
        spath = product_root / "scripts" / f"{action}.sh"
        spath.write_text(_script_stub(action, slug), encoding="utf-8")
        try:
            spath.chmod(0o755)
        except OSError:
            pass

    readme = _readme_text(
        product_id=slug,
        display_name=display_name,
        template_type=template_type,
    )
    (product_root / "README.md").write_text(readme, encoding="utf-8")

    # Validate generated manifest
    raw, err = load_yaml_file(config_path)
    if err is not None or raw is None:
        shutil.rmtree(product_root)
        return 2, f"internal error: wrote invalid YAML: {err}", {}

    merged = attach_paths(raw, repo_root=root, product_root=product_root, config_path=config_path)
    result = validate_manifest(merged, repo_root=root, product_root=product_root, config_path=config_path)
    if result.errors:
        shutil.rmtree(product_root)
        return 2, "generated product failed validation: " + "; ".join(result.errors), {}

    git_info: dict[str, Any] = {}
    if init_git:
        git_info = init_argus_product_git(product_root)

    return 0, str(product_root), git_info


__all__ = [
    "TEMPLATE_TYPES",
    "build_product_yaml_payload",
    "create_product_scaffold",
    "display_name_from_slug",
    "normalize_product_slug",
]
