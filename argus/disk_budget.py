"""
Filesystem free-space guardrails for Argus writes.

These checks use volume free space (``shutil.disk_usage``), not per-user NFS quotas.
Tune with ``ARGUS_MIN_FREE_DISK_MB`` and ``ARGUS_ENFORCE_DISK_HEADROOM``.
"""

from __future__ import annotations

import logging
import os
import shutil
import stat
from pathlib import Path

logger = logging.getLogger(__name__)

_ENV_MIN_FREE_MB = "ARGUS_MIN_FREE_DISK_MB"
_ENV_ENFORCE = "ARGUS_ENFORCE_DISK_HEADROOM"

_DEFAULT_MIN_FREE_MIB = 256


class DiskBudgetError(OSError):
    """Raised when strict disk headroom check fails before a write."""


def min_free_bytes() -> int | None:
    """
    Minimum free bytes required on the write volume.

    ``ARGUS_MIN_FREE_DISK_MB``: size in MiB. When unset, ``256`` MiB is required.
    ``0`` or negative disables the check (always passes).
    """
    raw = os.environ.get(_ENV_MIN_FREE_MB, "").strip()
    if not raw:
        return _DEFAULT_MIN_FREE_MIB * 1024 * 1024
    try:
        mb = int(raw, 10)
    except ValueError:
        return _DEFAULT_MIN_FREE_MIB * 1024 * 1024
    if mb <= 0:
        return None
    return mb * 1024 * 1024


def enforce_disk_headroom() -> bool:
    """When true, :func:`require_disk_headroom_for_write` raises instead of logging."""
    v = os.environ.get(_ENV_ENFORCE, "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _usage_anchor(path: Path) -> Path:
    """Walk toward filesystem root until an existing path is found."""
    p = path.resolve()
    for _ in range(512):
        try:
            if p.exists():
                return p
        except OSError:
            pass
        if p == p.parent:
            break
        p = p.parent
    return Path(".").resolve()


def volume_bytes_free_total(path: Path) -> tuple[int | None, int | None]:
    anchor = _usage_anchor(path)
    try:
        du = shutil.disk_usage(anchor)
        return du.free, du.total
    except OSError:
        return None, None


def check_disk_headroom(write_target: Path, *, op: str = "write") -> tuple[bool, str]:
    """
    Return ``(ok, message)``. When ``ok`` is false, ``message`` explains low disk space.

    On ``disk_usage`` failure, returns ``(True, "")`` (do not block writes).
    """
    need = min_free_bytes()
    if need is None:
        return True, ""
    anchor = _usage_anchor(write_target)
    try:
        du = shutil.disk_usage(anchor)
    except OSError:
        return True, ""
    if du.free >= need:
        return True, ""
    free_mb = du.free // (1024 * 1024)
    need_mb = max(1, need // (1024 * 1024))
    return (
        False,
        f"low disk space: {free_mb} MiB free on volume at {anchor} "
        f"(ARGUS_MIN_FREE_DISK_MB requires ~{need_mb} MiB) — {op} may fail",
    )


def require_disk_headroom_for_write(write_target: Path, *, op: str = "write") -> None:
    ok, msg = check_disk_headroom(write_target, op=op)
    if ok:
        return
    if enforce_disk_headroom():
        raise DiskBudgetError(msg)
    logger.warning("%s", msg)


def approx_tree_bytes(root: Path, *, max_files: int = 200_000) -> tuple[int, int, bool]:
    """
    Sum regular-file sizes under ``root`` (directories only; nonexistent root → zeros).

    Returns ``(total_bytes, files_counted, capped)`` where ``capped`` is true if ``max_files``
    was hit before the walk finished.
    """
    if not root.is_dir():
        return 0, 0, False
    total = 0
    count = 0
    stack: list[Path] = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if count >= max_files:
                return total, count, True
            try:
                st = e.lstat()
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                stack.append(e)
            elif stat.S_ISREG(st.st_mode):
                total += st.st_size
                count += 1
    return total, count, False


def collect_doctor_disk_section(repo: Path) -> dict[str, object]:
    """Structured disk / ``runs/`` footprint for ``argus doctor``."""
    runs = repo / "runs"
    approx_bytes, files_n, capped = approx_tree_bytes(runs)
    anchor = runs if runs.is_dir() else repo
    free, total = volume_bytes_free_total(anchor)
    need = min_free_bytes()
    low_warning: str | None = None
    if free is not None and need is not None and free < need:
        ok, detail = check_disk_headroom(anchor, op="writes")
        if not ok:
            low_warning = detail
    return {
        "schema": "argus.doctor_disk.v1",
        "runs_dir_approx_bytes": approx_bytes,
        "runs_dir_files_counted": files_n,
        "runs_dir_tree_capped": capped,
        "volume_anchor_path": str(_usage_anchor(anchor)),
        "volume_free_bytes": free,
        "volume_total_bytes": total,
        "min_free_bytes_required": need,
        "low_disk_warning": low_warning,
    }


__all__ = [
    "DiskBudgetError",
    "approx_tree_bytes",
    "check_disk_headroom",
    "collect_doctor_disk_section",
    "enforce_disk_headroom",
    "min_free_bytes",
    "require_disk_headroom_for_write",
    "volume_bytes_free_total",
]
