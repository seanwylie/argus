"""CLI: ``argus run`` — loop run summaries and safe first-run profile."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.run.safe_profile import apply_first_run_safe_profile, describe_first_run_safety
from argus.run.summary import (
    build_human_run_summary,
    latest_loop_run_id,
    write_run_summary_artifacts,
)


def run_run_subcommand(args: Any) -> int:
    sub = getattr(args, "run_command", None)
    root = repo_root()

    if sub == "safe-profile":
        path = apply_first_run_safe_profile(root)
        info = describe_first_run_safety(root)
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "ok": True,
                        "written": str(path),
                        **info,
                    }
                )
            )
        else:
            print(f"Wrote Tier 1 (suggest-only) autonomy: {path}", file=sys.stderr)
            print(
                "Next: uv run argus doctor  (optional)  then  uv run argus loop full",
                file=sys.stderr,
            )
        return 0

    if sub == "summary":
        rid = getattr(args, "run_id", None) or None
        if not rid or not str(rid).strip():
            rid = latest_loop_run_id(root)
            if not rid:
                print("No run_id given and no runs/loop/*/ found.", file=sys.stderr)
                return 2
        rid = str(rid).strip()
        text = build_human_run_summary(root, rid)
        if getattr(args, "write", False):
            p = write_run_summary_artifacts(root, rid)
            if p:
                print(f"Wrote {p.relative_to(root.resolve())}", file=sys.stderr)
        if getattr(args, "json", False):
            print(
                dumps_json(
                    {
                        "run_id": rid,
                        "run_dir": f"runs/loop/{rid}",
                        "text": text,
                    }
                )
            )
        else:
            sys.stdout.write(text)
        return 0

    print("Unknown run subcommand.", file=sys.stderr)
    return 2
