"""CLI: ``argus execution``."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.actions.executor import dry_run
from argus.actions.render import format_dry_run_text
from argus.autonomy.safe_execution import evaluate_safe_autonomy
from argus.capabilities.requests.integrations import record_execution_blocked
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.execution.engine import ExecutionBlocked, load_contract_from_path, load_run
from argus.execution.models import ExecutionRun, ExecutionStatus
from argus.execution.runner import run_action_file
from argus.execution.sandbox import validate_execution_sandbox
from argus.products.inventory import build_inventory


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def format_run_text(run: ExecutionRun) -> str:
    lines = [
        f"run_id: {run.run_id}",
        f"action_id: {run.action_id}",
        f"product_id: {run.product_id}",
        f"status: {run.status.value}",
        f"exit_code: {run.exit_code}",
        f"started_at: {run.started_at}",
        f"finished_at: {run.finished_at}",
        f"working_directory: {run.working_directory}",
        f"command: {run.command}",
        "",
        "--- stdout ---",
        run.output_log or "(empty)",
        "",
        "--- stderr ---",
        run.error_log or "(empty)",
        "",
    ]
    if run.rollback_notes.strip():
        lines.extend(["rollback_notes:", run.rollback_notes, ""])
    if run.subprocess_launched is not None:
        lines.extend([f"subprocess_launched: {run.subprocess_launched}", ""])
    return "\n".join(lines)


def run_execution_command(args: Any) -> int:
    repo = repo_root()
    sub = args.execution_command

    if sub == "dry-run":
        path = Path(args.action_file).expanduser().resolve()
        if not path.is_file():
            print(f"Not a file: {path}", file=sys.stderr)
            return 1
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        contract, err = load_contract_from_path(path)
        if err or contract is None:
            print(err or "could not load action file", file=sys.stderr)
            return 1
        inv = build_inventory(repo, products_dir=pdir)
        dr = dry_run(contract, repo_root=repo.resolve(), inventory=inv)
        sb = validate_execution_sandbox(contract, repo_root=repo.resolve())
        would = dr.ok and not dr.dangerous_flags and not sb
        if args.json:
            autonomy_ev = evaluate_safe_autonomy(contract, repo_root=repo.resolve())
            payload = {
                "dry_run": to_jsonable(dr),
                "sandbox_errors": sb,
                "would_pass_execution_policy": would,
                "autonomy": {
                    "autonomous": autonomy_ev.autonomous,
                    "reasons": autonomy_ev.reasons,
                },
            }
            print(dumps_json(payload))
        else:
            sys.stdout.write(format_dry_run_text(dr))
            sys.stdout.write("\n--- Execution sandbox ---\n")
            if sb:
                for x in sb:
                    sys.stdout.write(f"  BLOCKED: {x}\n")
            else:
                sys.stdout.write(
                    "  OK — working directory is under products/<id>/, system temp, "
                    "or runs/execution/tmp/\n",
                )
                sys.stdout.write("  OK — no additional execution command blocks\n")
            sys.stdout.write(
                f"\nWould pass execution policy: {'yes' if would else 'no'}\n"
                "(Actual `execution run` also requires --enable-execution and approval when enforced.)\n",
            )
        return 0 if would else 1

    if sub == "run":
        path = Path(args.action_file).expanduser().resolve()
        if not path.is_file():
            print(f"Not a file: {path}", file=sys.stderr)
            return 1
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        try:
            run = run_action_file(
                repo,
                path,
                enable_execution=bool(args.enable_execution),
                autonomous=bool(getattr(args, "autonomous", False)),
                products_dir=pdir,
            )
        except ExecutionBlocked as e:
            for r in e.reasons:
                print(r, file=sys.stderr)
            c2, _err = load_contract_from_path(path)
            aid = c2.action_id if c2 else ""
            pid = c2.product_id if c2 else ""
            try:
                if not any(str(x).strip().lower().startswith("autonomy:") for x in e.reasons):
                    rec = record_execution_blocked(
                        repo,
                        e.reasons,
                        action_path=str(path),
                        action_id=aid or None,
                        product_id=pid or None,
                    )
                    print(
                        f"Recorded capability request {rec.request_id} (argus capabilities request show {rec.request_id})",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "Autonomy capability request already recorded (see runs/capabilities/requests/ and "
                        "argus capabilities request list)",
                        file=sys.stderr,
                    )
            except OSError:
                pass
            return 1
        if run.status == ExecutionStatus.BLOCKED:
            reasons = [x for x in (run.error_log or "").split("\n") if x.strip()]
            for r in reasons:
                print(r, file=sys.stderr)
            c2, _err = load_contract_from_path(path)
            aid = c2.action_id if c2 else ""
            pid = c2.product_id if c2 else ""
            try:
                if reasons and not any(str(x).strip().lower().startswith("autonomy:") for x in reasons):
                    rec = record_execution_blocked(
                        repo,
                        reasons,
                        action_path=str(path),
                        action_id=aid or None,
                        product_id=pid or None,
                    )
                    print(
                        f"Recorded capability request {rec.request_id} (argus capabilities request show {rec.request_id})",
                        file=sys.stderr,
                    )
            except OSError:
                pass
            print(
                f"Blocked run recorded: {run.run_id} — {repo / 'runs' / 'execution' / run.run_id}",
                file=sys.stderr,
            )
        if args.json:
            print(dumps_json(to_jsonable(run)))
        else:
            print(f"Execution finished: {run.run_id} ({run.status.value}, exit={run.exit_code})")
            print(f"Artifacts: {repo / 'runs' / 'execution' / run.run_id}")
        return 0 if run.status.value == "success" else 2

    if sub == "show":
        try:
            run = load_run(repo, args.run_id)
        except FileNotFoundError:
            print(f"Unknown run_id: {args.run_id!r}", file=sys.stderr)
            return 1
        if args.json:
            print(dumps_json(to_jsonable(run)))
        else:
            sys.stdout.write(format_run_text(run))
        return 0

    print("Unknown execution subcommand.", file=sys.stderr)
    return 2
