"""Generate Cursor prompts for repo-aware interpretation of orchestration state."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.orchestrator.cursor_review import (
    PROVENANCE_ORCHESTRATION_CURSOR_REVIEW,
    orchestration_cursor_review_contract_prompt_block,
)
from argus.orchestrator.review_ingest import load_orchestration_state_for_review
from argus.orchestrator.state_pass import orchestration_latest_path
from argus.products.inventory import build_inventory


def _bounded_state_snapshot(state: dict[str, Any]) -> str:
    """Compact JSON for prompt: headline fields, eligible actions, blockers (no huge nesting)."""
    elig = state.get("eligible_actions") or []
    slim_elig: list[dict[str, Any]] = []
    if isinstance(elig, list):
        for row in elig[:24]:
            if isinstance(row, dict):
                slim_elig.append(
                    {
                        "action_id": row.get("action_id"),
                        "reason": (str(row.get("reason", ""))[:240]),
                    }
                )
    bl = state.get("blockers") or []
    slim_bl: list[dict[str, Any]] = []
    if isinstance(bl, list):
        for b in bl[:16]:
            if isinstance(b, dict):
                slim_bl.append(
                    {
                        "kind": b.get("kind"),
                        "detail": (str(b.get("detail", ""))[:240]),
                    }
                )
    arts = state.get("artifacts")
    art_keys: list[str] = []
    if isinstance(arts, dict):
        art_keys = sorted(arts.keys())[:20]

    blob = {
        "schema": state.get("schema"),
        "product_id": state.get("product_id"),
        "evaluated_at_utc": state.get("evaluated_at_utc"),
        "orchestration_status": state.get("orchestration_status"),
        "orchestration_status_reason": state.get("orchestration_status_reason"),
        "next_action": state.get("next_action"),
        "escalation_eligible": state.get("escalation_eligible"),
        "artifact_keys": art_keys,
        "eligible_actions": slim_elig,
        "eligible_actions_truncated": isinstance(elig, list) and len(elig) > 24,
        "blockers": slim_bl,
    }
    return json.dumps(blob, indent=2, sort_keys=True)


def build_orchestration_review_prompt(repo_root: Path, product_id: str) -> str:
    """
    Cursor prompt: deterministic orchestration snapshot + JSON contract for orchestration_cursor_review.

    Requires a resolvable product and evaluatable orchestration state (run ``argus orchestration state`` first
    for a persisted snapshot).
    """
    root = repo_root.resolve()
    inv = build_inventory(root)
    if product_id not in inv.valid:
        raise ValueError(f"Unknown product: {product_id!r}")

    node = inv.valid[product_id].node
    state = load_orchestration_state_for_review(root, product_id)
    rel_latest = orchestration_latest_path(root, product_id).relative_to(root)
    rel_review = (root / "runs" / "orchestration" / "review" / f"{product_id}.json").relative_to(root)

    snapshot = _bounded_state_snapshot(state)
    product_rel = Path(node.product_root)
    try:
        pr_rel = product_rel.as_posix()
    except Exception:
        pr_rel = str(node.product_root)

    lines = [
        "# Argus Cursor orchestration review (interpretation layer)",
        "",
        "You review **deterministic Argus orchestration state** (eligible actions, blockers, escalation posture) "
        "and add **repository-grounded** interpretation: weak transition logic, missing prerequisites, fragile "
        "assumptions about progression, or blind spots for likely next steps.",
        "",
        "## Rules",
        "- Use **repository files and paths only**; do not invent production telemetry.",
        "- **Do not** claim your text replaces deterministic orchestration; it is merged separately.",
        "- Cite evidence with **repo-relative paths** under `repo_evidence_refs` / per-finding refs.",
        "",
        "## Identity",
        f"- **product_id**: `{product_id}`",
        f"- **product_name**: {node.name or product_id}",
        f"- **product_root** (relative): `{pr_rel}`",
        f"- **repo_root**: `{root}`",
        "",
        "## Merge and provenance",
        f"- Deterministic state (unchanged by ingest): `{rel_latest}`",
        f"- Cursor layer is stored at `{rel_review}` under key `orchestration_review` "
        f"(via `argus orchestration cursor-ingest`).",
        f'- Set **`provenance`** to exactly `{PROVENANCE_ORCHESTRATION_CURSOR_REVIEW}` on the JSON object.',
        "",
        "## Deterministic baseline — bounded snapshot (from latest evaluation)",
        "Do not contradict structured ids without file-level evidence.",
        "",
        "```json",
        snapshot,
        "```",
        "",
        "## JSON contract (single root object)",
        orchestration_cursor_review_contract_prompt_block(),
    ]
    return "\n".join(lines)
