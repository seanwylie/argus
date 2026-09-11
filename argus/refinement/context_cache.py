"""Per-cycle cached doctrine + strategy text for stakeholder reviews (load once per council run)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from argus.context.sources import doctrine_excerpt, strategy_summary


@dataclass(frozen=True)
class CycleReviewContext:
    """Immutable snapshot for one refinement cycle — shared by all stakeholder LLM calls."""

    doctrine_excerpt: str
    strategy_summary: str
    cached_at_utc: str
    strategy_mode: str | None = None


def load_cycle_review_context(repo_root: Path, product_id: str | None) -> CycleReviewContext:
    """Load doctrine + strategy once; safe to share across parallel review workers."""
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).isoformat()
    doctrine = doctrine_excerpt(root, product_id, max_chars=8000)
    mode, summary = strategy_summary(root, max_chars=6000)
    return CycleReviewContext(
        doctrine_excerpt=doctrine,
        strategy_summary=summary,
        cached_at_utc=ts,
        strategy_mode=mode,
    )
