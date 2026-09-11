"""Per-UTC-day quotas for spawns and experiment creation (bounded autonomy)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.autonomy.controller import load_state, save_state
from argus.autonomy.operator_policy import effective_policy
from argus.autonomy.tiers import AutonomyTier, effective_execution_tier


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _reset_quota_day(state: dict[str, Any], key_day: str, key_count: str) -> None:
    day = _utc_day()
    if state.get(key_day) != day:
        state[key_day] = day
        state[key_count] = 0


def check_product_spawn_allowed(repo_root: Path) -> tuple[bool, str]:
    """Return (ok, message) before creating a new product scaffold."""
    root = repo_root.resolve()
    _, policy, tier = effective_policy(root)
    eff = effective_execution_tier(tier)
    if eff <= AutonomyTier.OBSERVE_ONLY:
        return False, "autonomy tier 0 (observe) — product spawn is blocked"
    limit = policy.max_product_spawns_per_utc_day
    state = load_state(root)
    _reset_quota_day(state, "spawns_utc_day", "product_spawns_today")
    n = int(state.get("product_spawns_today", 0))
    if n >= limit:
        return (
            False,
            f"product spawn quota exhausted: {n}/{limit} for UTC day (see max_product_spawns_per_utc_day)",
        )
    return True, ""


def record_product_spawn(repo_root: Path) -> None:
    root = repo_root.resolve()
    state = load_state(root)
    _reset_quota_day(state, "spawns_utc_day", "product_spawns_today")
    state["product_spawns_today"] = int(state.get("product_spawns_today", 0)) + 1
    save_state(root, state)


def check_experiment_create_allowed(repo_root: Path) -> tuple[bool, str]:
    root = repo_root.resolve()
    _, policy, tier = effective_policy(root)
    eff = effective_execution_tier(tier)
    if eff <= AutonomyTier.OBSERVE_ONLY:
        return False, "autonomy tier 0 (observe) — experiment creation is blocked"
    limit = policy.max_experiments_per_utc_day
    state = load_state(root)
    _reset_quota_day(state, "experiments_utc_day", "experiments_created_today")
    n = int(state.get("experiments_created_today", 0))
    if n >= limit:
        return (
            False,
            f"experiment create quota exhausted: {n}/{limit} for UTC day (see max_experiments_per_utc_day)",
        )
    return True, ""


def record_experiment_created(repo_root: Path) -> None:
    root = repo_root.resolve()
    state = load_state(root)
    _reset_quota_day(state, "experiments_utc_day", "experiments_created_today")
    state["experiments_created_today"] = int(state.get("experiments_created_today", 0)) + 1
    save_state(root, state)


def check_shutdown_apply_allowed(repo_root: Path) -> tuple[bool, str]:
    """Return (ok, message) before ``autonomy shutdown --apply``."""
    root = repo_root.resolve()
    _, policy, tier = effective_policy(root)
    eff = effective_execution_tier(tier)
    if eff <= AutonomyTier.OBSERVE_ONLY:
        return False, "autonomy tier 0 (observe) — shutdown apply is blocked"
    limit = policy.max_shutdowns_per_utc_day
    state = load_state(root)
    _reset_quota_day(state, "shutdowns_utc_day", "shutdowns_applied_today")
    n = int(state.get("shutdowns_applied_today", 0))
    if n >= limit:
        return (
            False,
            f"shutdown apply quota exhausted: {n}/{limit} for UTC day (see max_shutdowns_per_utc_day)",
        )
    return True, ""


def record_shutdown_applied(repo_root: Path) -> None:
    root = repo_root.resolve()
    state = load_state(root)
    _reset_quota_day(state, "shutdowns_utc_day", "shutdowns_applied_today")
    state["shutdowns_applied_today"] = int(state.get("shutdowns_applied_today", 0)) + 1
    save_state(root, state)
