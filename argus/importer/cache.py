"""Clone or update a GitHub repository in a local cache directory."""

from __future__ import annotations

import subprocess
from pathlib import Path

from argus.importer.url import GitHubRepoRef


def ensure_git_repo_cache(
    cache_root: Path,
    ref: GitHubRepoRef,
    *,
    shallow: bool = True,
) -> Path:
    """
    Return path to a bare checkout of ``ref`` under ``cache_root``.

    If the directory exists and contains a ``.git`` folder, run ``git fetch`` + ``git pull
    --ff-only`` (best effort). Otherwise clone ``ref.normalized_url``.
    """
    cache_root.mkdir(parents=True, exist_ok=True)
    dest = cache_root / ref.slug
    if (dest / ".git").is_dir():
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--all", "--prune"],
            check=False,
        )
        subprocess.run(
            ["git", "-C", str(dest), "pull", "--ff-only"],
            check=False,
        )
        return dest

    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["git", "clone"]
    if shallow:
        cmd.append("--depth=1")
    cmd.extend([ref.normalized_url, str(dest)])
    subprocess.run(cmd, check=True)
    return dest
