"""
Attention grouping for portfolio Builder activity (operator summary / UI).

Kept dependency-free so :mod:`argus.dashboard.operator_summary` can import constants
without pulling in :mod:`argus.portfolio.builder_activity` (heavy builder imports).
"""

from __future__ import annotations

from typing import Any

# Reason codes → presentation-only groups (same codes in ``reasons``; no new signals).
_SCOPE_SAFETY_REASONS = frozenset(
    {
        "scope_breach",
        "path_scope_breach",
        "semantic_scope_breach",
        "outcome_breached",
        "review_unsafe",
        "trust_flags",
    }
)
_BLOCKED_FAILED_REASONS = frozenset(
    {
        "review_blocked",
        "invoke_failed",
        "outcome_blocked",
    }
)
_NEEDS_REVIEW_REASONS = frozenset(
    {
        "escalation_inbox_visible",
        "reconcile_escalation_emitted",
    }
)

ATTENTION_GROUP_KEYS: tuple[str, ...] = ("scope_safety", "blocked_failed", "needs_review")

ATTENTION_GROUP_TITLES: dict[str, str] = {
    "scope_safety": "Scope / safety",
    "blocked_failed": "Blocked / failed",
    "needs_review": "Needs review",
}


def group_attention_products_by_category(
    attention_products: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """
    Split attention rows into UI groups using existing ``reasons`` codes only.

    A product may appear in more than one group when its reasons span categories.
    Unrecognized reason codes are listed only under **needs_review** (catch-all).
    """
    out: dict[str, list[dict[str, Any]]] = {
        "scope_safety": [],
        "blocked_failed": [],
        "needs_review": [],
    }
    for item in attention_products:
        if not isinstance(item, dict):
            continue
        reasons = list(item.get("reasons") or [])
        rs = set(reasons)
        matched_scope = bool(rs & _SCOPE_SAFETY_REASONS)
        matched_blocked = bool(rs & _BLOCKED_FAILED_REASONS)
        matched_needs = bool(rs & _NEEDS_REVIEW_REASONS)
        if matched_scope:
            out["scope_safety"].append(item)
        if matched_blocked:
            out["blocked_failed"].append(item)
        if matched_needs:
            out["needs_review"].append(item)
        if reasons and not (matched_scope or matched_blocked or matched_needs):
            out["needs_review"].append(item)
    return out
