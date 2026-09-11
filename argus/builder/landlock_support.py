"""
Linux Landlock helpers for Builder agent defense-in-depth (write-focused).

Uses libc ``syscall`` + kernel uapi structs. Optional: disabled via ``ARGUS_BUILDER_LANDLOCK=0``.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any

# --- Kernel uapi (Linux 5.13+ landlock syscalls; access bits from linux/landlock.h) ---

LANDLOCK_RULE_PATH_BENEATH = 1

LANDLOCK_CREATE_RULESET_VERSION = 1 << 0

# Filesystem access rights we subject to Landlock (deny-by-default for these; reads not listed).
LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
LANDLOCK_ACCESS_FS_REFER = 1 << 13
LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14

LANDLOCK_HANDLED_WRITES = (
    LANDLOCK_ACCESS_FS_WRITE_FILE
    | LANDLOCK_ACCESS_FS_REMOVE_DIR
    | LANDLOCK_ACCESS_FS_REMOVE_FILE
    | LANDLOCK_ACCESS_FS_MAKE_CHAR
    | LANDLOCK_ACCESS_FS_MAKE_DIR
    | LANDLOCK_ACCESS_FS_MAKE_REG
    | LANDLOCK_ACCESS_FS_MAKE_SOCK
    | LANDLOCK_ACCESS_FS_MAKE_FIFO
    | LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | LANDLOCK_ACCESS_FS_MAKE_SYM
    | LANDLOCK_ACCESS_FS_REFER
    | LANDLOCK_ACCESS_FS_TRUNCATE
)

PR_SET_NO_NEW_PRIVS = 38


def _landlock_syscall_numbers() -> tuple[int, int, int] | None:
    machine = platform.machine().lower()
    # asm-generic / most archs use 444–446 for landlock on Linux ≥5.13
    if machine in ("x86_64", "amd64"):
        return (444, 445, 446)
    if machine in ("aarch64", "arm64"):
        return (444, 445, 446)
    if machine == "riscv64":
        return (444, 445, 446)
    return None


def _libc() -> ctypes.CDLL:
    return ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)


def _get_errno() -> int:
    """Read C errno after a failed libc call."""
    try:
        fn = _libc().__errno_location
        fn.restype = ctypes.POINTER(ctypes.c_int)
        return int(fn().contents.value)
    except (AttributeError, ctypes.ArgumentError, ValueError):
        return errno.EINVAL


def _syscall(nr: int, *args: int) -> int:
    """``syscall`` with up to six ``long`` arguments (Linux convention)."""
    libc = _libc()
    fn = libc.syscall
    fn.restype = ctypes.c_long
    a = list(args) + [0] * (6 - len(args))
    return int(
        fn(
            ctypes.c_long(nr),
            ctypes.c_ulong(a[0]),
            ctypes.c_ulong(a[1]),
            ctypes.c_ulong(a[2]),
            ctypes.c_ulong(a[3]),
            ctypes.c_ulong(a[4]),
            ctypes.c_ulong(a[5]),
        )
    )


def landlock_sysctl_enabled() -> bool:
    """False only when the kernel explicitly reports Landlock disabled (``0``)."""
    p = Path("/sys/kernel/security/landlock.enabled")
    try:
        t = p.read_text(encoding="utf-8").strip()
    except OSError:
        return True
    if t == "0":
        return False
    return True


def landlock_requested_by_env() -> bool:
    v = (os.environ.get("ARGUS_BUILDER_LANDLOCK") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def should_attempt_landlock_for_fs_mode(filesystem_scope_mode: str) -> bool:
    """``legacy_repo_rw`` skips Landlock wrapper (same blast radius as mounts; honest skip)."""
    if filesystem_scope_mode == "legacy_repo_rw":
        return False
    return True


def derive_landlock_write_paths(
    *,
    repo_root: Path,
    product_id: str,
    scratch_root: Path,
    filesystem_scope_mode: str,
    products_dir: Path | None,
    extra_rw_paths: list[Path] | None,
    worktree_host_path: Path | None,
) -> list[Path]:
    """Absolute host paths that must allow writes for the agent (Landlock allowlist)."""
    rr = repo_root.resolve()
    out: list[Path] = []
    scratch_home = (scratch_root / "home" / product_id).resolve()
    out.append(scratch_home)
    out.append(Path("/tmp").resolve())

    from argus.builder.git_diff_capture import product_directory

    pd = product_directory(repo_root, product_id, products_dir)

    if filesystem_scope_mode == "product_scoped":
        out.append(pd.resolve())
        if extra_rw_paths:
            for p in extra_rw_paths:
                out.append(p.resolve())
    elif filesystem_scope_mode == "argus_root_worktree_scoped":
        if worktree_host_path:
            out.append(worktree_host_path.resolve())
        git_p = rr / ".git"
        if git_p.is_dir():
            out.append(git_p.resolve())
    elif filesystem_scope_mode == "legacy_repo_rw":
        out.append(rr)
    # dedupe preserving order
    seen: set[str] = set()
    uniq: list[Path] = []
    for p in out:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        uniq.append(p)
    return uniq


class LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
    ]


class LandlockPathBeneathAttr(ctypes.Structure):
    _pack_ = 1
    _fields_ = [
        ("allowed_access", ctypes.c_uint64),
        ("parent_fd", ctypes.c_int32),
        ("_padding", ctypes.c_uint32),
    ]


def apply_landlock_write_allowlist(paths: list[str]) -> tuple[bool, str | None]:
    """
    Apply Landlock ruleset restricting *handled* write-like filesystem operations to ``paths``.

    Returns ``(ok, error_detail)``. On failure, callers may still exec the agent without Landlock.
    """
    if not sys.platform.startswith("linux"):
        return False, "non_linux"
    nums = _landlock_syscall_numbers()
    if nums is None:
        return False, f"unsupported_machine:{platform.machine()}"
    nr_create, nr_add, nr_restrict = nums
    if not landlock_sysctl_enabled():
        return False, "landlock_disabled_in_kernel"

    libc = _libc()
    prctl = libc.prctl
    prctl.argtypes = (ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong)
    prctl.restype = ctypes.c_int
    # Defensive: ensure no_new_privs (required for landlock_restrict_self)
    try:
        if prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            err = _get_errno()
            return False, f"prctl_no_new_privs_failed:{errno.errorcode.get(err, err)}"
    except OSError as e:
        return False, f"prctl_error:{e}"

    abi = _syscall(nr_create, 0, 0, LANDLOCK_CREATE_RULESET_VERSION)
    if abi < 0:
        err = _get_errno()
        return False, f"landlock_version_probe:{errno.errorcode.get(err, err)}"
    if abi < 1:
        return False, f"landlock_abi_too_old:{abi}"

    attr = LandlockRulesetAttr(
        handled_access_fs=LANDLOCK_HANDLED_WRITES,
        handled_access_net=0,
    )
    rs_fd = _syscall(
        nr_create,
        ctypes.addressof(attr),
        ctypes.sizeof(attr),
        0,
    )
    if rs_fd < 0:
        # Retry older kernels (single-field ruleset attr)
        attr_old = (ctypes.c_uint64 * 1)(LANDLOCK_HANDLED_WRITES)
        rs_fd = _syscall(nr_create, ctypes.addressof(attr_old), 8, 0)
        if rs_fd < 0:
            err = _get_errno()
            return False, f"landlock_create_ruleset:{errno.errorcode.get(err, err)}"

    mask = LANDLOCK_HANDLED_WRITES
    for raw in paths:
        p = Path(raw)
        try:
            p = p.resolve()
        except OSError as e:
            os.close(rs_fd)
            return False, f"path_resolve:{raw}:{e}"
        if not p.exists():
            os.close(rs_fd)
            return False, f"path_missing:{p}"
        try:
            fd = os.open(os.fsdecode(p), os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
        except OSError:
            try:
                fd = os.open(os.fsdecode(p), os.O_PATH | os.O_CLOEXEC)
            except OSError as e:
                os.close(rs_fd)
                return False, f"open_path:{p}:{e}"

        beneath = LandlockPathBeneathAttr(allowed_access=mask, parent_fd=fd)
        # syscall(landlock_add_rule, ruleset_fd, rule_type, rule_attr*, flags)
        added = _syscall(
            nr_add,
            rs_fd,
            LANDLOCK_RULE_PATH_BENEATH,
            ctypes.addressof(beneath),
            0,
        )
        os.close(fd)
        if added < 0:
            err = _get_errno()
            os.close(rs_fd)
            return False, f"landlock_add_rule:{p}:{errno.errorcode.get(err, err)}"

    ret = _syscall(nr_restrict, rs_fd, 0)
    os.close(rs_fd)
    if ret < 0:
        err = _get_errno()
        return False, f"landlock_restrict_self:{errno.errorcode.get(err, err)}"
    return True, None


def merge_landlock_status_into_meta(
    meta: dict[str, Any],
    status_path: Path | None,
) -> None:
    """Read JSON status file from landlock launcher; update ``meta``; unlink file."""
    meta.setdefault("landlock_requested", False)
    if status_path is None or not status_path.is_file():
        if meta.get("landlock_requested"):
            meta["landlock_applied"] = False
            meta.setdefault("landlock_reason", "status_file_missing")
            meta["trust_degraded_missing_landlock"] = True
        return
    try:
        raw = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        meta["landlock_reason"] = "status_file_unreadable"
        meta["landlock_applied"] = False
        meta["trust_degraded_missing_landlock"] = bool(meta.get("landlock_requested"))
    else:
        meta["landlock_applied"] = bool(raw.get("landlock_applied"))
        meta["landlock_reason"] = raw.get("reason")
        meta["landlock_abi_version"] = raw.get("abi_version")
        if meta.get("landlock_requested") and not meta.get("landlock_applied"):
            meta["trust_degraded_missing_landlock"] = True
    try:
        status_path.unlink(missing_ok=True)
    except OSError:
        pass
