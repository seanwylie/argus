"""CLI: ``argus mission`` — inspect effective mission, mission experiments."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.mission.mission import resolve_global_mission, write_mission_effective_artifact


def run_mission_command(args: Any) -> int:
    root = repo_root()
    cmd = getattr(args, "mission_command", None)

    if cmd == "show":
        payload = resolve_global_mission(root)
        if args.json:
            print(dumps_json(payload))
            return 0

        path = write_mission_effective_artifact(root, payload)
        print(f"Wrote {path}")
        m = payload.get("mission") or {}
        print()
        print(f"Effective mission: {m.get('id')}")
        print(f"Primary objective: {m.get('primary_objective')}")
        print(f"Risk posture: {m.get('risk_posture')}")
        dr = m.get("drivers") or []
        if dr:
            print("Drivers:")
            for d in dr:
                print(f"  - {d}")
        ch = payload.get("resolution_chain") or []
        if ch:
            print("Resolution: " + " → ".join(ch))
        return 0

    if cmd == "test":
        from argus.mission.experiment import (
            parse_mission_composition_experiment_arg,
            run_mission_experiment,
        )

        profiles = [
            str(p).strip() for p in (getattr(args, "profiles", None) or []) if str(p).strip()
        ]
        comp_raw = [
            str(x).strip() for x in (getattr(args, "compositions", None) or []) if str(x).strip()
        ]
        pid = str(getattr(args, "product_id", "") or "").strip()
        if not pid:
            print("--product-id is required", file=sys.stderr)
            return 2
        if profiles and comp_raw:
            print("Use --profile or --composition, not both", file=sys.stderr)
            return 2
        if not profiles and not comp_raw:
            print("Provide --profile … and/or --composition …", file=sys.stderr)
            return 2
        compositions = [parse_mission_composition_experiment_arg(x) for x in comp_raw]
        if compositions:
            payload = run_mission_experiment(
                root,
                compared_compositions=compositions,
                product_ids=[pid],
                mode="targeted",
                write_artifacts=not bool(getattr(args, "no_save", False)),
            )
        else:
            payload = run_mission_experiment(
                root,
                profiles,
                product_ids=[pid],
                mode="targeted",
                write_artifacts=not bool(getattr(args, "no_save", False)),
            )
        if getattr(args, "json", False):
            print(dumps_json(payload))
        else:
            d = root / "runs" / "mission" / "experiments"
            print(f"Mission experiment ({payload.get('schema')})")
            print(f"Compared variants: {', '.join(payload.get('compared_profiles') or [])}")
            print(f"Products evaluated: {', '.join(payload.get('products_evaluated') or [])}")
            if not getattr(args, "no_save", False):
                print(f"Wrote {d / 'latest.json'}")
            for line in payload.get("behavior_change_summary") or []:
                print(f"- {line}")
        return 0

    if cmd == "sweep":
        from argus.mission.experiment import (
            parse_mission_composition_experiment_arg,
            run_mission_experiment,
        )

        raw = str(getattr(args, "profiles", "") or "")
        profiles = [p.strip() for p in raw.replace(" ", "").split(",") if p.strip()]
        comp_raw = [
            str(x).strip() for x in (getattr(args, "compositions", None) or []) if str(x).strip()
        ]
        if profiles and comp_raw:
            print("Use --profiles or --composition, not both", file=sys.stderr)
            return 2
        if not profiles and not comp_raw:
            print("--profiles or --composition is required", file=sys.stderr)
            return 2
        extra = getattr(args, "product_id", None)
        pids = [str(p).strip() for p in (extra or []) if str(p).strip()] or None
        mode = "sweep_mixed" if pids else "sweep_all"
        if comp_raw:
            compositions = [parse_mission_composition_experiment_arg(x) for x in comp_raw]
            payload = run_mission_experiment(
                root,
                compared_compositions=compositions,
                product_ids=pids,
                mode=mode,
                write_artifacts=not bool(getattr(args, "no_save", False)),
            )
        else:
            payload = run_mission_experiment(
                root,
                profiles,
                product_ids=pids,
                mode=mode,
                write_artifacts=not bool(getattr(args, "no_save", False)),
            )
        if getattr(args, "json", False):
            print(dumps_json(payload))
        else:
            d = root / "runs" / "mission" / "experiments"
            print(f"Mission sweep ({payload.get('schema')}) mode={payload.get('experiment_mode')}")
            print(f"Compared variants: {', '.join(payload.get('compared_profiles') or [])}")
            print(f"Products evaluated: {', '.join(payload.get('products_evaluated') or [])}")
            if not getattr(args, "no_save", False):
                print(f"Wrote {d / 'latest.json'}")
            for line in payload.get("behavior_change_summary") or []:
                print(f"- {line}")
        return 0

    print("Unknown mission subcommand", file=sys.stderr)
    return 2
