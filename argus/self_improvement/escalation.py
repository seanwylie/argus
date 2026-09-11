"""Hooks for escalation / governance when self-change proposals are risky."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.self_improvement.planning import load_latest_plan_json


def risky_self_improvement_items(repo_root: Path) -> list[dict[str, Any]]:
    """
    Items from the latest self-improvement plan that should not proceed without review.

    Safe to call even when no plan exists (returns empty list).
    """
    raw = load_latest_plan_json(repo_root)
    if not raw:
        return []
    props = {p["id"]: p for p in (raw.get("proposals") or []) if isinstance(p, dict)}
    out: list[dict[str, Any]] = []
    for pid in raw.get("high_risk_require_approval") or []:
        p = props.get(pid)
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "proposal_id": pid,
                "title": p.get("title"),
                "kind": p.get("kind"),
                "risk": p.get("risk"),
                "requires_operator_approval": p.get("requires_operator_approval"),
                "summary": (p.get("summary") or "")[:500],
            }
        )
    return out
