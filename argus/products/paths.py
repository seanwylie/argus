"""Path helpers: repo-relative normalization (POSIX strings)."""

from __future__ import annotations

from pathlib import Path


def ensure_posix_relative(repo_root: Path, path: Path) -> str:
    """Return ``path`` as a POSIX path relative to ``repo_root``."""
    root = repo_root.resolve()
    resolved = path.resolve()
    return resolved.relative_to(root).as_posix()


def join_under_product(product_root: Path, relative: str) -> Path:
    """
    Resolve ``relative`` under ``product_root`` and ensure the result stays under root.

    ``relative`` must be relative (no absolute paths, no ``..`` segments that escape).
    """
    base = product_root.resolve()
    rel = Path(relative.strip())
    if rel.is_absolute():
        raise ValueError("action path must not be absolute")
    parts = rel.parts
    if ".." in parts:
        raise ValueError("action path must not contain '..'")
    candidate = (base / rel).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as e:
        raise ValueError(f"path escapes product root: {relative!r}") from e
    return candidate


def looks_like_filesystem_command(value: str) -> bool:
    """True if ``value`` should be checked as a path on disk (not a shell one-liner)."""
    s = value.strip()
    if not s:
        return False
    if s.startswith("|") or s.startswith(">"):
        return False
    if s.startswith(("./", "../", "/")):
        return True
    if "/" in s and not s.startswith("$") and " " not in s.split()[0]:
        return True
    return False
