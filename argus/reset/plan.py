"""
Build inspectable reset plans (no filesystem mutation).

Modes:
  * ``soft`` — ``runs/**`` except ``runs/README.md``
  * ``portfolio`` — soft + remove all children of ``products/``
  * ``all`` — portfolio + additional project-local caches (conservative; see :func:`build_reset_plan`)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

ResetMode = Literal["soft", "portfolio", "all"]

# Tracked in-repo; must survive soft/portfolio/all (see repo ``.gitignore``).
RUNS_README_REL = "runs/README.md"


@dataclass(frozen=True)
class ResetPlan:
    """Concrete reset intent for one repo root."""

    mode: ResetMode
    repo_root: Path
    # Paths to delete: directories (shutil.rmtree) or files (unlink). Repo-relative posix for display.
    paths_targets: tuple[str, ...]
    preserved_categories: tuple[str, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)


def _is_under_repo(root: Path, path: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _count_tree_files_and_dirs(root: Path) -> tuple[int, int]:
    """Count files and directories under *root* (including *root* as a dir if it exists)."""
    if not root.is_dir():
        return 0, 0
    n_files = 0
    n_dirs = 0
    for p in root.rglob("*"):
        if p.is_dir():
            n_dirs += 1
        elif p.is_file():
            n_files += 1
    return n_files, n_dirs


def _rel_posix(repo: Path, path: Path) -> str:
    return str(path.resolve().relative_to(repo.resolve())).replace("\\", "/")


def build_reset_plan(repo_root: Path, mode: ResetMode) -> ResetPlan:
    """
    Enumerate targets. No deletion.

    * ``repo_root`` must be absolute or is resolved here.
    """
    root = repo_root.resolve()
    targets: list[str] = []
    preserved: list[str] = [
        "Source tree: argus/, tests/, docs/, pyproject.toml, tools/, …",
        "config/** (operator policy, mission profiles, …)",
        ".env and .env.* — never targeted by reset",
        ".git/** — never targeted",
        f"{RUNS_README_REL} — always kept (stable mount point)",
    ]
    notes: list[str] = []

    runs = root / "runs"
    runs_readme = runs / "README.md"
    if runs.is_dir():
        for child in sorted(runs.iterdir()):
            if child.name == "README.md" and child.is_file():
                try:
                    if runs_readme.resolve() == child.resolve():
                        continue
                except OSError:
                    pass
            if not _is_under_repo(root, child):
                continue
            targets.append(_rel_posix(root, child))

    if mode in ("portfolio", "all"):
        notes.append("Portfolio mode removes each child under products/ (empty portfolio).")
        products = root / "products"
        if products.is_dir():
            for child in sorted(products.iterdir()):
                if not _is_under_repo(root, child):
                    continue
                targets.append(_rel_posix(root, child))

    if mode == "all":
        notes.append(
            "Also removes .import_cache/ if present (Argus importer clone cache). "
            "Does not remove .venv/, node_modules/, or other tool dirs unless listed as targets."
        )
        import_cache = root / ".import_cache"
        if import_cache.is_dir() or import_cache.is_file():
            targets.append(_rel_posix(root, import_cache))

    # Dedupe while preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for t in targets:
        if t not in seen:
            seen.add(t)
            uniq.append(t)

    return ResetPlan(
        mode=mode,
        repo_root=root,
        paths_targets=tuple(uniq),
        preserved_categories=tuple(preserved),
        notes=tuple(notes),
    )


def plan_summary_counts(plan: ResetPlan) -> dict[str, int]:
    """Best-effort file/dir counts for paths that exist on disk."""
    root = plan.repo_root
    total_files = 0
    total_dirs = 0
    for rel in plan.paths_targets:
        p = root / rel
        if not p.exists():
            continue
        if p.is_file():
            total_files += 1
        elif p.is_dir():
            nf, nd = _count_tree_files_and_dirs(p)
            total_files += nf
            total_dirs += nd + 1
    return {"files": total_files, "directories": total_dirs}


def format_plan_report(plan: ResetPlan) -> str:
    """Human-readable multi-line summary for dry-run / stdout."""
    lines: list[str] = []
    lines.append(f"Reset mode: {plan.mode}")
    lines.append(f"Repo root: {plan.repo_root}")
    lines.append("")
    lines.append("Targets (relative to repo root):")
    if not plan.paths_targets:
        lines.append("  (none — nothing to remove)")
    else:
        for t in plan.paths_targets:
            lines.append(f"  - {t}")
    lines.append("")
    counts = plan_summary_counts(plan)
    lines.append(
        f"Approximate count (existing paths): files={counts['files']}, directories={counts['directories']}"
    )
    lines.append("")
    lines.append("Preserved (not targeted):")
    for p in plan.preserved_categories:
        lines.append(f"  - {p}")
    if plan.notes:
        lines.append("")
        lines.append("Notes:")
        for n in plan.notes:
            lines.append(f"  - {n}")
    return "\n".join(lines) + "\n"
