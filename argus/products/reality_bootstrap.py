"""
Post-scaffold “reality” bootstrap: doctrine template, metrics placeholders, experiment seed.

No external account creation — operators file capability requests for vendors/billing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json

_DOCTRINE_BOOTSTRAP = """schema: argus.doctrine.v1
summary: Bootstrap doctrine — refine with product strategy and constraints.
principles:
  - Ship small, measure, iterate.
  - Keep spend within declared caps unless escalated.
constraints: {}
scoring:
  experiment_score_boost: 0.1
"""


def write_reality_bootstrap(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
    force: bool = False,
) -> tuple[int, str, dict[str, Any]]:
    """
    Create or skip bootstrap artifacts under ``products/<id>/``.

    Returns ``(exit_code, message, detail)``.
    """
    root = repo_root.resolve()
    base = root / "products" if products_dir is None else products_dir.resolve()
    product_root = (base / product_id).resolve()
    if not product_root.is_dir():
        return 2, f"product root not found: {product_root}", {}

    created: list[str] = []
    skipped: list[str] = []

    metrics = product_root / "metrics"
    metrics.mkdir(parents=True, exist_ok=True)
    snap_readme = metrics / "snapshots" / "README.md"
    if not snap_readme.is_file() or force:
        snap_readme.parent.mkdir(parents=True, exist_ok=True)
        snap_readme.write_text(
            "# Snapshot drops\n\n"
            "Place exported analytics JSON/CSV here. Filename hints dispatch parsers — "
            "see `docs/analytics-integrations.md`.\n",
            encoding="utf-8",
        )
        created.append(str(snap_readme.relative_to(root)))
    else:
        skipped.append(str(snap_readme.relative_to(root)))

    placeholders = metrics / "analytics_placeholders.md"
    if not placeholders.is_file() or force:
        placeholders.write_text(
            f"# Analytics placeholders ({product_id})\n\n"
            "- PostHog: schedule export → `posthog_<date>.json` in this tree.\n"
            "- GA4: `ga4_<date>.json` or `google_analytics_*.csv`.\n"
            "- Content / Substack / YouTube: `content_platform_<date>.json` with `platform` field.\n\n"
            "Live API keys and hosted project setup: file a **capability request** "
            "(see `docs/capabilities.md`).\n",
            encoding="utf-8",
        )
        created.append(str(placeholders.relative_to(root)))
    else:
        skipped.append(str(placeholders.relative_to(root)))

    doctrine_path = product_root / "doctrine.yaml"
    if not doctrine_path.is_file() or force:
        doctrine_path.write_text(_DOCTRINE_BOOTSTRAP, encoding="utf-8")
        created.append(str(doctrine_path.relative_to(root)))
    else:
        skipped.append(str(doctrine_path.relative_to(root)))

    experiments_dir = product_root / "experiments"
    experiments_dir.mkdir(parents=True, exist_ok=True)
    seed_path = experiments_dir / "seed.json"
    if not seed_path.is_file() or force:
        seed_payload = {
            "schema": "argus.experiments.seed.v1",
            "product_id": product_id,
            "notes": "Initial experiment ideas — replace via `argus experiments` workflows.",
            "candidates": [
                {
                    "title": "Baseline instrumentation check",
                    "hypothesis": "Snapshots and doctrine are wired before scaling spend.",
                    "kind": "instrumentation",
                },
                {
                    "title": "First acquisition slice",
                    "hypothesis": "One channel shows measurable conversion to core metric.",
                    "kind": "growth",
                },
            ],
        }
        seed_path.write_text(dumps_json(seed_payload), encoding="utf-8")
        created.append(str(seed_path.relative_to(root)))
    else:
        skipped.append(str(seed_path.relative_to(root)))

    detail = {"created": created, "skipped": skipped, "product_root": str(product_root)}
    msg = f"reality bootstrap: {len(created)} wrote, {len(skipped)} skipped"
    return 0, msg, detail
