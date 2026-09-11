"""CLI: ``argus strategy`` — set and show operating mode."""

from __future__ import annotations

import json
import sys
from typing import Any

from argus.cli.repo import repo_root as resolve_repo_root
from argus.strategy.apply import (
    describe_profile,
    get_strategy_profile,
    load_strategy_record,
    save_strategy_mode,
)
from argus.strategy.modes import StrategyMode


def run_strategy_command(args: Any) -> int:
    repo_root = resolve_repo_root()
    sub = args.strategy_command
    if sub == "set":
        mode = StrategyMode(str(args.mode))
        path = save_strategy_mode(repo_root, mode)
        print(f"Strategy set to {mode.value} ({path.relative_to(repo_root.resolve())})")
        return 0

    if sub == "show":
        rec = load_strategy_record(repo_root)
        profile = get_strategy_profile(repo_root)
        detail = describe_profile(profile)
        if getattr(args, "json", False):
            out: dict[str, Any] = {"effective": detail}
            if rec is not None:
                out["persisted"] = rec
            print(json.dumps(out, indent=2))
            return 0
        print(f"Effective strategy: {detail['mode']}")
        print("Priority weights:", detail["weights"])
        print(f"Cost penalty input scale: {detail['cost_penalty_input_scale']}")
        print(f"Experiment score multiplier: {detail['experiment_score_multiplier']}")
        print(
            "Kill thresholds: kill>="
            f"{detail['kill_thresholds']['kill_score_min']}, "
            f"move_forward<={detail['kill_thresholds']['move_forward_max']}"
        )
        if rec:
            print(f"Persisted file: updated_at_utc={rec.get('updated_at_utc')}")
        else:
            print("No runs/strategy/current.json — using built-in default weights.")
        return 0

    print("Unknown strategy subcommand.", file=sys.stderr)
    return 2
