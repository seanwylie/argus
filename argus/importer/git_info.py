"""Read commit (and branch when unambiguous) from a git clone directory."""

from __future__ import annotations

import subprocess
from pathlib import Path


def read_git_commit(repo: Path) -> str | None:
    """40-char SHA at HEAD, or None if unavailable."""
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    s = (proc.stdout or "").strip()
    return s if len(s) >= 7 else None


def read_git_branch(repo: Path) -> str | None:
    """
    Current branch name, or None if detached/unknown.

    Does not fabricate branch names — omits when git cannot resolve a symbolic ref.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "symbolic-ref", "-q", "--short", "HEAD"],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return None
    name = (proc.stdout or "").strip()
    return name if name and name != "HEAD" else None
