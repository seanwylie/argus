"""Signals for decision assessment / uncertainty (non-authoritative nudges)."""

from __future__ import annotations

import json
from pathlib import Path

from argus.refinement.persistence import list_session_entries, session_dir


def refinement_uncertainty_nudge(repo_root: Path, product_id: str) -> float:
    """
    Small 0..~0.08 extra uncertainty when active refinement sessions exist for this product.

    Deterministic; does not read LLM output text.
    """
    root = repo_root.resolve()
    entries = list_session_entries(root)
    active = 0
    disagreement_total = 0
    for e in entries:
        if str(e.get("product_id") or "") != product_id:
            continue
        st = str(e.get("status", ""))
        if st in ("approved", "approved_with_risks", "rejected"):
            continue
        active += 1
        sid = str(e.get("session_id", ""))
        udir = session_dir(root, sid) / "outcomes"
        if udir.is_dir():
            for p in sorted(udir.glob("uncertainty_round_*.json")):
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(raw, dict):
                    try:
                        disagreement_total += int(raw.get("stakeholder_disagreement_count", 0))
                    except (TypeError, ValueError):
                        pass
    if active == 0:
        return 0.0
    # Cap contribution
    return min(0.08, 0.02 * active + 0.005 * min(disagreement_total, 12))
