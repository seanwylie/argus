"""Repository path helpers for the CLI."""

from __future__ import annotations

import os
from pathlib import Path


def repo_root() -> Path:
    """Root of the Argus monorepo (directory containing ``products/``)."""
    env = os.environ.get("ARGUS_REPO_ROOT", "").strip()
    if env:
        return Path(env).resolve()
    # argus/cli/repo.py -> parents[2] == repo root
    return Path(__file__).resolve().parents[2]
