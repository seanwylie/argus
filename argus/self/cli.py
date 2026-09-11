"""CLI: ``argus self audit`` and ``argus self improve``."""

from __future__ import annotations

import sys
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json


def run_self_command(args: Any) -> int:
    sub = args.self_command
    if sub == "improve":
        from argus.self_improvement.cli import run_self_improve_command

        return run_self_improve_command(args)

    if sub != "audit":
        print("Unknown self subcommand.", file=sys.stderr)
        return 2

    from argus.self.audit import run_self_audit

    r = repo_root()
    report = run_self_audit(r)
    if args.json:
        print(dumps_json(report.to_jsonable()))
        return 0

    print(f"Argus self-audit ({report.generated_at_utc})")
    print(f"Repo: {report.repo_root}\n")
    if not report.findings:
        print("No findings — local signals, history, and experiments look sufficient for a basic critique.")
        return 0

    for f in report.findings:
        print(f"[{f.severity.value}] {f.category.value}")
        print(f"  {f.message}")
        if f.detail:
            print(f"  {f.detail}")
        print("")
    summary = report.to_jsonable()["summary"]
    print(
        f"Total: {summary['count']} finding(s); "
        + ", ".join(f"{k}={v}" for k, v in summary["by_category"].items() if v),
        file=sys.stderr,
    )
    return 0
