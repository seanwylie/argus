"""CLI: ``argus planning weekly`` and ``argus planning actions``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.planning.models import PlanningActionsBundle
from argus.planning.plan_actions import build_planning_actions
from argus.planning.render import plan_to_json, render_markdown, render_terminal_summary
from argus.planning.weekly import build_weekly_plan


def _by_ids(bundle: PlanningActionsBundle, ids: list[str]) -> list:
    amap = {a.action_id: a for a in bundle.actions}
    return [amap[i] for i in ids if i in amap]


def _format_actions_text(bundle: PlanningActionsBundle) -> str:
    lines = [
        "=== Argus planning actions (ActionContracts) ===",
        f"generated: {bundle.generated_at_utc}",
        f"weekly plan snapshot: {bundle.weekly_plan_at_utc}",
        f"window: {bundle.planning_window}",
        "",
        "Priority-driven:",
    ]
    for a in _by_ids(bundle, bundle.priority_action_ids):
        lines.append(f"  [{a.action_id}]  product={a.product_id}")
        lines.append(f"    command: {a.command}")
        lines.append(f"    expected_outcome: {a.expected_outcome}")
        lines.append(f"    rollback_notes: {a.rollback_notes}")
        lines.append("")
    lines.extend(["Experiments:", ""])
    for a in _by_ids(bundle, bundle.experiment_action_ids):
        lines.append(f"  [{a.action_id}]  product={a.product_id}")
        lines.append(f"    command: {a.command}")
        lines.append(f"    expected_outcome: {a.expected_outcome}")
        lines.append("")
    lines.extend(["Decision follow-ups:", ""])
    for a in _by_ids(bundle, bundle.decision_action_ids):
        lines.append(f"  [{a.action_id}]  product={a.product_id}")
        lines.append(f"    command: {a.command}")
        lines.append(f"    expected_outcome: {a.expected_outcome}")
        lines.append("")
    lines.append(f"Total actions: {len(bundle.actions)}")
    lines.append("")
    return "\n".join(lines)


def run_planning_command(args: Any) -> int:
    sub = getattr(args, "planning_command", None)
    if sub == "actions":
        return _run_planning_actions(args)
    if sub != "weekly":
        print("Unknown planning subcommand.", file=sys.stderr)
        return 2

    r = repo_root()
    pdir = getattr(args, "products_dir", None)
    products_dir = pdir.resolve() if pdir is not None else None
    plan = build_weekly_plan(r, products_dir=products_dir)

    base = r / "runs" / "planning"
    base.mkdir(parents=True, exist_ok=True)
    json_path = base / "weekly.json"

    out_arg = getattr(args, "out", None)
    md_path: Path
    if out_arg is None:
        md_path = base / "weekly.md"
    else:
        md_path = out_arg.resolve()
        if md_path.is_dir():
            md_path = md_path / "weekly.md"
    md_path.parent.mkdir(parents=True, exist_ok=True)

    json_path.write_text(plan_to_json(plan), encoding="utf-8")
    md_path.write_text(render_markdown(plan), encoding="utf-8")

    if args.json:
        print(plan_to_json(plan))
    else:
        print(render_terminal_summary(plan), end="")
        print(f"Wrote {json_path.relative_to(r)}", file=sys.stderr)
        print(f"Wrote {md_path.relative_to(r)}", file=sys.stderr)
    return 0


def _run_planning_actions(args: Any) -> int:
    r = repo_root()
    pdir = getattr(args, "products_dir", None)
    products_dir = pdir.resolve() if pdir is not None else None
    bundle = build_planning_actions(r, products_dir=products_dir)

    base = r / "runs" / "planning"
    base.mkdir(parents=True, exist_ok=True)
    out_path = base / "actions.json"
    if not getattr(args, "no_save", False):
        out_path.write_text(dumps_json(to_jsonable(bundle)), encoding="utf-8")

    out_arg = getattr(args, "out", None)
    if out_arg is not None:
        p = out_arg.resolve()
        if p.is_dir():
            p = p / "actions.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dumps_json(to_jsonable(bundle)), encoding="utf-8")

    if args.json:
        print(dumps_json(to_jsonable(bundle)))
    else:
        print(_format_actions_text(bundle), end="")
        if not getattr(args, "no_save", False):
            print(f"Wrote {out_path.relative_to(r)}", file=sys.stderr)
    return 0
