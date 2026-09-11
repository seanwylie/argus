"""CLI entry for ``argus loop``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.loop import run_analysis_loop


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_loop_command(args: Any) -> int:
    sub = getattr(args, "loop_command", None)
    if sub not in ("run", "full"):
        print("Unknown loop subcommand.", file=sys.stderr)
        return 2

    from argus.cli.repo import repo_root

    repo = repo_root()
    pdir = _products_dir(repo, getattr(args, "products_dir", None))

    if sub == "full":
        from argus.loop.harness import run_full_loop_harness

        dry = bool(getattr(args, "dry_run", True))
        fail_fast = bool(getattr(args, "fail_fast", False))
        code, summary = run_full_loop_harness(
            repo,
            products_dir=pdir,
            product_id=getattr(args, "product_id", None),
            dry_run_execution=dry,
            continue_on_error=not fail_fast,
        )
        run_id = summary.get("run_id", "?")
        run_dir = repo / "runs" / "loop" / str(run_id)
        if getattr(args, "json", False):
            print(dumps_json({"exit_code": code, "summary": summary, "run_dir": str(run_dir)}))
        else:
            status = "ok" if code == 0 else "failed"
            print(
                f"Loop full {status} (exit {code})\n"
                f"  run_id: {run_id}\n"
                f"  summary: {run_dir.relative_to(repo.resolve()) / 'summary.json'}",
                file=sys.stderr,
            )
            for row in summary.get("stages") or []:
                s = row.get("stage", "?")
                ok = row.get("ok")
                err = row.get("error")
                line = f"  - {s}: {'ok' if ok else 'FAILED'}"
                if err:
                    line += f" — {err}"
                print(line, file=sys.stderr)
        return code

    code, manifest = run_analysis_loop(
        repo,
        products_dir=pdir,
        product_id=getattr(args, "product_id", None),
        continue_on_error=bool(getattr(args, "continue_on_error", False)),
    )

    run_id = manifest.get("run_id", "?")
    run_dir = repo / "runs" / "loop" / str(run_id)
    if getattr(args, "json", False):
        print(dumps_json({"exit_code": code, "manifest": manifest, "run_dir": str(run_dir)}))
    else:
        status = "ok" if code == 0 else "failed"
        print(
            f"Loop {status} (exit {code})\n"
            f"  run_id: {run_id}\n"
            f"  artifacts: {run_dir.relative_to(repo.resolve())}",
            file=sys.stderr,
        )
        for row in manifest.get("stages") or []:
            s = row.get("stage", "?")
            ok = row.get("ok")
            err = row.get("error")
            line = f"  - {s}: {'ok' if ok else 'FAILED'}"
            if err:
                line += f" — {err}"
            print(line, file=sys.stderr)

    return code
