"""
Outer containment for Builder **agent** backend execution (bubblewrap + env stripping).

Cursor backend is unchanged (IDE handoff; containment ``not_applicable``).

Does **not** claim perfect isolation: bwrap reduces filesystem/credential exposure; operators should
still treat agent output as untrusted.
"""

from __future__ import annotations

import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any

from argus.builder.git_diff_capture import product_directory
from argus.builder.landlock_support import (
    derive_landlock_write_paths,
    landlock_requested_by_env,
    should_attempt_landlock_for_fs_mode,
)

BUILDER_CONTAINMENT_SCHEMA = "argus.builder.containment.v1"

# Filesystem scope for bubblewrap repo mounts (see builder_containment metadata).
FS_SCOPE_PRODUCT_SCOPED = "product_scoped"
FS_SCOPE_LEGACY_REPO_RW = "legacy_repo_rw"
# Argus-root git worktree: ro-bind main repo; rw-bind worktree checkout + main .git (metadata).
FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED = "argus_root_worktree_scoped"

# CLI / env: ARGUS_BUILDER_AGENT_SANDBOX=auto|bwrap|none
ENV_BUILDER_SANDBOX = "ARGUS_BUILDER_AGENT_SANDBOX"
ENV_ALLOW_UNSANDBOXED = "ARGUS_BUILDER_ALLOW_UNSANDBOXED"
# Network policy for agent bwrap: default | allow_all | disabled (see docs)
ENV_BUILDER_NETWORK_MODE = "ARGUS_BUILDER_NETWORK_MODE"

NETWORK_MODE_DEFAULT = "default"
NETWORK_MODE_ALLOW_ALL = "allow_all"
NETWORK_MODE_DISABLED = "disabled"
# Skip setpriv --no-new-privs (e.g. broken util-linux); not recommended.
ENV_SKIP_NO_NEW_PRIVS = "ARGUS_BUILDER_SKIP_NO_NEW_PRIVS"

SANDBOX_MODE_AUTO = "auto"
SANDBOX_MODE_BWRAP = "bwrap"
SANDBOX_MODE_NONE = "none"

APPLIED_BWRAP = "bwrap"
APPLIED_NONE = "none"
APPLIED_NOT_APPLICABLE = "not_applicable"
APPLIED_UNAVAILABLE = "unavailable"

# Strip these prefixes (case-sensitive, typical env conventions).
_STRIP_PREFIXES: tuple[str, ...] = (
    "AWS_",
    "AZURE_",
    "GCP_",
    "ALICLOUD_",
    "GITHUB_",
    "GITLAB_",
    "GITEA_",
    "GIT_",
    "SSH_",
    "KUBE",
    "KUBERNETES_",
    "GOOGLE_",
    "GCP_",
    "OPENAI_",
    "ANTHROPIC_",
    "COHERE_",
    "CLOUDSDK_",
    "DIGITALOCEAN_",
    "HEROKU_",
    "NETLIFY_",
    "VERCEL_",
    "DOCKER_",
    "CONTAINER_",
    "NPM_TOKEN_",
    "NODE_AUTH",
)

_STRIP_EXACT: frozenset[str] = frozenset(
    {
        "SSH_AUTH_SOCK",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "GITLAB_TOKEN",
        "NETLIFY_AUTH_TOKEN",
        "VERCEL_TOKEN",
        "AWS_SESSION_TOKEN",
        "AWS_ACCESS_KEY_ID",
        "AWS_SECRET_ACCESS_KEY",
        "CREDENTIALS_DIRECTORY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "ARGUS_AGENT_EXTRA_ARGS",  # could carry secrets; use explicit non-env config later
    }
)


def which_bwrap() -> str | None:
    return shutil.which("bwrap")


def which_setpriv() -> str | None:
    return shutil.which("setpriv")


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def wrap_argv_with_no_new_privs(argv: list[str], meta: dict[str, Any]) -> list[str]:
    """
    Prepend ``setpriv --no-new-privs --`` on Linux when available.

    Mutates ``meta`` with ``no_new_privs_*`` and optional ``trust_degraded_missing_no_new_privs``.
    """
    meta["no_new_privs_launcher"] = None
    meta["setpriv_path"] = None
    meta["trust_degraded_missing_no_new_privs"] = False

    if not _is_linux():
        meta["no_new_privs_requested"] = False
        meta["no_new_privs_applied"] = False
        meta["no_new_privs_reason"] = "non_linux_skip"
        return argv

    if (os.environ.get(ENV_SKIP_NO_NEW_PRIVS) or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        meta["no_new_privs_requested"] = False
        meta["no_new_privs_applied"] = False
        meta["no_new_privs_reason"] = "skipped_by_env_ARGUS_BUILDER_SKIP_NO_NEW_PRIVS"
        return argv

    sp = which_setpriv()
    meta["no_new_privs_requested"] = True
    if not sp:
        meta["no_new_privs_applied"] = False
        meta["no_new_privs_reason"] = "setpriv_not_found_on_path"
        meta["trust_degraded_missing_no_new_privs"] = True
        return argv

    meta["no_new_privs_applied"] = True
    meta["no_new_privs_launcher"] = "setpriv"
    meta["setpriv_path"] = sp
    meta["no_new_privs_reason"] = "setpriv_no_new_privs"
    return [sp, "--no-new-privs", "--", *argv]


def finalize_agent_containment_result(
    result: dict[str, Any],
    *,
    execution_backend: str,
    execute: bool,
) -> dict[str, Any]:
    """Apply no_new_privs wrapper when agent subprocess will actually run."""
    if execution_backend != "agent" or not execute or result.get("error"):
        return result
    bc = result.get("builder_containment")
    if not isinstance(bc, dict):
        return result
    result["argv"] = wrap_argv_with_no_new_privs(result["argv"], bc)
    return result


def _should_strip_key(k: str) -> bool:
    if k in _STRIP_EXACT:
        return True
    return any(k.startswith(p) for p in _STRIP_PREFIXES)


def strip_builder_agent_environment(
    base: dict[str, str] | None,
) -> tuple[dict[str, str], list[str], int]:
    """
    Return ``(env, stripped_key_names, stripped_count)`` for the agent child.

    Drops almost all ambient keys; keeps a small non-credential allow-list. Does not log values.
    """
    if not base:
        base = {}
    allow_optional = frozenset(
        {
            "PATH",
            "HOME",
            "USER",
            "LOGNAME",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TERM",
            "TZ",
            "TMPDIR",
        }
    )
    out: dict[str, str] = {}
    stripped_names: list[str] = []
    stripped = 0
    for k, v in base.items():
        if _should_strip_key(k):
            stripped += 1
            stripped_names.append(k)
            continue
        if k in allow_optional:
            out[k] = v
            continue
        stripped += 1
        stripped_names.append(k)
    out.setdefault("PATH", "/usr/bin:/bin")
    out.setdefault("LANG", "C.UTF-8")
    out.setdefault("LC_ALL", "C.UTF-8")
    return out, stripped_names[:64], stripped


def _standard_ro_bind_args() -> list[str]:
    args: list[str] = []
    for p in ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/opt"):
        if Path(p).is_dir():
            args.extend(["--ro-bind", p, p])
    return args


def _etc_bind_args() -> list[str]:
    args: list[str] = []
    for name in ("resolv.conf", "nsswitch.conf", "hosts"):
        ep = Path("/etc") / name
        if ep.is_file():
            args.extend(["--ro-bind", str(ep), str("/etc/" + name)])
    return args


def infer_builder_filesystem_scope(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None,
    artifact_source: str,
    explicit_prompt_path: Path | None,
    explicit_task_path: Path | None,
    git_workspace_kind: str | None = None,
    worktree_host_path: Path | None = None,
) -> tuple[str, str | None, list[Path]]:
    """
    Decide bubblewrap mount strategy.

    **Nested product** (``products/<id>/.git``): ``product_scoped`` — ro repo + rw product subtree
    (+ optional prepare path).

    **Plain Argus root** (no nested git): ``legacy_repo_rw`` — full repo rw in bwrap; records
    ``trust_degraded_workspace_scope``.

    **Argus-root worktree** (``git worktree`` isolation): prefer ``argus_root_worktree_scoped`` —
    ro main repo + rw worktree checkout + rw main ``.git/`` for git metadata. Falls back to
    ``legacy_repo_rw`` with an honest reason when the worktree path is missing or not under the repo.

    Returns ``(mode, reason, extra_rw_paths)``. ``extra_rw_paths`` are additional rw overlays for
    ``product_scoped`` only; worktree-scoped mode uses an empty list (prepare artifacts are mirrored
    into the worktree before execute).
    """
    rr = repo_root.resolve()
    pd = product_directory(repo_root, product_id, products_dir)

    extra_rw: list[Path] = []
    if artifact_source == "runs_prepare":
        extra_rw.append((rr / "runs" / "builder" / "prepare" / product_id).resolve())

    if artifact_source == "explicit":
        if explicit_prompt_path is None or explicit_task_path is None:
            return FS_SCOPE_LEGACY_REPO_RW, "explicit_paths_incomplete", []
        try:
            explicit_prompt_path.resolve().relative_to(pd)
            explicit_task_path.resolve().relative_to(pd)
        except ValueError:
            return (
                FS_SCOPE_LEGACY_REPO_RW,
                "explicit_paths_not_under_product_directory",
                [],
            )

    if git_workspace_kind == "argus_root_worktree":
        if worktree_host_path is not None:
            try:
                worktree_host_path.resolve().relative_to(rr)
            except ValueError:
                return (
                    FS_SCOPE_LEGACY_REPO_RW,
                    "argus_root_worktree_path_not_under_repo_root",
                    extra_rw,
                )
            return FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED, None, []
        return (
            FS_SCOPE_LEGACY_REPO_RW,
            "argus_root_worktree_missing_worktree_path",
            extra_rw,
        )

    if git_workspace_kind == "argus_root":
        return (
            FS_SCOPE_LEGACY_REPO_RW,
            "argus_root_workspace_not_product_scoped_filesystem",
            extra_rw,
        )

    return FS_SCOPE_PRODUCT_SCOPED, None, extra_rw


def _bind_parent_if_needed(agent_exe: Path) -> list[str]:
    """If agent is outside standard trees, ro-bind its parent directory."""
    try:
        resolved = agent_exe.resolve()
    except OSError:
        return []
    s = str(resolved)
    for prefix in ("/usr/", "/opt/", "/bin/", "/sbin/"):
        if s.startswith(prefix):
            return []
    parent = resolved.parent
    if parent.is_dir():
        ps = str(parent)
        return ["--ro-bind", ps, ps]
    return []


def _sorted_rw_overlay_paths(paths: list[Path]) -> list[Path]:
    """Deduplicate and order paths longest-first so nested mounts override parents consistently."""
    seen: set[str] = set()
    resolved = [p.resolve() for p in paths]
    resolved.sort(key=lambda p: len(str(p)), reverse=True)
    out: list[Path] = []
    for p in resolved:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        out.append(p)
    return out


def build_bwrap_argv(
    *,
    bwrap_exe: str,
    repo_root: Path,
    product_id: str,
    inner_argv: list[str],
    env: dict[str, str],
    scratch_root: Path,
    filesystem_scope_mode: str = FS_SCOPE_PRODUCT_SCOPED,
    products_dir: Path | None = None,
    extra_rw_paths: list[Path] | None = None,
    git_workspace_kind: str | None = None,
    network_mode: str = NETWORK_MODE_DEFAULT,
    worktree_root: Path | None = None,
    agent_executable_for_binds: Path | None = None,
    extra_inner_env: dict[str, str] | None = None,
) -> list[str]:
    """
    Wrap ``inner_argv`` (first element = absolute agent path) in bubblewrap.

    ``filesystem_scope_mode``:

    - ``product_scoped`` — mount repo root read-only, then read-write overlays for the managed
      product subtree, scratch HOME, optional prepare subtree, and (when git workspace is the Argus
      root) ``.git`` as a directory only.
    - ``legacy_repo_rw`` — mount entire ``repo_root`` read-write (older / explicit-path behavior).
    - ``argus_root_worktree_scoped`` — ro main ``repo_root``; rw ``worktree_root`` (must be under
      ``repo_root``) and rw main ``.git/`` for linked-worktree metadata; chdir to worktree.

    ``network_mode``:

    - ``default`` / ``allow_all`` — host network namespace in the sandbox (no extra bwrap flags).
    - ``disabled`` — append ``--unshare-net`` (may break agent features that need outbound access).
    """
    rr_path = repo_root.resolve()
    rr = str(rr_path)
    if not inner_argv or not inner_argv[0]:
        raise ValueError("inner_argv must be non-empty")
    bind_src = agent_executable_for_binds if agent_executable_for_binds is not None else Path(inner_argv[0])
    agent_exe = bind_src
    scratch = (scratch_root / "home" / product_id).resolve()
    scratch.mkdir(parents=True, exist_ok=True)

    product_rw = product_directory(repo_root, product_id, products_dir)
    product_rw.mkdir(parents=True, exist_ok=True)

    out: list[str] = [
        bwrap_exe,
        "--die-with-parent",
        "--unshare-pid",
    ]
    if network_mode == NETWORK_MODE_DISABLED:
        out.append("--unshare-net")
    out.extend(
        [
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
        ]
    )
    out.extend(_standard_ro_bind_args())
    out.extend(_etc_bind_args())
    out.extend(_bind_parent_if_needed(agent_exe))

    chdir_path = rr

    if filesystem_scope_mode == FS_SCOPE_LEGACY_REPO_RW:
        out.extend(["--bind", rr, rr])
    elif filesystem_scope_mode == FS_SCOPE_PRODUCT_SCOPED:
        out.extend(["--ro-bind", rr, rr])
        rw_paths: list[Path] = [scratch, product_rw]
        if extra_rw_paths:
            for p in extra_rw_paths:
                p2 = p.resolve()
                try:
                    p2.relative_to(rr_path)
                except ValueError as e:
                    raise ValueError(f"extra_rw_path not under repo_root: {p2}") from e
                p2.mkdir(parents=True, exist_ok=True)
                rw_paths.append(p2)
        if git_workspace_kind == "argus_root":
            git_meta = rr_path / ".git"
            if git_meta.is_dir():
                rw_paths.append(git_meta.resolve())
            elif git_meta.is_file():
                raise ValueError(
                    "product_scoped_sandbox_unsupported: Argus repo uses a .git file (worktree); "
                    "use nested product git under products/<id>/.git, or run with --allow-unsandboxed."
                )
        for p in _sorted_rw_overlay_paths(rw_paths):
            ps = str(p)
            out.extend(["--bind", ps, ps])
    elif filesystem_scope_mode == FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED:
        if worktree_root is None:
            raise ValueError("worktree_root is required for argus_root_worktree_scoped")
        wt = worktree_root.resolve()
        try:
            wt.relative_to(rr_path)
        except ValueError as e:
            raise ValueError(f"worktree_root must be under repo_root: {wt}") from e
        out.extend(["--ro-bind", rr, rr])
        rw_paths_wt: list[Path] = [scratch, wt]
        git_meta = rr_path / ".git"
        if git_meta.is_dir():
            rw_paths_wt.append(git_meta.resolve())
        elif git_meta.is_file():
            raise ValueError(
                "argus_root_worktree_scoped_unsupported: Argus repo .git is a file (linked worktree); "
                "use nested product git under products/<id>/.git, or run with --allow-unsandboxed."
            )
        if extra_rw_paths:
            for p in extra_rw_paths:
                p2 = p.resolve()
                try:
                    p2.relative_to(rr_path)
                except ValueError as e:
                    raise ValueError(f"extra_rw_path not under repo_root: {p2}") from e
                p2.mkdir(parents=True, exist_ok=True)
                rw_paths_wt.append(p2)
        for p in _sorted_rw_overlay_paths(rw_paths_wt):
            ps = str(p)
            out.extend(["--bind", ps, ps])
        chdir_path = str(wt)
    else:
        raise ValueError(f"unknown filesystem_scope_mode: {filesystem_scope_mode!r}")

    out.extend(["--bind", str(scratch), "/home/argus"])
    out.append("--chdir")
    out.append(chdir_path)

    # Minimal env inside sandbox (bwrap --setenv); omit ambient HOME from parent.
    inner_env = dict(env)
    inner_env["HOME"] = "/home/argus"
    inner_env["TMPDIR"] = "/tmp"
    try:
        ap_dir = str(agent_exe.resolve().parent)
    except OSError:
        ap_dir = "/usr/bin"
    inner_env["PATH"] = f"{ap_dir}:/usr/bin:/bin:/usr/local/bin"
    if extra_inner_env:
        inner_env.update(extra_inner_env)

    for k, v in sorted(inner_env.items()):
        out.extend(["--setenv", k, v])

    out.append("--")
    out.extend(inner_argv)
    return out


def resolve_sandbox_mode_from_env() -> str | None:
    raw = (os.environ.get(ENV_BUILDER_SANDBOX) or "").strip().lower()
    if not raw:
        return None
    if raw in (SANDBOX_MODE_AUTO, SANDBOX_MODE_BWRAP, SANDBOX_MODE_NONE):
        return raw
    return None


def allow_unsandboxed_from_env() -> bool:
    v = (os.environ.get(ENV_ALLOW_UNSANDBOXED) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def resolve_network_mode_from_env() -> str | None:
    raw = (os.environ.get(ENV_BUILDER_NETWORK_MODE) or "").strip().lower()
    if not raw:
        return None
    if raw in (NETWORK_MODE_DEFAULT, NETWORK_MODE_ALLOW_ALL, NETWORK_MODE_DISABLED):
        return raw
    return None


def resolve_effective_network_mode(
    cli: str | None,
    *,
    env_value: str | None = None,
) -> str:
    """
    CLI wins over ``env_value`` (or process env if omitted); when both absent, ``default``.

    Invalid values fall back to ``default`` (lenient; not a hard error).
    """
    if cli is not None and str(cli).strip():
        raw = str(cli).strip().lower()
    else:
        raw = (env_value if env_value is not None else os.environ.get(ENV_BUILDER_NETWORK_MODE)) or ""
        raw = raw.strip().lower()
    if not raw:
        return NETWORK_MODE_DEFAULT
    if raw in (NETWORK_MODE_DEFAULT, NETWORK_MODE_ALLOW_ALL, NETWORK_MODE_DISABLED):
        return raw
    return NETWORK_MODE_DEFAULT


def build_agent_containment(
    *,
    repo_root: Path,
    product_id: str,
    execution_backend: str,
    execute: bool,
    sandbox_cli: str | None,
    allow_unsandboxed_cli: bool,
    inner_argv: list[str],
    products_dir: Path | None = None,
    filesystem_scope_mode: str = FS_SCOPE_PRODUCT_SCOPED,
    filesystem_scope_reason: str | None = None,
    extra_rw_paths: list[Path] | None = None,
    git_workspace_kind: str | None = None,
    network_mode_cli: str | None = None,
    worktree_host_path: Path | None = None,
) -> dict[str, Any]:
    """
    Decide argv/env/containment record for agent subprocess.

    Returns a dict with keys:
    - ``argv`` — list to pass to ``subprocess``
    - ``env`` — environment dict
    - ``builder_containment`` — artifact payload
    - ``error`` — if set, invoke must fail without running the agent
    """
    rr = repo_root.resolve()
    scratch_root = rr / "runs" / "builder" / ".sandbox"
    product_rw_path = product_directory(repo_root, product_id, products_dir)

    base_env = dict(os.environ)
    env_sanitized, stripped_names, stripped_count = strip_builder_agent_environment(base_env)
    bwrap = which_bwrap()

    req = (sandbox_cli or resolve_sandbox_mode_from_env() or SANDBOX_MODE_AUTO).strip().lower()
    if req not in (SANDBOX_MODE_AUTO, SANDBOX_MODE_BWRAP, SANDBOX_MODE_NONE):
        req = SANDBOX_MODE_AUTO

    allow_explicit = bool(allow_unsandboxed_cli) or allow_unsandboxed_from_env()

    effective_nm = resolve_effective_network_mode(
        network_mode_cli,
        env_value=os.environ.get(ENV_BUILDER_NETWORK_MODE),
    )

    minimal_host_env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }

    meta: dict[str, Any] = {
        "schema": BUILDER_CONTAINMENT_SCHEMA,
        "containment_requested": req,
        "containment_applied": APPLIED_NOT_APPLICABLE,
        "containment_fallback_used": False,
        "containment_reason": None,
        "trust_degraded_unsandboxed": False,
        "sanitized_env_stripped_count": stripped_count,
        "sanitized_env_stripped_keys_sample": stripped_names[:24],
        "bwrap_path": bwrap,
        "no_new_privs_requested": False,
        "no_new_privs_applied": False,
        "no_new_privs_launcher": None,
        "setpriv_path": None,
        "no_new_privs_reason": None,
        "trust_degraded_missing_no_new_privs": False,
        "trust_degraded_workspace_scope": False,
        "filesystem_scope_mode": None,
        "filesystem_scope_reason": None,
        "repo_root_mount_mode": None,
        "product_mount_mode": None,
        "product_rw_path": None,
        "extra_rw_paths": None,
        "network_mode": effective_nm,
        "network_applied": None,
        "network_reason": None,
        "trust_degraded_network_open": False,
        "worktree_rw_path": None,
        "git_dir_mount_mode": None,
        "landlock_requested": False,
        "landlock_applied": None,
        "landlock_reason": None,
        "trust_degraded_missing_landlock": False,
        "landlock_skipped_reason": None,
        "landlock_allowed_write_paths_summary": None,
    }

    def _apply_fs_meta_for_agent_execute() -> None:
        meta["filesystem_scope_mode"] = filesystem_scope_mode
        meta["filesystem_scope_reason"] = filesystem_scope_reason
        meta["product_rw_path"] = str(product_rw_path)
        meta["trust_degraded_workspace_scope"] = git_workspace_kind == "argus_root"
        if filesystem_scope_mode == FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED and worktree_host_path:
            meta["worktree_rw_path"] = str(worktree_host_path.resolve())
            git_p = repo_root.resolve() / ".git"
            meta["git_dir_mount_mode"] = "read_write" if git_p.is_dir() else None
        if extra_rw_paths:
            meta["extra_rw_paths"] = [str(p.resolve()) for p in extra_rw_paths]
        meta["trust_degraded_network_open"] = (
            effective_nm == NETWORK_MODE_ALLOW_ALL and execution_backend == "agent" and execute
        )

    if execution_backend != "agent" or not execute:
        meta["containment_reason"] = "cursor_backend_or_review_only"
        meta["no_new_privs_reason"] = "not_agent_execute"
        meta["network_applied"] = None
        meta["network_reason"] = (
            "cursor_backend_network_not_managed_by_builder_containment"
            if execution_backend != "agent"
            else "agent_review_only_network_policy_not_applied"
        )
        meta["trust_degraded_network_open"] = False
        return finalize_agent_containment_result(
            {
                "argv": inner_argv,
                "env": env_sanitized,
                "subprocess_env": env_sanitized,
                "builder_containment": meta,
                "error": None,
            },
            execution_backend=execution_backend,
            execute=execute,
        )

    _apply_fs_meta_for_agent_execute()

    want_bwrap = req == SANDBOX_MODE_BWRAP or req == SANDBOX_MODE_AUTO

    if req == SANDBOX_MODE_NONE:
        meta["containment_applied"] = APPLIED_NONE
        meta["containment_reason"] = "explicit_sandbox_none"
        meta["trust_degraded_unsandboxed"] = True
        meta["repo_root_mount_mode"] = "unsandboxed_host"
        meta["product_mount_mode"] = "unsandboxed_host"
        meta["network_applied"] = False
        meta["network_reason"] = "unsandboxed_explicit_sandbox_none_network_not_enforced"
        return finalize_agent_containment_result(
            {
                "argv": inner_argv,
                "env": env_sanitized,
                "subprocess_env": env_sanitized,
                "builder_containment": meta,
                "error": None,
            },
            execution_backend=execution_backend,
            execute=execute,
        )

    if want_bwrap and bwrap:
        landlock_status_path: Path | None = None
        landlock_inner = list(inner_argv)
        landlock_extra_env: dict[str, str] | None = None
        agent_executable_for_binds: Path | None = None
        try:
            agent_executable_for_binds = Path(inner_argv[0]).resolve()
        except OSError:
            agent_executable_for_binds = Path(inner_argv[0])

        use_landlock = (
            _is_linux()
            and landlock_requested_by_env()
            and should_attempt_landlock_for_fs_mode(filesystem_scope_mode)
        )
        if use_landlock:
            wl = derive_landlock_write_paths(
                repo_root=rr,
                product_id=product_id,
                scratch_root=scratch_root,
                filesystem_scope_mode=filesystem_scope_mode,
                products_dir=products_dir,
                extra_rw_paths=extra_rw_paths,
                worktree_host_path=worktree_host_path,
            )
            landlock_status_path = (
                scratch_root / ".landlock" / f"{product_id}_{uuid.uuid4().hex[:12]}.json"
            )
            landlock_status_path.parent.mkdir(parents=True, exist_ok=True)
            landlock_extra_env = {
                "ARGUS_BUILDER_LANDLOCK_WRITE_PATHS": ":".join(str(p) for p in wl),
                "ARGUS_BUILDER_LANDLOCK_STATUS_FILE": str(landlock_status_path),
            }
            landlock_inner = [
                sys.executable,
                "-m",
                "argus.builder.landlock_launcher",
                "--",
                *inner_argv,
            ]
            meta["landlock_requested"] = True
            meta["landlock_allowed_write_paths_summary"] = [str(x) for x in wl[:48]]
        elif _is_linux() and not landlock_requested_by_env():
            meta["landlock_skipped_reason"] = "disabled_by_ARGUS_BUILDER_LANDLOCK"
        elif _is_linux() and not should_attempt_landlock_for_fs_mode(filesystem_scope_mode):
            meta["landlock_skipped_reason"] = "legacy_repo_rw_scope_bypasses_landlock"
        elif not _is_linux():
            meta["landlock_skipped_reason"] = "non_linux_skip"

        try:
            argv = build_bwrap_argv(
                bwrap_exe=bwrap,
                repo_root=rr,
                product_id=product_id,
                inner_argv=landlock_inner,
                env=env_sanitized,
                scratch_root=scratch_root,
                filesystem_scope_mode=filesystem_scope_mode,
                products_dir=products_dir,
                extra_rw_paths=extra_rw_paths,
                git_workspace_kind=git_workspace_kind,
                network_mode=effective_nm,
                worktree_root=worktree_host_path
                if filesystem_scope_mode == FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED
                else None,
                agent_executable_for_binds=agent_executable_for_binds,
                extra_inner_env=landlock_extra_env,
            )
        except (OSError, ValueError) as e:
            meta["containment_applied"] = APPLIED_UNAVAILABLE
            meta["containment_reason"] = f"bwrap_argv_build_failed:{e}"
            meta["network_applied"] = False
            meta["network_reason"] = "bwrap_argv_build_failed_network_policy_not_applied"
            if not allow_explicit:
                meta["no_new_privs_reason"] = "invoke_error_no_subprocess"
                return {
                    "argv": inner_argv,
                    "env": env_sanitized,
                    "subprocess_env": env_sanitized,
                    "builder_containment": meta,
                    "error": (
                        "Builder agent sandbox (bubblewrap) could not be constructed. "
                        f"Fix the error above, install bubblewrap, or re-run with "
                        f"--allow-unsandboxed (degraded trust). Detail: {e}"
                    ),
                }
            meta["containment_fallback_used"] = True
            meta["containment_applied"] = APPLIED_NONE
            meta["trust_degraded_unsandboxed"] = True
            meta["repo_root_mount_mode"] = "unsandboxed_host"
            meta["product_mount_mode"] = "unsandboxed_host"
            meta["network_applied"] = False
            meta["network_reason"] = "unsandboxed_fallback_network_policy_not_enforced_by_bwrap"
            return finalize_agent_containment_result(
                {
                    "argv": inner_argv,
                    "env": env_sanitized,
                    "subprocess_env": env_sanitized,
                    "builder_containment": meta,
                    "error": None,
                },
                execution_backend=execution_backend,
                execute=execute,
            )

        meta["containment_applied"] = APPLIED_BWRAP
        if filesystem_scope_mode == FS_SCOPE_PRODUCT_SCOPED:
            meta["containment_reason"] = "bubblewrap_outer_sandbox_product_scoped_fs"
            meta["repo_root_mount_mode"] = "read_only"
            meta["product_mount_mode"] = "read_write"
        elif filesystem_scope_mode == FS_SCOPE_ARGUS_ROOT_WORKTREE_SCOPED:
            meta["containment_reason"] = "bubblewrap_outer_sandbox_argus_root_worktree_scoped_fs"
            meta["repo_root_mount_mode"] = "read_only"
            meta["product_mount_mode"] = "read_write_within_worktree_checkout"
        else:
            meta["containment_reason"] = "bubblewrap_outer_sandbox_legacy_repo_rw"
            meta["repo_root_mount_mode"] = "read_write"
            meta["product_mount_mode"] = "read_write"
        meta["network_applied"] = True
        meta["network_reason"] = (
            "bubblewrap_unshare_net" if effective_nm == NETWORK_MODE_DISABLED else None
        )
        return finalize_agent_containment_result(
            {
                "argv": argv,
                "env": env_sanitized,
                "subprocess_env": minimal_host_env,
                "builder_containment": meta,
                "error": None,
                "landlock_status_file": landlock_status_path,
            },
            execution_backend=execution_backend,
            execute=execute,
        )

    # want bwrap but missing
    meta["containment_applied"] = APPLIED_UNAVAILABLE
    meta["containment_reason"] = "bwrap_not_found_on_path"
    meta["network_applied"] = False
    meta["network_reason"] = "bwrap_missing_network_policy_not_enforced"
    if not allow_explicit:
        meta["no_new_privs_reason"] = "invoke_error_no_subprocess"
        return {
            "argv": inner_argv,
            "env": env_sanitized,
            "subprocess_env": env_sanitized,
            "builder_containment": meta,
            "error": (
                "Builder agent execution requires bubblewrap (bwrap) on PATH for sandboxed runs "
                f"(requested={req!r}). Install bubblewrap or set {ENV_ALLOW_UNSANDBOXED}=1 "
                "or pass --allow-unsandboxed (degraded trust; not recommended)."
            ),
        }
    meta["containment_fallback_used"] = True
    meta["containment_applied"] = APPLIED_NONE
    meta["trust_degraded_unsandboxed"] = True
    meta["containment_reason"] = "bwrap_unavailable_allow_unsandboxed"
    meta["repo_root_mount_mode"] = "unsandboxed_host"
    meta["product_mount_mode"] = "unsandboxed_host"
    meta["network_applied"] = False
    meta["network_reason"] = "unsandboxed_fallback_network_policy_not_enforced_by_bwrap"
    return finalize_agent_containment_result(
        {
            "argv": inner_argv,
            "env": env_sanitized,
            "subprocess_env": env_sanitized,
            "builder_containment": meta,
            "error": None,
        },
        execution_backend=execution_backend,
        execute=execute,
    )
