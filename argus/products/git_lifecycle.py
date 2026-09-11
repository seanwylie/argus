"""
Best-effort local git initialization for Argus product directories.

No remotes, no push — only ``git init`` and an initial commit when possible.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


def init_argus_product_git(
    product_root: Path,
    *,
    initial_message: str = "chore(argus): initial product scaffold",
) -> dict[str, Any]:
    """
    Initialize a git repository under ``product_root`` if missing; attempt an initial commit.

    Safe when ``git`` is unavailable, the tree is already a repo, or commit cannot complete.
    Does not configure a remote.
    """
    out: dict[str, Any] = {
        "git_executable_found": bool(shutil.which("git")),
        "git_init_ok": False,
        "initial_commit_ok": False,
        "note": None,
    }
    if not out["git_executable_found"]:
        out["note"] = "git not on PATH; skipped local repo init"
        return out

    pr = product_root.resolve()
    git_dir = pr / ".git"
    if git_dir.exists():
        out["git_init_ok"] = True
        out["note"] = "existing_git_repo"
        return out

    init = subprocess.run(
        ["git", "-C", str(pr), "init"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if init.returncode != 0:
        out["note"] = (init.stderr or init.stdout or "git init failed").strip()[:500]
        return out
    out["git_init_ok"] = True

    # Local identity only (this repo), so commit can succeed in CI/agent environments.
    subprocess.run(
        ["git", "-C", str(pr), "config", "user.email", "argus-product@local.invalid"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    subprocess.run(
        ["git", "-C", str(pr), "config", "user.name", "Argus"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    add = subprocess.run(
        ["git", "-C", str(pr), "add", "-A"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if add.returncode != 0:
        out["note"] = (add.stderr or add.stdout or "git add failed").strip()[:500]
        return out

    commit = subprocess.run(
        ["git", "-C", str(pr), "commit", "-m", initial_message],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if commit.returncode == 0:
        out["initial_commit_ok"] = True
        out["note"] = "initial_commit_created"
    else:
        err = (commit.stderr or commit.stdout or "").strip()
        out["note"] = err[:500] if err else "commit_failed_unknown"
    return out


def write_non_git_import_marker(product_root: Path, *, reason: str) -> None:
    """
    Mark a product tree as not git-backed by Argus import (fallback copy path).

    Idempotent overwrite of ``notes/ARGUS_IMPORT_NON_GIT.md``.
    """
    notes = product_root / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    path = notes / "ARGUS_IMPORT_NON_GIT.md"
    path.write_text(
        "# Non–git-backed import\n\n"
        f"This product tree was imported without a `.git/` directory under `products/`.\n\n"
        f"**Reason:** {reason}\n\n"
        "Run `git init` locally if you want a repo-backed product node.\n",
        encoding="utf-8",
    )


__all__ = ["init_argus_product_git", "write_non_git_import_marker"]
