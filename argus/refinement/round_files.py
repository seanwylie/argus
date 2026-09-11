"""Round-numbered artifact files under a refinement session (drafts/reviews/synthesis/convergence)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_ROUND_JSON = re.compile(r"^round_(\d+)\.json$")


def round_indices(subdir: Path) -> set[int]:
    if not subdir.is_dir():
        return set()
    out: set[int] = set()
    for p in subdir.glob("round_*.json"):
        m = _ROUND_JSON.match(p.name)
        if m:
            out.add(int(m.group(1)))
    return out


def latest_round_file(subdir: Path) -> Path | None:
    """Highest ``round_N.json`` in *subdir*, or None."""
    best: tuple[int, Path] | None = None
    if not subdir.is_dir():
        return None
    for p in subdir.glob("round_*.json"):
        m = _ROUND_JSON.match(p.name)
        if not m:
            continue
        n = int(m.group(1))
        if best is None or n > best[0]:
            best = (n, p)
    return best[1] if best else None


def scan_round_chain(session_dir: Path) -> tuple[list[str], list[str]]:
    """
    Detect orphaned or inconsistent round files.

    **Errors** (broken chain): reviews without draft, synthesis without reviews,
    convergence without synthesis.

    **Warnings**: draft without reviews (incomplete cycle), reviews without synthesis,
    synthesis without convergence (may be mid-cycle).
    """
    errors: list[str] = []
    warnings: list[str] = []
    d = session_dir.resolve()
    drafts = round_indices(d / "drafts")
    reviews = round_indices(d / "reviews")
    synthesis = round_indices(d / "synthesis")
    convergence = round_indices(d / "convergence")

    for n in sorted(reviews):
        if n not in drafts:
            errors.append(f"reviews/round_{n}.json exists but drafts/round_{n}.json missing")

    for n in sorted(synthesis):
        if n not in reviews:
            errors.append(f"synthesis/round_{n}.json exists but reviews/round_{n}.json missing")

    for n in sorted(convergence):
        if n not in synthesis:
            errors.append(f"convergence/round_{n}.json exists but synthesis/round_{n}.json missing")

    for n in sorted(drafts):
        if n not in reviews:
            warnings.append(f"drafts/round_{n}.json exists but reviews/round_{n}.json missing (incomplete cycle)")

    for n in sorted(reviews):
        if n not in synthesis and n in drafts:
            warnings.append(f"reviews/round_{n}.json exists but synthesis/round_{n}.json missing")

    for n in sorted(synthesis):
        if n not in convergence and n in reviews:
            warnings.append(f"synthesis/round_{n}.json exists but convergence/round_{n}.json missing")

    return errors, warnings


def round_sets_summary(session_dir: Path) -> dict[str, Any]:
    """Structured snapshot for dashboards (per-session drill-down)."""
    d = session_dir.resolve()
    return {
        "drafts": sorted(round_indices(d / "drafts")),
        "reviews": sorted(round_indices(d / "reviews")),
        "synthesis": sorted(round_indices(d / "synthesis")),
        "convergence": sorted(round_indices(d / "convergence")),
    }
