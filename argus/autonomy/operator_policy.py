"""Operator-facing autonomy mode and caps (``runs/autonomy/autonomy.json``)."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from argus.autonomy.models import AutonomyMode, AutonomyPolicy
from argus.autonomy.tiers import AutonomyTier, effective_execution_tier, infer_tier
from argus.core.models.enums import ActionType

_ALL_ACTION_TYPES: tuple[str, ...] = tuple(sorted(a.value for a in ActionType))


def _policy_off() -> AutonomyPolicy:
    return AutonomyPolicy(
        max_actions_per_run=0,
        max_cost_per_day=0.0,
        allowed_action_types=(),
        forbidden_action_types=_ALL_ACTION_TYPES,
        require_approval_for=_ALL_ACTION_TYPES,
        auto_execute_types=(),
        escalation_thresholds={"block_streak": 3.0},
    )


def _policy_manual() -> AutonomyPolicy:
    return AutonomyPolicy(
        max_actions_per_run=50,
        max_cost_per_day=2_000.0,
        allowed_action_types=(),
        forbidden_action_types=(),
        require_approval_for=_ALL_ACTION_TYPES,
        auto_execute_types=(),
        escalation_thresholds={"block_streak": 5.0},
    )


def _policy_supervised() -> AutonomyPolicy:
    return AutonomyPolicy(
        max_actions_per_run=20,
        max_cost_per_day=150.0,
        allowed_action_types=(),
        forbidden_action_types=("deprecate", "archive"),
        require_approval_for=("scale", "deprecate", "archive", "stop"),
        auto_execute_types=("analyze", "investigate", "generate", "start"),
        escalation_thresholds={"block_streak": 4.0},
    )


def _policy_limited() -> AutonomyPolicy:
    return AutonomyPolicy(
        max_actions_per_run=200,
        max_cost_per_day=2_000.0,
        allowed_action_types=(),
        forbidden_action_types=(),
        require_approval_for=("deprecate", "archive"),
        auto_execute_types=("analyze", "investigate", "start", "pause", "scale"),
        escalation_thresholds={"block_streak": 8.0},
    )


def _policy_active() -> AutonomyPolicy:
    return AutonomyPolicy(
        max_actions_per_run=2_000,
        max_cost_per_day=50_000.0,
        allowed_action_types=(),
        forbidden_action_types=(),
        require_approval_for=("archive",),
        auto_execute_types=_ALL_ACTION_TYPES,
        escalation_thresholds={"block_streak": 12.0},
    )


DEFAULT_POLICY_BY_MODE: dict[AutonomyMode, AutonomyPolicy] = {
    AutonomyMode.OFF: _policy_off(),
    AutonomyMode.MANUAL: _policy_manual(),
    AutonomyMode.SUPERVISED: _policy_supervised(),
    AutonomyMode.LIMITED: _policy_limited(),
    AutonomyMode.ACTIVE: _policy_active(),
}


def default_policy_for_mode(mode: AutonomyMode) -> AutonomyPolicy:
    return DEFAULT_POLICY_BY_MODE[mode]


def merge_policy(base: AutonomyPolicy, overrides: dict[str, Any] | None) -> AutonomyPolicy:
    if not overrides:
        return base
    data = base.to_jsonable()
    for k, v in overrides.items():
        if k not in data:
            continue
        if k == "escalation_thresholds" and isinstance(v, dict):
            data[k] = {str(x): float(y) for x, y in v.items()}
        elif k in (
            "allowed_action_types",
            "forbidden_action_types",
            "require_approval_for",
            "auto_execute_types",
        ):
            if v is None:
                data[k] = []
            elif isinstance(v, list):
                data[k] = [str(x).strip().lower() for x in v if str(x).strip()]
            else:
                continue
        elif k in ("max_actions_per_run", "max_cost_per_day"):
            data[k] = v
        elif k in ("max_product_spawns_per_utc_day", "max_experiments_per_utc_day", "max_shutdowns_per_utc_day"):
            data[k] = int(v)
        elif k == "min_confidence_autonomous":
            data[k] = float(v)
    return AutonomyPolicy.from_mapping(data)  # type: ignore[arg-type]


def apply_tier_quota_caps(policy: AutonomyPolicy, tier: AutonomyTier) -> AutonomyPolicy:
    """Tighten spawn/experiment/shutdown daily caps to tier ceilings (overrideable lower via policy_overrides)."""
    eff = effective_execution_tier(tier)
    caps: dict[AutonomyTier, tuple[int, int, int]] = {
        AutonomyTier.OBSERVE_ONLY: (0, 0, 0),
        AutonomyTier.SUGGEST_ONLY: (1, 3, 0),
        AutonomyTier.SAFE_EXECUTION: (3, 10, 1),
        AutonomyTier.BOUNDED_EXECUTION: (15, 100, 5),
        AutonomyTier.FULL_AUTONOMY: (50, 500, 20),
    }
    sc, ec, sh = caps[eff]
    return replace(
        policy,
        max_product_spawns_per_utc_day=min(policy.max_product_spawns_per_utc_day, sc),
        max_experiments_per_utc_day=min(policy.max_experiments_per_utc_day, ec),
        max_shutdowns_per_utc_day=min(policy.max_shutdowns_per_utc_day, sh),
    )


def autonomy_config_path(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "autonomy" / "autonomy.json"


def load_autonomy_config(repo_root: Path) -> tuple[AutonomyMode, dict[str, Any] | None, int | None]:
    p = autonomy_config_path(repo_root)
    if not p.is_file():
        return AutonomyMode.ACTIVE, None, None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return AutonomyMode.ACTIVE, None, None
    if not isinstance(raw, dict):
        return AutonomyMode.ACTIVE, None, None
    mode_s = str(raw.get("mode", "active")).strip().lower()
    try:
        mode = AutonomyMode(mode_s)
    except ValueError:
        mode = AutonomyMode.ACTIVE
    overrides = raw.get("policy_overrides")
    if overrides is not None and not isinstance(overrides, dict):
        overrides = None
    tier_raw: int | None = None
    if "tier" in raw and raw["tier"] is not None:
        try:
            tier_raw = int(raw["tier"])
        except (TypeError, ValueError):
            tier_raw = None
    return mode, overrides, tier_raw


def save_autonomy_config(
    repo_root: Path,
    mode: AutonomyMode,
    policy_overrides: dict[str, Any] | None = None,
    *,
    tier: int | None = None,
) -> Path:
    p = autonomy_config_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    existing_tier: int | None = None
    if p.is_file():
        try:
            prev = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(prev, dict) and prev.get("tier") is not None:
                existing_tier = int(prev["tier"])
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    payload: dict[str, Any] = {
        "schema": "argus.autonomy.v1",
        "mode": mode.value,
    }
    t = tier if tier is not None else existing_tier
    if t is not None:
        payload["tier"] = int(t)
    if policy_overrides:
        payload["policy_overrides"] = policy_overrides
    p.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return p


def effective_policy(repo_root: Path) -> tuple[AutonomyMode, AutonomyPolicy, AutonomyTier]:
    mode, ov, tier_raw = load_autonomy_config(repo_root)
    tier = infer_tier(tier_raw, mode)
    base = default_policy_for_mode(mode)
    merged = merge_policy(base, ov)
    merged = apply_tier_quota_caps(merged, tier)
    return mode, merged, tier
