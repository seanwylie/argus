"""
Builder v0.5: resolve prepared prompt/task artifacts and hand off to Cursor (optional subprocess).

Honest, inspectable bridge — not autonomous execution. Records each attempt under ``runs/builder/invoke/``.

Execution backends (``--execute`` only):

* **cursor** — ``cursor <builder_next_prompt.md>`` (opens file in IDE).
* **agent** — ``agent [--extra…] -p --output-format json --workspace <repo> "<prompt text>"`` (headless print mode; no ``--mode=plan``; see env vars).
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.git_branch_isolation import compute_git_branch_isolation, read_git_branch
from argus.builder.git_diff_capture import (
    GIT_BASELINE_SCHEMA,
    capture_baseline_commit,
    collect_diff_since_baseline,
    ensure_product_git_workspace,
    resolve_git_workspace,
)
from argus.builder.git_worktree_isolation import (
    argus_root_worktree_enabled,
    prepare_argus_root_worktree_isolation,
    remove_argus_root_worktree,
    sync_builder_artifacts_into_worktree,
)
from argus.builder.landlock_support import merge_landlock_status_into_meta
from argus.builder.next_expansion_prepare import next_expansion_path, write_prepare_artifacts
from argus.builder.sandbox import build_agent_containment, infer_builder_filesystem_scope
from argus.core.serialize import dumps_json
from argus.project_permissions.gate import evaluate_phase1_for_keys

BUILDER_PROMPT_FILENAME = "builder_next_prompt.md"
BUILDER_TASK_FILENAME = "builder_task.json"
BUILDER_INVOKE_RECORD_SCHEMA = "argus.builder_invoke_record.v1"

# Executable: ARGUS_CURSOR_CLI overrides CURSOR_CLI; default ``cursor`` (IDE CLI if installed).
ENV_CURSOR_PRIMARY = "ARGUS_CURSOR_CLI"
ENV_CURSOR_FALLBACK = "CURSOR_CLI"
DEFAULT_CURSOR_CLI = "cursor"

# Agent backend: ARGUS_AGENT_CLI overrides default ``agent``; optional ARGUS_AGENT_EXTRA_ARGS (shell-quoted).
ENV_AGENT_PRIMARY = "ARGUS_AGENT_CLI"
DEFAULT_AGENT_CLI = "agent"
ENV_AGENT_EXTRA_ARGS = "ARGUS_AGENT_EXTRA_ARGS"
# Subprocess timeout for agent (seconds). Generous default; does not imply task completeness.
ENV_AGENT_TIMEOUT = "ARGUS_AGENT_TIMEOUT_SECONDS"
DEFAULT_AGENT_TIMEOUT = 7200
DEFAULT_CURSOR_TIMEOUT = 120

MAX_AGENT_IO_RECORD_CHARS = 12000

# Fixed argv tail after env-driven extras: headless --print (-p) with JSON to stdout and explicit workspace.
# Omits --mode=plan/--mode=ask so the Cursor agent CLI may use write/shell tools (see `agent --help`).
AGENT_HEADLESS_INJECTED_ARGV = ("-p", "--output-format", "json")


class BuilderInvokeError(ValueError):
    """Missing artifacts or invalid invoke configuration."""


@dataclass(frozen=True)
class ResolvedArtifacts:
    """Absolute paths to existing prepared Builder files."""

    prompt_md: Path
    task_json: Path
    source: str  # "product_generated" | "runs_prepare" | "explicit"


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


def invoke_record_dir(repo_root: Path, product_id: str) -> Path:
    return (repo_root / "runs" / "builder" / "invoke" / product_id).resolve()


def resolve_cursor_cli() -> str:
    return (
        os.environ.get(ENV_CURSOR_PRIMARY)
        or os.environ.get(ENV_CURSOR_FALLBACK)
        or DEFAULT_CURSOR_CLI
    ).strip() or DEFAULT_CURSOR_CLI


def resolve_agent_cli() -> str:
    return (os.environ.get(ENV_AGENT_PRIMARY) or DEFAULT_AGENT_CLI).strip() or DEFAULT_AGENT_CLI


def parse_agent_extra_args_from_env() -> list[str]:
    raw = (os.environ.get(ENV_AGENT_EXTRA_ARGS) or "").strip()
    if not raw:
        return []
    try:
        return shlex.split(raw)
    except ValueError as e:
        raise BuilderInvokeError(
            f"Could not parse {ENV_AGENT_EXTRA_ARGS!r} as shell-like tokens: {e}"
        ) from e


def resolve_agent_timeout_seconds() -> int:
    raw = (os.environ.get(ENV_AGENT_TIMEOUT) or "").strip()
    if not raw:
        return DEFAULT_AGENT_TIMEOUT
    try:
        n = int(raw, 10)
    except ValueError:
        return DEFAULT_AGENT_TIMEOUT
    return max(1, n)


def _truncate_for_record(s: str, limit: int = MAX_AGENT_IO_RECORD_CHARS) -> str:
    if len(s) <= limit:
        return s
    return s[: limit - 32] + "\n… [truncated for invoke record] …\n"


def resolve_prepared_artifacts(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
    prompt_path: Path | None = None,
    task_path: Path | None = None,
) -> ResolvedArtifacts:
    """
    Locate ``builder_next_prompt.md`` and ``builder_task.json``.

    Search order when paths not given:

    1. ``products/<id>/generated/``
    2. ``runs/builder/prepare/<id>/``
    """
    if prompt_path is not None or task_path is not None:
        if prompt_path is None or task_path is None:
            raise BuilderInvokeError(
                "Both --prompt-path and --task-path are required when overriding paths"
            )
        pp = prompt_path.expanduser().resolve()
        tp = task_path.expanduser().resolve()
        if not pp.is_file():
            raise BuilderInvokeError(f"Prompt file not found: {pp}")
        if not tp.is_file():
            raise BuilderInvokeError(f"Task file not found: {tp}")
        return ResolvedArtifacts(prompt_md=pp, task_json=tp, source="explicit")

    base = _products_base(repo_root, products_dir)
    candidates = [
        (
            base / product_id / "generated",
            "product_generated",
        ),
        (
            repo_root / "runs" / "builder" / "prepare" / product_id,
            "runs_prepare",
        ),
    ]
    for directory, source in candidates:
        pp = directory / BUILDER_PROMPT_FILENAME
        tp = directory / BUILDER_TASK_FILENAME
        if pp.is_file() and tp.is_file():
            return ResolvedArtifacts(prompt_md=pp.resolve(), task_json=tp.resolve(), source=source)

    raise BuilderInvokeError(
        f"No prepared Builder artifacts for {product_id!r}. Expected both "
        f"{BUILDER_PROMPT_FILENAME} and {BUILDER_TASK_FILENAME} under "
        f"products/{product_id}/generated/ or runs/builder/prepare/{product_id}/ "
        f"(or pass --prompt-path and --task-path). Run: argus builder prepare {product_id}"
    )


def try_resolve_prepared_artifacts(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
    prompt_path: Path | None = None,
    task_path: Path | None = None,
) -> ResolvedArtifacts | None:
    """Like :func:`resolve_prepared_artifacts` but returns ``None`` if artifacts are missing."""
    try:
        return resolve_prepared_artifacts(
            repo_root,
            product_id,
            products_dir=products_dir,
            prompt_path=prompt_path,
            task_path=task_path,
        )
    except BuilderInvokeError:
        return None


def _load_task_summary(task_path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(task_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise BuilderInvokeError(f"Invalid builder task JSON at {task_path}: {e}") from e
    if not isinstance(raw, dict):
        raise BuilderInvokeError("builder_task.json root must be an object")
    return raw


def _norm_target(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if not d or not isinstance(d, dict):
        return None
    return {
        "id": d.get("id"),
        "target_type": d.get("target_type"),
        "group_id": d.get("group_id"),
    }


def _targets_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if not a or not b:
        return False
    return (
        a.get("id") == b.get("id")
        and a.get("target_type") == b.get("target_type")
        and a.get("group_id") == b.get("group_id")
    )


def assess_prepared_contract_vs_declared(
    repo_root: Path,
    product_id: str,
    *,
    task: dict[str, Any],
    products_dir: Path | None,
    explicit_paths: bool,
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None, str | None]:
    """
    Compare ``resolved_target`` in ``builder_task.json`` to ``primary_target`` in ``next_expansion.json``.

    Returns ``(prepared_contract_status, declared_target, prepared_target, next_expansion_path_str)`` where
    ``prepared_contract_status`` is ``aligned``, ``stale``, or ``unknown``.
    """
    rt = task.get("resolved_target")
    if not isinstance(rt, dict):
        rt = {}
    prepared = _norm_target(rt)

    if explicit_paths:
        return "unknown", None, prepared, None

    ne_path = next_expansion_path(repo_root, product_id, products_dir=products_dir)
    rr = repo_root.resolve()
    try:
        ne_rel = str(ne_path.resolve().relative_to(rr))
    except ValueError:
        ne_rel = str(ne_path.resolve())

    if not ne_path.is_file():
        return "unknown", None, prepared, ne_rel

    try:
        raw = json.loads(ne_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "unknown", None, prepared, ne_rel

    if not isinstance(raw, dict):
        return "unknown", None, prepared, ne_rel

    pt = raw.get("primary_target")
    if not isinstance(pt, dict):
        return "unknown", None, prepared, ne_rel

    declared = _norm_target(pt)
    if declared is None:
        return "unknown", None, prepared, ne_rel

    if prepared is None:
        return "unknown", declared, prepared, ne_rel

    if _targets_equal(declared, prepared):
        return "aligned", declared, prepared, ne_rel

    return "stale", declared, prepared, ne_rel


def _suggest_prepare_command(product_id: str) -> str:
    return f"uv run argus builder prepare {product_id}"


def build_invoke_argv(*, cursor_cli: str, prompt_abs: Path) -> list[str]:
    """Argv passed to ``subprocess`` — open the prompt file in Cursor (workspace = repo cwd)."""
    return [cursor_cli, str(prompt_abs)]


def build_agent_argv(
    *,
    agent_cli: str,
    prompt_text: str,
    extra_args: list[str],
    repo_root: Path,
) -> list[str]:
    """
    Argv for execution-capable headless ``agent`` invocation.

    Shape: ``agent [extras…] -p --output-format json --workspace <workspace_root> <prompt>``.
    ``repo_root`` is the agent workspace root (Argus repo root or an Argus-root git worktree).

    Does **not** pass ``--mode=plan`` or ``--mode=ask`` (those are read-only per the Cursor agent CLI).
    """
    rr = str(repo_root.resolve())
    return [
        agent_cli,
        *extra_args,
        *AGENT_HEADLESS_INJECTED_ARGV,
        "--workspace",
        rr,
        prompt_text,
    ]


def _resolve_executable(cursor_cli: str) -> str | None:
    p = Path(cursor_cli)
    if p.is_file():
        return str(p.resolve())
    w = shutil.which(cursor_cli)
    return w


def run_builder_invoke(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
    prompt_path: Path | None = None,
    task_path: Path | None = None,
    prepare_first: bool = False,
    prepare_under: str = "product",
    execute: bool = False,
    explicit_dry_run: bool = False,
    no_record: bool = False,
    execution_backend: str = "cursor",
    agent_sandbox: str | None = None,
    allow_unsandboxed: bool = False,
    agent_network_mode: str | None = None,
) -> dict[str, Any]:
    """
    Resolve artifacts, optionally ``prepare_first``, optionally run a backend subprocess, write record.

    ``execution_backend``: ``cursor`` (open prompt file) or ``agent`` (headless ``-p`` JSON + ``--workspace``; file body as prompt).

    ``agent_sandbox``: ``auto`` (default), ``bwrap``, or ``none`` — outer containment for the agent backend only
    (bubblewrap when available). ``allow_unsandboxed``: opt out when bwrap is missing (degraded trust).

    ``agent_network_mode``: ``default`` (recommended), ``allow_all`` (explicit wide-open; degraded-trust flag),
    or ``disabled`` (``bwrap --unshare-net`` when sandboxed). CLI/env: ``--network``, ``ARGUS_BUILDER_NETWORK_MODE``.

    Returns an ``argus.builder_invoke_record.v1`` dict (also written to disk unless ``no_record``).
    """
    if prepare_first:
        write_prepare_artifacts(
            repo_root,
            product_id,
            under=prepare_under,
            products_dir=products_dir,
        )

    resolved = resolve_prepared_artifacts(
        repo_root,
        product_id,
        products_dir=products_dir,
        prompt_path=prompt_path,
        task_path=task_path,
    )
    task = _load_task_summary(resolved.task_json)
    rt = task.get("resolved_target")
    if not isinstance(rt, dict):
        rt = {}

    explicit_paths = prompt_path is not None or task_path is not None
    p_status, declared_d, _prepared_d, ne_rel = assess_prepared_contract_vs_declared(
        repo_root,
        product_id,
        task=task,
        products_dir=products_dir,
        explicit_paths=explicit_paths,
    )
    rt_block = {
        "id": rt.get("id"),
        "target_type": rt.get("target_type"),
        "group_id": rt.get("group_id"),
    }

    eb = (execution_backend or "cursor").strip().lower()
    if eb not in ("cursor", "agent"):
        raise BuilderInvokeError(f"Unknown execution backend {execution_backend!r} (use cursor or agent)")

    cursor_cli = resolve_cursor_cli()
    agent_cli = resolve_agent_cli()
    prompt_abs = resolved.prompt_md

    if eb == "agent":
        try:
            agent_extra = parse_agent_extra_args_from_env()
        except BuilderInvokeError:
            raise
        try:
            prompt_text = prompt_abs.read_text(encoding="utf-8")
        except OSError as e:
            raise BuilderInvokeError(f"Could not read prompt file {prompt_abs}: {e}") from e
    else:
        agent_extra = []
        prompt_text = ""

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    ensure_product_git_workspace(repo_root, product_id, products_dir=products_dir)
    ws = resolve_git_workspace(repo_root, product_id, products_dir=products_dir)

    fix_cmd = _suggest_prepare_command(product_id)
    stale_execute_blocked = (
        p_status == "stale" and execute and not prepare_first and not explicit_paths
    )

    permission_ev: dict[str, Any] | None = None
    permission_execute_blocked = False
    if execute and not stale_execute_blocked:
        permission_ev = evaluate_phase1_for_keys(
            repo_root,
            product_id,
            ("builder_execute",),
            products_dir=products_dir,
            check_environment=True,
            execution_path="builder_invoke",
            action_description=f"argus builder invoke --execute --backend {eb}",
        )
        permission_execute_blocked = permission_ev.get("execution_proceeds") is not True

    inc_id: str | None = None
    ect = task.get("execution_contract")
    if isinstance(ect, dict) and ect.get("increment_target_id"):
        inc_id = str(ect.get("increment_target_id")).strip() or None
    if not inc_id and rt.get("id"):
        inc_id = str(rt.get("id")).strip() or None

    git_branch_isolation = compute_git_branch_isolation(
        execute=execute,
        stale_execute_blocked=stale_execute_blocked,
        permission_execute_blocked=permission_execute_blocked,
        ws=ws,
        increment_id=inc_id,
        product_id=product_id,
    )

    ws_exec: dict[str, Any] = {**ws}
    wt_cleanup_path: Path | None = None
    if (
        execute
        and not stale_execute_blocked
        and not permission_execute_blocked
        and eb == "agent"
        and ws.get("kind") == "argus_root"
        and argus_root_worktree_enabled()
    ):
        wt_res = prepare_argus_root_worktree_isolation(
            repo_root,
            product_id,
            increment_id=inc_id,
        )
        st = str(wt_res.get("branch_isolation_status") or "")
        if st in ("ok", "degraded_dirty_tree"):
            wtp = wt_res.get("git_worktree_path")
            if wtp:
                wt_path = Path(str(wtp))
                sync_builder_artifacts_into_worktree(
                    repo_root,
                    wt_path,
                    product_id,
                    products_dir=products_dir,
                    artifact_source=resolved.source,
                )
                ws_exec = {
                    "kind": "argus_root_worktree",
                    "git_cwd": wt_path.resolve(),
                    "argus_path_prefix": "",
                }
                git_branch_isolation = wt_res
                wt_cleanup_path = wt_path.resolve()
        else:
            git_branch_isolation = {**git_branch_isolation, "argus_root_worktree_attempt": wt_res}

    bl_cap = capture_baseline_commit(ws_exec["git_cwd"])
    br_now, _ = read_git_branch(ws_exec["git_cwd"])
    git_baseline_payload: dict[str, Any] = {
        "schema": GIT_BASELINE_SCHEMA,
        "git_workspace_kind": ws_exec["kind"],
        "git_cwd": str(ws_exec["git_cwd"]),
        "argus_path_prefix": ws_exec["argus_path_prefix"],
        "baseline_commit": bl_cap.get("baseline_commit"),
        "working_tree_dirty_before": bl_cap.get("working_tree_dirty_before"),
        "capture_error": bl_cap.get("error"),
        "git_branch_at_baseline": br_now,
    }

    if eb == "agent":
        agent_workspace_root = (
            ws_exec["git_cwd"]
            if ws_exec.get("kind") == "argus_root_worktree"
            else repo_root
        )
        argv_planned = build_agent_argv(
            agent_cli=agent_cli,
            prompt_text=prompt_text,
            extra_args=agent_extra,
            repo_root=agent_workspace_root,
        )
    else:
        argv_planned = build_invoke_argv(cursor_cli=cursor_cli, prompt_abs=prompt_abs)

    if eb == "cursor":
        invocation_method = "cursor_cli_open_file"
    else:
        # Preview/review: same argv as execute would use; subprocess not launched unless execute.
        invocation_method = (
            "agent_cli_execute_mode" if execute else "agent_cli_execute_preview"
        )

    record: dict[str, Any] = {
        "schema": BUILDER_INVOKE_RECORD_SCHEMA,
        "product_id": product_id,
        "source_prompt_path": str(resolved.prompt_md),
        "source_task_path": str(resolved.task_json),
        "artifact_resolution": resolved.source,
        "source_next_expansion_hint": task.get("source_next_expansion_path"),
        "resolved_target": rt_block,
        "declared_target": declared_d,
        "prepared_target": rt_block,
        "prepared_contract_status": p_status,
        "next_expansion_path": ne_rel,
        "invoked_at_utc": now,
        "prepare_first": prepare_first,
        "prepare_under": prepare_under if prepare_first else None,
        "execution_backend": eb,
        "invocation_method": invocation_method,
        "cursor_cli": cursor_cli if eb == "cursor" else None,
        "agent_cli": agent_cli if eb == "agent" else None,
        "backend_extra_args": agent_extra if eb == "agent" else [],
        "backend_executable": None,
        "command": argv_planned,
        "working_directory": str(
            ws_exec["git_cwd"]
            if eb == "agent" and ws_exec.get("kind") == "argus_root_worktree"
            else repo_root
        ),
        "review_required": bool(task.get("review_required", True)),
        "git_baseline": git_baseline_payload,
        "git_branch_isolation": git_branch_isolation,
        "project_permission_decision": permission_ev if execute and not stale_execute_blocked else None,
    }
    ec_task = task.get("execution_contract")
    if isinstance(ec_task, dict):
        record["execution_contract_schema"] = ec_task.get("schema")
        record["execution_contract_kind"] = ec_task.get("contract_kind")
        record["increment_target_id"] = ec_task.get("increment_target_id")
    if eb == "agent":
        record["agent_cli_plan_mode"] = False
        record["agent_headless_execute_argv"] = True
        record["agent_note"] = (
            "Headless invoke: `agent ... -p --output-format json --workspace <repo> <prompt>`; "
            "no `--mode=plan`. Subprocess exit 0 does not assert task or filesystem success — "
            "verify repo state and reconcile/findings separately."
        )

    # Containment (agent: bubblewrap + stripped env; cursor: not_applicable).
    inner_for_sandbox: list[str]
    if eb == "agent":
        ra_pre = _resolve_executable(agent_cli)
        if not ra_pre:
            w = shutil.which(agent_cli)
            ra_pre = w if w else None
        inner_for_sandbox = list(argv_planned)
        if ra_pre:
            inner_for_sandbox[0] = ra_pre
    else:
        inner_for_sandbox = [cursor_cli, str(prompt_abs)]

    wk = ws_exec.get("kind")
    wt_for_scope: Path | None = None
    if wk == "argus_root_worktree":
        gcwd = ws_exec.get("git_cwd")
        if gcwd:
            wt_for_scope = Path(str(gcwd)).resolve()
    fs_mode, fs_reason, fs_extra_rw = infer_builder_filesystem_scope(
        repo_root,
        product_id,
        products_dir=products_dir,
        artifact_source=resolved.source,
        explicit_prompt_path=resolved.prompt_md if explicit_paths else None,
        explicit_task_path=resolved.task_json if explicit_paths else None,
        git_workspace_kind=wk if isinstance(wk, str) else None,
        worktree_host_path=wt_for_scope,
    )
    actx = build_agent_containment(
        repo_root=repo_root,
        product_id=product_id,
        execution_backend=eb,
        execute=execute,
        sandbox_cli=agent_sandbox,
        allow_unsandboxed_cli=allow_unsandboxed,
        inner_argv=inner_for_sandbox,
        products_dir=products_dir,
        filesystem_scope_mode=fs_mode,
        filesystem_scope_reason=fs_reason,
        extra_rw_paths=fs_extra_rw,
        git_workspace_kind=wk if isinstance(wk, str) and wk else None,
        network_mode_cli=agent_network_mode,
        worktree_host_path=wt_for_scope,
    )
    record["builder_containment"] = actx["builder_containment"]
    landlock_status_file = actx.get("landlock_status_file")

    if stale_execute_blocked:
        record["mode"] = "execute"
        record["invocation_status"] = "failed"
        record["exit_code"] = None
        record["error"] = (
            "Prepared contract is stale: content/next_expansion.json declares a different primary_target "
            f"than builder_task.json resolved_target. Run `{fix_cmd}` or re-run invoke with `--prepare-first` "
            "before `--execute`."
        )
        record["note"] = record["error"]
    elif not execute:
        record["mode"] = "dry_run" if explicit_dry_run else "review"
        record["invocation_status"] = "not_executed"
        record["exit_code"] = None
        record["error"] = None
        if eb == "cursor":
            record["note"] = (
                "No subprocess. Re-run with --execute --backend cursor to open the prompt in the IDE, "
                f"or open {BUILDER_PROMPT_FILENAME} manually."
            )
        else:
            record["note"] = (
                "No subprocess. Re-run with --execute --backend agent to run the headless agent "
                f"(`{agent_cli}` -p --output-format json --workspace <repo> <prompt from file>). "
                f"Override executable with {ENV_AGENT_PRIMARY}; optional args via {ENV_AGENT_EXTRA_ARGS}; "
                f"timeout via {ENV_AGENT_TIMEOUT}."
            )
        if p_status == "stale":
            record["prepared_contract_warning"] = (
                f"prepared_contract_status=stale: declared target in next_expansion.json does not match "
                f"builder_task.json. Suggested fix: `{fix_cmd}`."
            )
            record["note"] += "\n\n" + record["prepared_contract_warning"]
    else:
        record["mode"] = "execute"
        branch_failed = git_branch_isolation.get("branch_isolation_status") == "failed"
        containment_failed = bool(eb == "agent" and actx.get("error"))
        if permission_execute_blocked:
            record["invocation_status"] = "failed"
            record["exit_code"] = None
            pr = (
                str(permission_ev.get("reason") or "project permission gate blocked execution")
                if permission_ev
                else "project permission gate blocked execution"
            )
            record["error"] = pr
            record["note"] = pr
        elif branch_failed:
            record["invocation_status"] = "failed"
            record["exit_code"] = None
            record["error"] = (
                git_branch_isolation.get("branch_isolation_error") or "git branch isolation failed"
            )
            record["note"] = record["error"]
        elif containment_failed:
            record["invocation_status"] = "failed"
            record["exit_code"] = None
            record["error"] = actx.get("error")
            record["note"] = record["error"]
        else:
            if eb == "cursor":
                resolved_exe = _resolve_executable(cursor_cli)
                if resolved_exe is None:
                    record["invocation_status"] = "failed"
                    record["exit_code"] = None
                    record["backend_executable"] = cursor_cli
                    record["error"] = (
                        f"Executable not found: {cursor_cli!r} "
                        f"(set {ENV_CURSOR_PRIMARY} or {ENV_CURSOR_FALLBACK} to a full path, "
                        "or install the Cursor CLI on PATH)"
                    )
                else:
                    record["command"] = [resolved_exe, str(prompt_abs)]
                    record["backend_executable"] = resolved_exe
                    # Cursor opens whatever is on disk; stub-sized files are almost always a mistake
                    # (tests use a minimal "# p" placeholder — not a real prepare output).
                    try:
                        pt = prompt_abs.read_text(encoding="utf-8")
                        if len(pt.strip()) < 100:
                            record["prompt_file_warning"] = (
                                f"builder_next_prompt.md is only {len(pt)} bytes; "
                                f"expected output from `argus builder prepare {product_id}`. "
                                f"If you did not mean to open a stub, run `{fix_cmd}` then invoke again."
                            )
                    except OSError:
                        pass
                    try:
                        proc = subprocess.run(
                            [resolved_exe, str(prompt_abs)],
                            cwd=repo_root,
                            capture_output=True,
                            text=True,
                            timeout=DEFAULT_CURSOR_TIMEOUT,
                        )
                        record["exit_code"] = proc.returncode
                        record["invocation_status"] = "ok" if proc.returncode == 0 else "failed"
                        err_parts = []
                        if proc.stderr and proc.stderr.strip():
                            err_parts.append(proc.stderr.strip()[:4000])
                        if proc.stdout and proc.stdout.strip():
                            err_parts.append(proc.stdout.strip()[:2000])
                        record["error"] = "\n".join(err_parts) if err_parts else None
                        if proc.returncode != 0 and not record["error"]:
                            record["error"] = f"non-zero exit: {proc.returncode}"
                    except OSError as e:
                        record["invocation_status"] = "failed"
                        record["exit_code"] = None
                        record["error"] = str(e)
                    except subprocess.TimeoutExpired:
                        record["invocation_status"] = "failed"
                        record["exit_code"] = None
                        record["error"] = f"subprocess timeout ({DEFAULT_CURSOR_TIMEOUT}s)"
            elif eb == "agent":
                resolved_agent = _resolve_executable(agent_cli)
                record["backend_executable"] = agent_cli if resolved_agent is None else resolved_agent
                if resolved_agent is None:
                    record["invocation_status"] = "failed"
                    record["exit_code"] = None
                    record["error"] = (
                        f"Executable not found: {agent_cli!r} "
                        f"(set {ENV_AGENT_PRIMARY} to a full path or install the agent CLI on PATH)"
                    )
                    bc_ll = record.get("builder_containment")
                    if isinstance(bc_ll, dict) and landlock_status_file:
                        merge_landlock_status_into_meta(bc_ll, Path(landlock_status_file))
                else:
                    argv_run = list(actx["argv"])
                    record["command"] = argv_run
                    timeout_sec = resolve_agent_timeout_seconds()
                    try:
                        proc = subprocess.run(
                            argv_run,
                            cwd=(
                                ws_exec["git_cwd"]
                                if ws_exec.get("kind") == "argus_root_worktree"
                                else repo_root
                            ),
                            env=actx["subprocess_env"],
                            capture_output=True,
                            text=True,
                            timeout=timeout_sec,
                        )
                        record["exit_code"] = proc.returncode
                        record["invocation_status"] = "ok" if proc.returncode == 0 else "failed"
                        record["agent_stdout_summary"] = _truncate_for_record(proc.stdout or "")
                        record["agent_stderr_summary"] = _truncate_for_record(proc.stderr or "")
                        err_parts = []
                        if proc.stderr and proc.stderr.strip():
                            err_parts.append(proc.stderr.strip()[:4000])
                        if proc.stdout and proc.stdout.strip():
                            err_parts.append(proc.stdout.strip()[:2000])
                        record["error"] = "\n".join(err_parts) if err_parts else None
                        if proc.returncode != 0 and not record["error"]:
                            record["error"] = f"non-zero exit: {proc.returncode}"
                    except OSError as e:
                        record["invocation_status"] = "failed"
                        record["exit_code"] = None
                        record["error"] = str(e)
                    except subprocess.TimeoutExpired:
                        record["invocation_status"] = "failed"
                        record["exit_code"] = None
                        record["error"] = f"subprocess timeout ({timeout_sec}s)"
                    finally:
                        bc_ll = record.get("builder_containment")
                        if isinstance(bc_ll, dict) and landlock_status_file:
                            merge_landlock_status_into_meta(bc_ll, Path(landlock_status_file))
            else:
                record["invocation_status"] = "failed"
                record["exit_code"] = None
                record["error"] = f"Unknown execution backend {eb!r}"

    if (
        execute
        and not stale_execute_blocked
        and not permission_execute_blocked
        and git_branch_isolation.get("branch_isolation_status") != "failed"
        and record.get("git_baseline", {}).get("baseline_commit")
        and not record.get("git_baseline", {}).get("capture_error")
    ):
        gb = record["git_baseline"]
        record["post_invoke_git_diff"] = collect_diff_since_baseline(
            Path(gb["git_cwd"]),
            str(gb["baseline_commit"]).strip(),
            argus_path_prefix=str(gb.get("argus_path_prefix") or ""),
        )

    if wt_cleanup_path is not None:
        ok_rm, err_rm = remove_argus_root_worktree(repo_root, wt_cleanup_path)
        gbi = record.get("git_branch_isolation")
        if isinstance(gbi, dict):
            gbi["worktree_removed_after_invoke"] = ok_rm
            if not ok_rm:
                gbi["worktree_remove_error"] = err_rm

    if not no_record:
        out_dir = invoke_record_dir(repo_root, product_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        latest = out_dir / "latest.json"
        latest.write_text(dumps_json(record), encoding="utf-8")

    return record
