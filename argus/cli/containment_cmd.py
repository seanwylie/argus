"""CLI: ``argus containment`` — credential containment status and escalation report."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.containment.policy import (
    build_capability_map,
    is_containment_enforced,
    load_containment_policy,
    policy_path,
)
from argus.containment.report import render_escalation_report
from argus.core.serialize import dumps_json


def run_containment_command(args: Any) -> int:
    r = repo_root()
    sub = args.containment_command

    if sub == "status":
        payload = build_capability_map(r)
        payload["subprocess_sanitization_active"] = is_containment_enforced(r)
        if args.json:
            print(dumps_json(payload))
        else:
            print(f"Containment enforced: {payload['containment_enforced']}")
            print(f"Policy file: {policy_path(r)} (exists: {policy_path(r).is_file()})")
            caps = payload.get("capabilities") or {}
            for k, v in sorted(caps.items()):
                if isinstance(v, dict):
                    print(f"  {k}: {v.get('status')} — {v.get('reason')}")
        if getattr(args, "save", False):
            _write_debug_artifacts(r, payload)
        return 0

    if sub == "escalation-report":
        text = render_escalation_report(r)
        print(text)
        if getattr(args, "save", False):
            d = r / "runs" / "debug" / "containment"
            d.mkdir(parents=True, exist_ok=True)
            p = d / "escalation_report.md"
            p.write_text(text + "\n", encoding="utf-8")
            print(f"Wrote {p.relative_to(r)}", file=sys.stderr)
        return 0

    return 2


def _write_debug_artifacts(repo_root: Path, payload: dict[str, Any]) -> None:
    d = repo_root / "runs" / "debug" / "containment"
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    pol = load_containment_policy(repo_root)
    out = {
        "schema": "argus.containment_status_snapshot.v0",
        "capability_map": payload,
        "policy_loaded": pol is not None,
        "stripped_key_count_estimate": _strip_diff_count(repo_root),
    }
    p.write_text(dumps_json(out) + "\n", encoding="utf-8")
    print(f"Wrote {p.relative_to(repo_root)}", file=sys.stderr)


def _strip_diff_count(repo_root: Path) -> int | None:
    if not is_containment_enforced(repo_root):
        return None
    import os

    from argus.containment.policy import sanitized_subprocess_environment

    base = dict(os.environ)
    pol = load_containment_policy(repo_root)
    stripped = sanitized_subprocess_environment(base, policy=pol)
    return len(base) - len(stripped)


__all__ = ["run_containment_command"]
