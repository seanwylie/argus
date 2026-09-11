"""Load/save strategy mode and resolve the effective ``StrategyProfile``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.strategy.modes import LEGACY_PROFILE, PROFILE_BY_MODE, StrategyMode, StrategyProfile

STRATEGY_REL_PATH = Path("runs") / "strategy" / "current.json"


def strategy_path(repo_root: Path) -> Path:
    return repo_root.resolve() / STRATEGY_REL_PATH


def get_strategy_profile(repo_root: Path | None) -> StrategyProfile:
    """Effective profile: persisted mode when ``repo_root`` is set and file exists; else legacy."""
    if repo_root is None:
        return LEGACY_PROFILE
    mode = load_strategy_mode(repo_root)
    if mode is None:
        return LEGACY_PROFILE
    return PROFILE_BY_MODE[mode]


def load_strategy_mode(repo_root: Path) -> StrategyMode | None:
    path = strategy_path(repo_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    m = raw.get("mode")
    if not isinstance(m, str):
        return None
    try:
        return StrategyMode(m)
    except ValueError:
        return None


def load_strategy_record(repo_root: Path) -> dict[str, Any] | None:
    """Return full persisted record or None if missing/unreadable."""
    path = strategy_path(repo_root)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def save_strategy_mode(repo_root: Path, mode: StrategyMode) -> Path:
    path = strategy_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "schema": "argus.strategy.v1",
        "mode": mode.value,
        "updated_at_utc": now,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def describe_profile(p: StrategyProfile) -> dict[str, Any]:
    """Stable dict for CLI / debugging."""
    label = "default (no strategy file)" if p.mode is None else p.mode.value
    return {
        "mode": label,
        "weights": {
            "impact": p.w_impact,
            "confidence": p.w_confidence,
            "urgency": p.w_urgency,
            "lifecycle_fit": p.w_lifecycle_fit,
            "cost_penalty": p.w_cost_penalty,
            "effort_penalty": p.w_effort_penalty,
        },
        "cost_penalty_input_scale": p.cost_penalty_input_scale,
        "experiment_score_multiplier": p.experiment_score_multiplier,
        "kill_thresholds": {
            "kill_score_min": p.kill_score_min,
            "move_forward_max": p.move_forward_max,
        },
        "uncertainty_posture": {
            "freshness_stale_confidence_factor": p.freshness_stale_confidence_factor,
            "freshness_missing_confidence_factor": p.freshness_missing_confidence_factor,
            "leap_of_faith_lift": p.leap_of_faith_lift,
            "leap_of_faith_damp": p.leap_of_faith_damp,
        },
    }
