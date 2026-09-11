"""Orchestrate validation, locking, and subprocess execution."""

from __future__ import annotations

import os
from pathlib import Path

from argus.actions.executor import dry_run
from argus.actions.gate import ExecutionNotApprovedError, require_execution_approval
from argus.autonomy.safe_execution import evaluate_safe_autonomy, is_autonomous_execution_enabled
from argus.execution.engine import (
    ExecutionBlocked,
    build_blocked_governed_run,
    ensure_execution_allowed,
    execution_detail_for_approval_block,
    execution_detail_for_autonomy_block,
    execution_detail_for_pre_subprocess_block,
    execution_root,
    load_contract_from_path,
    new_run_id,
    run_subprocess,
    save_run,
)
from argus.execution.models import ExecutionRun, ExecutionStatus
from argus.products.inventory import build_inventory
from argus.project_permissions.approvals import consume_confirm_once_grant

ENV_ENABLED = "ARGUS_EXECUTION_ENABLED"


def _is_execution_enabled(*, cli_flag: bool) -> bool:
    if cli_flag:
        return True
    v = os.environ.get(ENV_ENABLED, "").strip().lower()
    return v in ("1", "true", "yes")


def _is_autonomous_invocation(*, cli_autonomous: bool) -> bool:
    return bool(cli_autonomous) or is_autonomous_execution_enabled()


def _acquire_lock(repo_root: Path, run_id: str) -> None:
    ex = execution_root(repo_root)
    lock = ex / ".lock"
    if lock.exists():
        try:
            lines = lock.read_text(encoding="utf-8").strip().split("\n")
            pid = int(lines[0])
            os.kill(pid, 0)
            raise ExecutionBlocked(
                [f"another execution is in progress (pid {pid}, lock {lock.as_posix()})"],
            )
        except (ProcessLookupError, OSError, ValueError):
            lock.unlink(missing_ok=True)
    lock.write_text(f"{os.getpid()}\n{run_id}\n", encoding="utf-8")


def _release_lock(repo_root: Path) -> None:
    lock = repo_root.resolve() / "runs" / "execution" / ".lock"
    if not lock.is_file():
        return
    try:
        lines = lock.read_text(encoding="utf-8").strip().split("\n")
        pid = int(lines[0])
        if pid == os.getpid():
            lock.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def run_action_file(
    repo_root: Path,
    action_path: Path,
    *,
    enable_execution: bool,
    autonomous: bool = False,
    products_dir: Path | None = None,
) -> ExecutionRun:
    """
    Load action file, dry-run validate, then execute if policy allows.

    Opt-in: ``enable_execution`` or ``ARGUS_EXECUTION_ENABLED=1``, **or**
    ``autonomous=True`` / ``ARGUS_AUTONOMOUS_SAFE_EXECUTION=1`` when the action
    passes :func:`~argus.autonomy.safe_execution.evaluate_safe_autonomy`.

    Requires a stored approval **or** auto-approval (see ``argus.approval.rules``).
    """
    explicit = _is_execution_enabled(cli_flag=enable_execution)
    auto_inv = _is_autonomous_invocation(cli_autonomous=autonomous)
    if not explicit and not auto_inv:
        raise ExecutionBlocked(
            [
                "execution is disabled by default; set "
                f"{ENV_ENABLED}=1 or pass --enable-execution, "
                "or pass --autonomous / set ARGUS_AUTONOMOUS_SAFE_EXECUTION=1 for safe autonomous actions",
            ],
        )

    contract, err = load_contract_from_path(action_path)
    if err or contract is None:
        raise ExecutionBlocked([err or "could not load action file"])

    root = repo_root.resolve()

    if auto_inv:
        ev = evaluate_safe_autonomy(contract, repo_root=root)
        if not ev.autonomous:
            raise ExecutionBlocked(
                ["autonomous execution refused: " + "; ".join(ev.reasons)],
            )

    inv = build_inventory(root, products_dir=products_dir)

    dr = dry_run(contract, repo_root=root, inventory=inv)
    run_id = new_run_id()

    try:
        phase1_ev, phase1_audit_rel = ensure_execution_allowed(contract, root, dr, products_dir=products_dir)
    except ExecutionBlocked as e:
        detail = execution_detail_for_pre_subprocess_block(e)
        run = build_blocked_governed_run(
            contract,
            run_id,
            error_log="\n".join(e.reasons),
            execution_detail=detail,
            subprocess_launched=False,
        )
        save_run(root, run)
        return run

    try:
        require_execution_approval(root, contract)
    except ExecutionNotApprovedError as e:
        detail = execution_detail_for_approval_block(phase1_ev, phase1_audit_rel, str(e))
        run = build_blocked_governed_run(
            contract,
            run_id,
            error_log=str(e),
            execution_detail=detail,
            subprocess_launched=False,
        )
        save_run(root, run)
        return run

    _acquire_lock(root, run_id)
    try:
        try:
            run = run_subprocess(
                contract,
                repo_root=root,
                run_id=run_id,
                phase1_ev=phase1_ev,
                phase1_audit_rel=phase1_audit_rel,
            )
        except ExecutionBlocked as e:
            if any(str(x).strip().lower().startswith("autonomy:") for x in e.reasons):
                detail = execution_detail_for_autonomy_block(phase1_ev, phase1_audit_rel, e)
            else:
                detail = execution_detail_for_pre_subprocess_block(e)
            run = build_blocked_governed_run(
                contract,
                run_id,
                error_log="\n".join(e.reasons),
                execution_detail=detail,
                subprocess_launched=False,
            )
            save_run(root, run)
            return run
        if run.status == ExecutionStatus.SUCCESS and isinstance(phase1_ev.get("phase1_pending_consumption"), dict):
            pc = phase1_ev["phase1_pending_consumption"]
            gid = str(pc.get("grant_id") or "").strip()
            if gid:
                consume_confirm_once_grant(root, str(contract.product_id), gid)
        return run
    finally:
        _release_lock(root)
