"""Generate Cursor prompts for repo-aware interpretation of collected signals."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.products.inventory import build_inventory
from argus.signals.cursor_review import (
    PROVENANCE_SIGNAL_CURSOR_REVIEW,
    signal_cursor_review_contract_prompt_block,
)
from argus.signals.persistence import latest_path
from argus.signals.review_ingest import load_deterministic_bundle_dict


def _bounded_bundle_snapshot(det: dict[str, Any], *, max_records: int = 12) -> str:
    """Compact JSON for prompt: schema, counts, bounded record stubs (no full payload dumps)."""
    recs = det.get("records")
    if not isinstance(recs, list):
        recs = []
    slim: list[dict[str, Any]] = []
    for item in recs[:max_records]:
        if not isinstance(item, dict):
            continue
        p = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        p_keys = sorted(p.keys())[:12]
        slim.append(
            {
                "id": item.get("id"),
                "signal_type": item.get("signal_type"),
                "source": item.get("source"),
                "payload_keys": p_keys,
            },
        )
    blob = {
        "schema": det.get("schema"),
        "product_id": det.get("product_id"),
        "collected_at_utc": det.get("collected_at_utc"),
        "record_count": len(recs),
        "records_excerpt": slim,
        "truncated": len(recs) > max_records,
    }
    return json.dumps(blob, indent=2, sort_keys=True)


def build_signal_review_prompt(repo_root: Path, product_id: str) -> str:
    """
    Cursor prompt: deterministic signal bundle snapshot + JSON contract for ``signal_cursor_review``.

    Requires ``runs/signals/latest/<product_id>.json`` from a prior collect.
    """
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown product: {product_id!r}")

    node = inv.valid[product_id].node
    det = load_deterministic_bundle_dict(root, product_id)
    rel_latest = latest_path(root, product_id).relative_to(root)
    rel_review = (root / "runs" / "signals" / "review" / f"{product_id}.json").relative_to(root)

    snapshot = _bounded_bundle_snapshot(det)
    product_rel = Path(node.product_root)
    try:
        pr_rel = product_rel.as_posix()
    except Exception:
        pr_rel = str(node.product_root)

    lines = [
        "# Argus Cursor signal review (interpretation layer)",
        "",
        "You review **collected, deterministic Argus signals** and add **repository-grounded** "
        "interpretation: signal quality, missing instrumentation, weak definitions, or likely "
        "collection blind spots.",
        "",
        "## Rules",
        "- Use **repository files and paths only**; do not invent production telemetry.",
        "- **Do not** claim your text replaces deterministic collection; it is merged separately.",
        "- Cite evidence with **repo-relative paths** under `repo_evidence_refs` / per-finding refs.",
        "",
        "## Identity",
        f"- **product_id**: `{product_id}`",
        f"- **product_name**: {node.name or product_id}",
        f"- **product_root** (relative): `{pr_rel}`",
        f"- **repo_root**: `{root}`",
        "",
        "## Merge and provenance",
        f"- Deterministic bundle (unchanged): `{rel_latest}`",
        f"- Cursor layer is stored at `{rel_review}` under key `signal_review` (via `argus signals cursor-ingest`).",
        f'- Set **`provenance`** to exactly `{PROVENANCE_SIGNAL_CURSOR_REVIEW}` on the JSON object.',
        "",
        "## Deterministic baseline — bounded snapshot (ground truth from last collect)",
        "Do not contradict structured ids/types without file-level evidence.",
        "",
        "```json",
        snapshot,
        "```",
        "",
        "## JSON contract (single root object)",
        signal_cursor_review_contract_prompt_block(),
    ]
    return "\n".join(lines)
