"""CLI: ``argus validate artifacts``."""

from __future__ import annotations

import sys
from typing import Any

from argus.core.serialize import dumps_json
from argus.validation.validate import validate_all_artifacts, validate_repo_artifacts


def run_validate_command(args: Any) -> int:
    from argus.cli.repo import repo_root

    repo = repo_root()
    sub = getattr(args, "validate_command", None)
    if sub != "artifacts":
        print("Unknown validate subcommand.", file=sys.stderr)
        return 2

    run_id = getattr(args, "run_id", None)
    if run_id:
        run_id = str(run_id).strip() or None

    rep = (
        validate_all_artifacts(repo, loop_run_id=run_id)
        if run_id
        else validate_repo_artifacts(repo)
    )
    payload = {
        "schema": "argus.validate_report.v1",
        "ok": rep.ok,
        "checked_files": rep.checked_files,
        "issues": [
            {"path": i.path, "message": i.message, "severity": i.severity} for i in rep.issues
        ],
    }
    if getattr(args, "json", False):
        print(dumps_json(payload))
    else:
        status = "ok" if rep.ok else "failed"
        print(f"Artifact validation {status} ({len(rep.issues)} issue(s))", file=sys.stderr)
        for i in rep.issues:
            print(f"  [{i.severity}] {i.path}: {i.message}", file=sys.stderr)
    return 0 if rep.ok else 1

