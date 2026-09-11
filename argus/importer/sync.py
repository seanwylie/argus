"""Copy a cloned tree into ``products/<id>/`` with excludes."""

from __future__ import annotations

import os
import shutil
import subprocess
from fnmatch import fnmatch
from pathlib import Path


def _rsync_available() -> bool:
    return shutil.which("rsync") is not None


def _rel_key(rel: Path) -> str:
    s = rel.as_posix()
    return s if s != "." else ""


def _excluded(rel: Path, patterns: list[str]) -> bool:
    """Return True if ``rel`` (relative to copy root) matches an exclude pattern."""
    key = _rel_key(rel)
    name = rel.name if rel != Path(".") else ""
    for pat in patterns:
        p = pat.rstrip("/")
        if pat.endswith("/"):
            if key == p or key.startswith(p + "/"):
                return True
            continue
        if "*" in pat or "?" in pat or "[" in pat:
            if fnmatch(name, pat) or (key and fnmatch(key, pat)):
                return True
        else:
            if name == pat or key == pat:
                return True
    return False


def _copy_tree_filtered(source: Path, dest: Path, patterns: list[str]) -> None:
    """Copy ``source`` to ``dest`` honoring exclude patterns (non-rsync fallback)."""
    dest.mkdir(parents=True, exist_ok=True)
    for root, dirs, files in os.walk(source, topdown=True):
        rel_root = Path(root).relative_to(source)
        dirs[:] = [
            d
            for d in dirs
            if not _excluded(rel_root / d if rel_root != Path(".") else Path(d), patterns)
        ]
        dest_dir = dest / rel_root if rel_root != Path(".") else dest
        dest_dir.mkdir(parents=True, exist_ok=True)
        for fname in files:
            rel_file = rel_root / fname if rel_root != Path(".") else Path(fname)
            if _excluded(rel_file, patterns):
                continue
            shutil.copy2(source / rel_file, dest_dir / fname)


def sync_tree(
    source: Path,
    dest: Path,
    *,
    excludes: list[str],
    delete: bool,
) -> None:
    """
    Mirror ``source`` into ``dest``.

    Uses ``rsync`` when available. Otherwise copies with filtered walk (delete removes ``dest``
    first when ``delete`` is True).
    """
    dest.parent.mkdir(parents=True, exist_ok=True)

    if _rsync_available():
        cmd = ["rsync", "-a"]
        if delete:
            cmd.append("--delete")
        for pat in excludes:
            cmd.append(f"--exclude={pat}")
        cmd.append(f"{source.as_posix()}/")
        cmd.append(f"{dest.as_posix()}/")
        subprocess.run(cmd, check=True)
        return

    if delete and dest.exists():
        shutil.rmtree(dest)
    _copy_tree_filtered(source, dest, excludes)
