"""CLI: ``argus actions`` (validate, dry-run, show, execute action contract files)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.actions.executor import dry_run, execute_action
from argus.actions.render import (
    dry_run_to_json,
    execute_to_json,
    format_action_show,
    format_dry_run_text,
    format_execute_text,
    format_validation_text,
)
from argus.actions.validate import load_action_file, validate_action_contract
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.products.inventory import build_inventory


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_actions_subcommand(args: Any) -> int:
    repo = repo_root()
    action_path: Path = Path(args.file).expanduser()
    if not action_path.is_file():
        print(f"Not a file: {action_path}", file=sys.stderr)
        return 1

    contract, err = load_action_file(action_path)
    if err is not None:
        print(err, file=sys.stderr)
        return 1
    assert contract is not None

    pdir = _products_dir(repo, getattr(args, "products_dir", None))
    inv = build_inventory(repo, products_dir=pdir)

    sub = args.actions_command
    if sub == "show":
        if args.json:
            print(dumps_json(to_jsonable(contract)))
        else:
            print(format_action_show(contract), end="")
        return 0

    if sub == "validate":
        errors = validate_action_contract(contract, repo_root=repo, inventory=inv)
        if args.json:
            print(dumps_json({"ok": not errors, "errors": errors}))
        else:
            print(format_validation_text(errors), end="")
        return 1 if errors else 0

    if sub == "dry-run":
        result = dry_run(contract, repo_root=repo, inventory=inv)
        if args.json:
            print(dry_run_to_json(result))
        else:
            print(format_dry_run_text(result), end="")
        return 0 if result.ok else 1

    if sub == "execute":
        timeout = float(getattr(args, "timeout", 3600.0))
        result = execute_action(
            contract,
            repo_root=repo,
            inventory=inv,
            timeout_s=timeout,
        )
        if args.json:
            print(execute_to_json(result))
        else:
            print(format_execute_text(result), end="")
        if result.returncode is None:
            return 1
        return 0 if result.returncode == 0 else 1

    print(f"Unknown actions subcommand: {sub}", file=sys.stderr)
    return 2
