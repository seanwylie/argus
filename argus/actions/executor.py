"""Dry-run analysis and gated execution (requires approval)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from argus.actions.gate import ExecutionNotApprovedError, require_execution_approval
from argus.actions.models import ActionContract, DryRunResult, ExecuteResult, FileCheckResult
from argus.actions.validate import (
    check_referenced_files,
    dangerous_patterns,
    referenced_path_tokens,
    resolve_working_directory,
    validate_action_contract,
)
from argus.autonomy.controller import autonomy_denial_message, record_autonomy_execution
from argus.containment.policy import subprocess_env_for_repo
from argus.products.inventory import ProductInventory, build_inventory


def dry_run(
    contract: ActionContract,
    *,
    repo_root: Path,
    inventory: ProductInventory | None = None,
) -> DryRunResult:
    """
    Analyze what would run: validation, path existence, and dangerous patterns.

    Does **not** execute ``contract.command`` or invoke a shell.
    """
    root = repo_root.resolve()
    inv = inventory if inventory is not None else build_inventory(root)

    wd_resolved = ""
    wd_path, _wd_err = resolve_working_directory(root, contract.working_directory)
    if wd_path is not None:
        wd_resolved = wd_path.relative_to(root).as_posix()

    val_errors = validate_action_contract(contract, repo_root=root, inventory=inv)

    file_checks: list[FileCheckResult] = []
    if wd_path is not None:
        tokens = referenced_path_tokens(contract.command)
        pairs = check_referenced_files(root, wd_path, tokens)
        for rel_disp, exists in pairs:
            kind = "script" if rel_disp.endswith((".sh", ".bash", ".py")) else "path"
            file_checks.append(FileCheckResult(path=rel_disp, kind=kind, exists=exists))

    danger = dangerous_patterns(
        contract.command,
        repo_root=root,
        product_id=contract.product_id.strip(),
        lifecycle=contract.lifecycle_transition,
    )

    preview_lines = [
        f"action_id: {contract.action_id}",
        f"product_id: {contract.product_id}",
        f"working_directory (resolved): {wd_resolved or '(invalid)'}",
        "command:",
        f"  {contract.command}",
    ]
    rendered = "\n".join(preview_lines)

    notes: list[str] = []
    if contract.requires_approval:
        notes.append("requires_approval is true — human approval expected before execution")
    if contract.safe_to_auto_execute and danger:
        notes.append("safe_to_auto_execute is true but dangerous patterns were detected — inconsistent")

    return DryRunResult(
        action_id=contract.action_id,
        product_id=contract.product_id,
        rendered_preview=rendered,
        working_directory_resolved=wd_resolved,
        validation_errors=list(val_errors),
        file_checks=file_checks,
        dangerous_flags=danger,
        notes=notes,
    )


def execute_action(
    contract: ActionContract,
    *,
    repo_root: Path,
    inventory: ProductInventory | None = None,
    timeout_s: float | None = 3600.0,
) -> ExecuteResult:
    """
    Run ``contract.command`` in the resolved working directory **only if**:

    - dry-run validation passes (no validation errors), and
    - a matching **approved** record exists in ``runs/approval/records/``.

    Uses ``/bin/sh -c`` with ``cwd`` set to the resolved product-relative working directory.
    """
    root = repo_root.resolve()
    inv = inventory if inventory is not None else build_inventory(root)
    dr = dry_run(contract, repo_root=root, inventory=inv)

    if not dr.ok:
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout="",
            stderr="Dry-run failed validation; not executed.",
            dry_run_snapshot=dr,
        )

    if dr.dangerous_flags:
        joined = "; ".join(dr.dangerous_flags)
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout="",
            stderr=f"Unsafe command patterns detected; not executed: {joined}",
            dry_run_snapshot=dr,
        )

    deny = autonomy_denial_message(root, contract)
    if deny is not None:
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout="",
            stderr=deny,
            dry_run_snapshot=dr,
        )

    try:
        require_execution_approval(root, contract)
    except ExecutionNotApprovedError as e:
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout="",
            stderr=str(e),
            dry_run_snapshot=dr,
        )

    wd_path, wd_err = resolve_working_directory(root, contract.working_directory)
    if wd_path is None:
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout="",
            stderr=f"Cannot resolve working_directory: {wd_err or 'unknown'}",
            dry_run_snapshot=dr,
        )

    try:
        record_autonomy_execution(root, contract)
        proc = subprocess.run(
            ["/bin/sh", "-c", contract.command],
            cwd=str(wd_path),
            env=subprocess_env_for_repo(root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            dry_run_snapshot=dr,
        )
    except subprocess.TimeoutExpired as e:
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout=e.stdout or "",
            stderr=(e.stderr or "") + "\n(timeout)",
            dry_run_snapshot=dr,
        )
    except OSError as e:
        return ExecuteResult(
            action_id=contract.action_id,
            product_id=contract.product_id,
            returncode=None,
            stdout="",
            stderr=str(e),
            dry_run_snapshot=dr,
        )
