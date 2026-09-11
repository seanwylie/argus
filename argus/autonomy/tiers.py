"""Explicit autonomy tiers (bounded rollout) mapped to existing ``AutonomyMode`` policy presets."""

from __future__ import annotations

import os
from enum import IntEnum

from argus.autonomy.models import AutonomyMode


class AutonomyTier(IntEnum):
    """
    Rollout tiers — higher means more automation is *permitted* when policy allows.

    Tier 4 is defined for forward compatibility but is **not enabled** for unattended execution
    unless ``ARGUS_ENABLE_TIER4=1`` is set (operator opt-in).
    """

    OBSERVE_ONLY = 0
    SUGGEST_ONLY = 1
    SAFE_EXECUTION = 2
    BOUNDED_EXECUTION = 3
    FULL_AUTONOMY = 4


TIER_LABELS: dict[int, str] = {
    0: "Tier 0 — Observe only: collect signals/findings; no mutating execution",
    1: "Tier 1 — Suggest only: plans and recommendations; execution requires explicit operator runs",
    2: "Tier 2 — Safe local execution: reversible actions within tight policy budgets",
    3: "Tier 3 — Bounded execution: wider automation with approval gates on destructive work",
    4: "Tier 4 — Future / not enabled by default (requires ARGUS_ENABLE_TIER4=1)",
}


def tier_from_mode(mode: AutonomyMode) -> AutonomyTier:
    return {
        AutonomyMode.OFF: AutonomyTier.OBSERVE_ONLY,
        AutonomyMode.MANUAL: AutonomyTier.SUGGEST_ONLY,
        AutonomyMode.SUPERVISED: AutonomyTier.SAFE_EXECUTION,
        AutonomyMode.LIMITED: AutonomyTier.BOUNDED_EXECUTION,
        AutonomyMode.ACTIVE: AutonomyTier.FULL_AUTONOMY,
    }[mode]


def mode_for_tier(tier: AutonomyTier) -> AutonomyMode:
    """Canonical ``AutonomyMode`` written to ``autonomy.json`` for a chosen tier."""
    return {
        AutonomyTier.OBSERVE_ONLY: AutonomyMode.OFF,
        AutonomyTier.SUGGEST_ONLY: AutonomyMode.MANUAL,
        AutonomyTier.SAFE_EXECUTION: AutonomyMode.SUPERVISED,
        AutonomyTier.BOUNDED_EXECUTION: AutonomyMode.LIMITED,
        AutonomyTier.FULL_AUTONOMY: AutonomyMode.ACTIVE,
    }[tier]


def infer_tier(stored: int | None, mode: AutonomyMode) -> AutonomyTier:
    """Resolve tier from config (preferred) or from mode."""
    if stored is not None:
        try:
            v = int(stored)
        except (TypeError, ValueError):
            return tier_from_mode(mode)
        v = max(0, min(4, v))
        return AutonomyTier(v)
    # Legacy ``autonomy.json`` without ``tier``: treat ACTIVE as bounded (3), not full (4).
    if mode == AutonomyMode.ACTIVE:
        return AutonomyTier.BOUNDED_EXECUTION
    return tier_from_mode(mode)


def effective_execution_tier(tier: AutonomyTier) -> AutonomyTier:
    """Tier used for caps and matrices (never above 3 unless tier4 enabled)."""
    if tier == AutonomyTier.FULL_AUTONOMY and os.environ.get("ARGUS_ENABLE_TIER4", "").strip() not in (
        "1",
        "true",
        "yes",
    ):
        return AutonomyTier.BOUNDED_EXECUTION
    return tier


def tier4_enabled() -> bool:
    return os.environ.get("ARGUS_ENABLE_TIER4", "").strip().lower() in ("1", "true", "yes")


def describe_tier(tier: AutonomyTier) -> dict[str, str | bool]:
    eff = effective_execution_tier(tier)
    return {
        "tier": str(int(tier)),
        "label": TIER_LABELS.get(int(tier), ""),
        "effective_tier": str(int(eff)),
        "tier4_opt_in_required": tier == AutonomyTier.FULL_AUTONOMY and not tier4_enabled(),
    }
