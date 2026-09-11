"""Summarize per-product changes across stored portfolio snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from argus.history.models import ProductSnapshot
from argus.history.storage import iter_snapshot_dirs, load_snapshot_file


def load_product_timeline(repo_root: Path, product_id: str) -> list[tuple[str, str, ProductSnapshot]]:
    """
    Return ``(snapshot_id, observed_at_utc, product_row)`` newest-first for ``product_id``.

    Snapshots that omit this product (e.g. not yet in inventory) are skipped.
    """
    pid = product_id.strip()
    rows: list[tuple[str, str, ProductSnapshot]] = []
    for d in iter_snapshot_dirs(repo_root):
        sp = d / "snapshot.json"
        try:
            snap = load_snapshot_file(sp)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for p in snap.products:
            if p.product_id == pid:
                rows.append((snap.snapshot_id, snap.observed_at_utc, p))
                break
    return rows


def format_product_history_text(repo_root: Path, product_id: str) -> str:
    """Human-readable multi-line summary for ``argus history product``."""
    timeline = load_product_timeline(repo_root, product_id)
    if not timeline:
        return (
            f"No history entries for product {product_id!r} under "
            f"{(repo_root / 'runs' / 'history' / 'snapshots').as_posix()}.\n"
            "Run `argus history snapshot` to capture the current portfolio state.\n"
        )

    lines: list[str] = [
        f"Product {product_id!r} — {len(timeline)} snapshot(s) (newest first)",
        "",
        f"{'snapshot_id':<32} {'observed_at_utc':<28} findings cost $  kill?  top action (truncated)",
        "-" * 120,
    ]
    for sid, obs, p in timeline:
        action = (p.top_recommended_action or "").replace("\n", " ")[:56]
        cost = "" if p.monthly_cost_usd is None else f"{p.monthly_cost_usd:.2f}"
        lines.append(
            f"{sid:<32} {obs:<28} {p.active_findings_count:>3} {cost:>8} "
            f"{'Y' if p.kill_candidate else 'N':>4}  {action}"
        )
    lines.append("")
    return "\n".join(lines)
