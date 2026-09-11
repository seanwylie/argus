"""Defaults for clone/sync excludes and regression-fixture placeholders."""

from __future__ import annotations

# rsync/gitignore-style patterns passed to ``rsync --exclude=``.
DEFAULT_RSYNC_EXCLUDES: tuple[str, ...] = (
    ".git/",
    ".venv/",
    "venv/",
    "__pycache__/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".mypy_cache/",
    ".tox/",
    ".eggs/",
    "*.egg-info/",
    "*.pyc",
    ".env",
    ".env.*",
    "*.log",
    ".coverage",
    "htmlcov/",
    "dist/",
    "build/",
    ".cursor/",  # IDE metadata — use ``--include-cursor`` to copy
    # Large / sensitive local artifacts (opt-in via ``--include-local-db-artifacts``)
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.db-wal",
    "*.db-shm",
)

# Documented in import_notes.md; not all environments have every tool.
OPTIONAL_EXCLUDES_NODE: tuple[str, ...] = (
    "node_modules/",
    ".next/",
    "out/",
    "coverage/",
)


def build_exclude_list(
    *,
    include_cursor: bool,
    include_local_db: bool,
    exclude_node_artifacts: bool,
    extra: list[str],
    preserve_product_git: bool = True,
) -> list[str]:
    """Compose rsync excludes; conservative defaults with explicit opt-ins.

    When ``preserve_product_git`` is True (default), ``.git/`` is **not** excluded so the
    mirrored product tree under ``products/<id>/`` retains repository metadata from the clone cache.
    Set to False for legacy flat copies without ``.git`` under the product directory.
    """
    out = list(DEFAULT_RSYNC_EXCLUDES)
    if preserve_product_git:
        out = [x for x in out if x != ".git/"]
    if include_cursor:
        out = [x for x in out if x != ".cursor/"]
    if include_local_db:
        drop = {"*.db", "*.sqlite", "*.sqlite3", "*.db-wal", "*.db-shm"}
        out = [x for x in out if x not in drop]
    out.extend(extra)
    if exclude_node_artifacts:
        out.extend(OPTIONAL_EXCLUDES_NODE)
    return out
