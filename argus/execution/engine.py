"""Validate and run action contracts via subprocess (no shell)."""

from __future__ import annotations

import os
import secrets
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.actions.models import ActionContract, DryRunResult
from argus.actions.validate import load_action_file, resolve_working_directory
from argus.autonomy.controller import enforce_autonomy_or_raise, record_autonomy_execution
from argus.containment.policy import subprocess_env_for_repo
from argus.core.serialize import dumps_json, loads_json, to_jsonable
from argus.execution.models import ExecutionRun, ExecutionStatus
from argus.execution.sandbox import validate_execution_sandbox
from argus.project_permissions.gate import evaluate_phase1_for_keys, infer_phase1_key

MAX_CAPTURE_BYTES = 2 * 1024 * 1024
ENV_TIMEOUT = "ARGUS_EXECUTION_TIMEOUT_SECONDS"
DEFAULT_TIMEOUT_S = 3600.0


class ExecutionBlocked(Exception):
    """Execution refused before subprocess (policy / validation)."""

    def __init__(
        self,
        reasons: list[str],
        *,
        phase1_execution_detail: dict[str, Any] | None = None,
    ):
        self.reasons = reasons
        self.phase1_execution_detail = phase1_execution_detail
        super().__init__("; ".join(reasons))


def execution_root(repo_root: Path) -> Path:
    d = repo_root.resolve() / "runs" / "execution"
    d.mkdir(parents=True, exist_ok=True)
    return d


def new_run_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"exec_{ts}_{secrets.token_hex(4)}"


def run_dir(repo_root: Path, run_id: str) -> Path:
    return execution_root(repo_root) / run_id


def save_run(repo_root: Path, run: ExecutionRun) -> Path:
    p = run_dir(repo_root, run.run_id) / "run.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(to_jsonable(run)), encoding="utf-8")
    return p


def load_run(repo_root: Path, run_id: str) -> ExecutionRun:
    p = run_dir(repo_root, run_id) / "run.json"
    if not p.is_file():
        raise FileNotFoundError(run_id)
    raw = loads_json(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("invalid run.json")
    st = raw.get("status", ExecutionStatus.PENDING.value)
    sl_raw = raw.get("subprocess_launched")
    subprocess_launched: bool | None = None
    if isinstance(sl_raw, bool):
        subprocess_launched = sl_raw
    return ExecutionRun(
        run_id=str(raw["run_id"]),
        action_id=str(raw["action_id"]),
        product_id=str(raw["product_id"]),
        command=str(raw["command"]),
        working_directory=str(raw["working_directory"]),
        started_at=str(raw["started_at"]),
        finished_at=str(raw.get("finished_at") or ""),
        status=ExecutionStatus(str(st)),
        output_log=str(raw.get("output_log") or ""),
        error_log=str(raw.get("error_log") or ""),
        exit_code=(int(raw["exit_code"]) if raw.get("exit_code") is not None else None),
        rollback_notes=str(raw.get("rollback_notes") or ""),
        schema=str(raw.get("schema") or "argus.execution_run.v1"),
        execution_detail=raw.get("execution_detail") if isinstance(raw.get("execution_detail"), dict) else None,
        subprocess_launched=subprocess_launched,
    )


def _truncate(s: str) -> str:
    if len(s.encode("utf-8")) <= MAX_CAPTURE_BYTES:
        return s
    enc = s.encode("utf-8")[:MAX_CAPTURE_BYTES].decode("utf-8", errors="ignore")
    return enc + "\n... [output truncated]\n"


def _embed_phase1_on_run(
    run: ExecutionRun,
    *,
    phase1_ev: dict[str, Any] | None,
    phase1_audit_rel: str | None,
) -> None:
    if not phase1_ev or not phase1_audit_rel:
        return
    from argus.orchestrator.phase1_execution_detail import embed_phase1_evaluated

    run.execution_detail = embed_phase1_evaluated(
        {},
        permission_decision=phase1_ev,
        audit_path_repo_relative=phase1_audit_rel,
    )


def build_blocked_governed_run(
    contract: ActionContract,
    run_id: str,
    *,
    error_log: str,
    execution_detail: dict[str, Any],
    subprocess_launched: bool = False,
) -> ExecutionRun:
    """Durable run record for governed subprocess attempts blocked before or without subprocess completion."""
    now = datetime.now(timezone.utc).isoformat()
    detail = dict(execution_detail)
    detail.setdefault("subprocess_launched", subprocess_launched)
    return ExecutionRun(
        run_id=run_id,
        action_id=contract.action_id,
        product_id=contract.product_id,
        command=contract.command,
        working_directory=contract.working_directory,
        started_at=now,
        finished_at=now,
        status=ExecutionStatus.BLOCKED,
        output_log="",
        error_log=error_log,
        exit_code=None,
        rollback_notes=contract.rollback_notes or "",
        execution_detail=detail,
        subprocess_launched=subprocess_launched,
    )


def execution_detail_for_pre_subprocess_block(exc: ExecutionBlocked) -> dict[str, Any]:
    """Map ``ExecutionBlocked`` to Phase-1-shaped ``execution_detail`` (embed or not-evaluated)."""
    if exc.phase1_execution_detail:
        d = dict(exc.phase1_execution_detail)
        d.setdefault("subprocess_launched", False)
        return d
    from argus.orchestrator.phase1_execution_detail import embed_phase1_not_evaluated

    return embed_phase1_not_evaluated(
        {"subprocess_launched": False},
        reason="; ".join(exc.reasons),
    )


def execution_detail_for_approval_block(
    phase1_ev: dict[str, Any],
    phase1_audit_rel: str,
    message: str,
) -> dict[str, Any]:
    from argus.orchestrator.phase1_execution_detail import embed_phase1_evaluated

    d = embed_phase1_evaluated(
        {},
        permission_decision=phase1_ev,
        audit_path_repo_relative=phase1_audit_rel,
    )
    d["subprocess_launched"] = False
    d["execution_approval_blocked"] = True
    d["execution_approval_message"] = message
    return d


def execution_detail_for_autonomy_block(
    phase1_ev: dict[str, Any],
    phase1_audit_rel: str,
    exc: ExecutionBlocked,
) -> dict[str, Any]:
    from argus.orchestrator.phase1_execution_detail import embed_phase1_evaluated

    d = embed_phase1_evaluated(
        {},
        permission_decision=phase1_ev,
        audit_path_repo_relative=phase1_audit_rel,
    )
    d["subprocess_launched"] = False
    d["autonomy_blocked"] = True
    d["autonomy_block_reasons"] = list(exc.reasons)
    return d


def _phase1_subprocess_gate(
    repo_root: Path,
    contract: ActionContract,
    *,
    products_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Phase 1 + Phase 2 grant resolution for subprocess execution (mirrors orchestration semantics).

    Writes permission decision audit; on ``blocked_pending_confirmation`` writes pending approval.
    """
    from argus.orchestrator.phase1_execution_detail import embed_phase1_blocked
    from argus.project_permissions.approvals import write_pending_approval_request
    from argus.project_permissions.audit import write_phase1_permission_decision

    pid = str(contract.product_id or "").strip()
    if not pid:
        raise ExecutionBlocked(["project permission gate: action contract missing product_id"])

    key = infer_phase1_key(contract)
    ev = evaluate_phase1_for_keys(
        repo_root,
        pid,
        (key,),
        products_dir=products_dir,
        check_environment=True,
        execution_path="subprocess_execution",
        action_description=str(contract.command or "")[:2000],
        subprocess_grant_action_id=str(contract.action_id),
    )
    audit_path = write_phase1_permission_decision(
        repo_root,
        ev,
        product_id=pid,
        action_slug=str(contract.action_id),
    )
    try:
        rel_audit = str(audit_path.relative_to(repo_root.resolve())).replace("\\", "/")
    except ValueError:
        rel_audit = str(audit_path)

    if ev.get("execution_proceeds") is True:
        return ev, rel_audit

    blocked_detail = embed_phase1_blocked(
        permission_decision=ev,
        audit_path_repo_relative=rel_audit,
    )
    if str(ev.get("aggregate_decision") or "") == "blocked_pending_confirmation":
        pk = ev.get("per_key") or []
        field = ""
        if isinstance(pk, list):
            for row in pk:
                if isinstance(row, dict) and row.get("policy_decision") == "confirm":
                    field = str(row.get("phase1_policy_field") or "")
                    break
        if field:
            ppath = write_pending_approval_request(
                repo_root,
                product_id=pid,
                orchestration_action_id=str(contract.action_id),
                phase1_policy_field=field,
                reason=str(ev.get("reason") or ""),
                execution_path="subprocess_execution",
                phase1_decision_audit_path_repo_relative=rel_audit,
            )
            try:
                rel_pend = str(ppath.relative_to(repo_root.resolve())).replace("\\", "/")
            except ValueError:
                rel_pend = str(ppath)
            blocked_detail["phase1_pending_approval_request_path"] = rel_pend

    raise ExecutionBlocked(
        [str(ev.get("reason") or "project permission gate blocked execution")],
        phase1_execution_detail=blocked_detail,
    )


def ensure_safe_to_execute(dry: DryRunResult) -> None:
    reasons: list[str] = []
    if dry.validation_errors:
        reasons.extend(dry.validation_errors)
    if dry.dangerous_flags:
        reasons.extend([f"unsafe pattern: {x}" for x in dry.dangerous_flags])
    if reasons:
        raise ExecutionBlocked(reasons)


def ensure_execution_allowed(
    contract: ActionContract,
    repo_root: Path,
    dry: DryRunResult,
    *,
    products_dir: Path | None = None,
) -> tuple[dict[str, Any], str]:
    """
    Dry-run safety checks plus execution sandbox (working directory + command policy),
    then Phase 1 (+ Phase 2 grants) for subprocess execution.

    Returns the Phase 1 decision payload and repo-relative audit path for embedding on the run record.
    """
    ensure_safe_to_execute(dry)
    sb = validate_execution_sandbox(contract, repo_root=repo_root)
    if sb:
        raise ExecutionBlocked(sb)
    return _phase1_subprocess_gate(repo_root, contract, products_dir=products_dir)


def run_subprocess(
    contract: ActionContract,
    *,
    repo_root: Path,
    run_id: str,
    phase1_ev: dict[str, Any] | None = None,
    phase1_audit_rel: str | None = None,
) -> ExecutionRun:
    """
    Run ``contract.command`` in ``working_directory`` (resolved under repo).

    Precondition: ``ensure_execution_allowed`` already passed in the runner.
    """
    root = repo_root.resolve()
    enforce_autonomy_or_raise(root, contract)
    sb = validate_execution_sandbox(contract, repo_root=root)
    if sb:
        now = datetime.now(timezone.utc).isoformat()
        run = ExecutionRun(
            run_id=run_id,
            action_id=contract.action_id,
            product_id=contract.product_id,
            command=contract.command,
            working_directory=contract.working_directory,
            started_at=now,
            finished_at=now,
            status=ExecutionStatus.FAILED,
            output_log="",
            error_log="\n".join(sb),
            exit_code=-1,
            rollback_notes=contract.rollback_notes or "",
            subprocess_launched=False,
        )
        _embed_phase1_on_run(run, phase1_ev=phase1_ev, phase1_audit_rel=phase1_audit_rel)
        return run

    wd_path, wd_err = resolve_working_directory(root, contract.working_directory)
    if wd_err or wd_path is None:
        now = datetime.now(timezone.utc).isoformat()
        run = ExecutionRun(
            run_id=run_id,
            action_id=contract.action_id,
            product_id=contract.product_id,
            command=contract.command,
            working_directory=contract.working_directory,
            started_at=now,
            finished_at=now,
            status=ExecutionStatus.FAILED,
            output_log="",
            error_log=f"working_directory: {wd_err}",
            exit_code=-1,
            rollback_notes=contract.rollback_notes or "",
            subprocess_launched=False,
        )
        _embed_phase1_on_run(run, phase1_ev=phase1_ev, phase1_audit_rel=phase1_audit_rel)
        return run

    cmd = contract.command.strip()
    try:
        argv = shlex.split(cmd, posix=True)
    except ValueError as e:
        now = datetime.now(timezone.utc).isoformat()
        run = ExecutionRun(
            run_id=run_id,
            action_id=contract.action_id,
            product_id=contract.product_id,
            command=cmd,
            working_directory=contract.working_directory,
            started_at=now,
            finished_at=now,
            status=ExecutionStatus.FAILED,
            output_log="",
            error_log=f"could not parse command: {e}",
            exit_code=-1,
            rollback_notes=contract.rollback_notes or "",
            subprocess_launched=False,
        )
        _embed_phase1_on_run(run, phase1_ev=phase1_ev, phase1_audit_rel=phase1_audit_rel)
        return run

    if not argv:
        now = datetime.now(timezone.utc).isoformat()
        run = ExecutionRun(
            run_id=run_id,
            action_id=contract.action_id,
            product_id=contract.product_id,
            command=cmd,
            working_directory=contract.working_directory,
            started_at=now,
            finished_at=now,
            status=ExecutionStatus.FAILED,
            output_log="",
            error_log="command parses to empty argv",
            exit_code=-1,
            rollback_notes=contract.rollback_notes or "",
            subprocess_launched=False,
        )
        _embed_phase1_on_run(run, phase1_ev=phase1_ev, phase1_audit_rel=phase1_audit_rel)
        return run

    started = datetime.now(timezone.utc).isoformat()
    run = ExecutionRun(
        run_id=run_id,
        action_id=contract.action_id,
        product_id=contract.product_id,
        command=cmd,
        working_directory=contract.working_directory,
        started_at=started,
        status=ExecutionStatus.RUNNING,
        rollback_notes=contract.rollback_notes or "",
    )
    save_run(root, run)

    timeout_s = DEFAULT_TIMEOUT_S
    raw_t = os.environ.get(ENV_TIMEOUT, "").strip()
    if raw_t:
        try:
            timeout_s = max(1.0, float(raw_t))
        except ValueError:
            pass

    out_txt = ""
    err_txt = ""
    code: int | None = None
    finished: str
    try:
        proc = subprocess.run(
            argv,
            cwd=str(wd_path),
            env=subprocess_env_for_repo(root),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
        out_txt = _truncate(proc.stdout or "")
        err_txt = _truncate(proc.stderr or "")
        code = proc.returncode
        st = ExecutionStatus.SUCCESS if code == 0 else ExecutionStatus.FAILED
        finished = datetime.now(timezone.utc).isoformat()
    except subprocess.TimeoutExpired as e:
        out_txt = _truncate((e.stdout or "") if isinstance(e.stdout, str) else "")
        err_txt = _truncate(
            ((e.stderr or "") if isinstance(e.stderr, str) else "")
            + f"\n[timeout after {timeout_s}s]",
        )
        code = -124
        st = ExecutionStatus.FAILED
        finished = datetime.now(timezone.utc).isoformat()
    except OSError as e:
        err_txt = str(e)
        code = -1
        st = ExecutionStatus.FAILED
        finished = datetime.now(timezone.utc).isoformat()

    run.status = st
    run.finished_at = finished
    run.output_log = out_txt
    run.error_log = err_txt
    run.exit_code = code
    run.subprocess_launched = True

    if code == 0:
        record_autonomy_execution(repo_root, contract)

    _embed_phase1_on_run(run, phase1_ev=phase1_ev, phase1_audit_rel=phase1_audit_rel)

    rdir = run_dir(root, run_id)
    (rdir / "stdout.log").write_text(out_txt, encoding="utf-8")
    (rdir / "stderr.log").write_text(err_txt, encoding="utf-8")
    save_run(root, run)
    return run


def load_contract_from_path(action_path: Path) -> tuple[ActionContract | None, str | None]:
    return load_action_file(action_path.resolve())
