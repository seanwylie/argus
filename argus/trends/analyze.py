"""Build trend summaries from historical portfolio snapshots."""

from __future__ import annotations

import json
from pathlib import Path

from argus.history.models import ProductSnapshot
from argus.history.storage import iter_snapshot_dirs, load_snapshot_file
from argus.history.summarize import load_product_timeline
from argus.trends.models import TrendFlag, TrendSummary
from argus.trends.rules import (
    assign_trend_flags,
    build_interpretation,
    build_summary_text,
    confidence_score,
    drift_signals,
    series_metrics,
)


def iter_product_ids_with_history(repo_root: Path) -> list[str]:
    """Distinct product ids appearing in any stored snapshot (sorted)."""
    seen: set[str] = set()
    for d in iter_snapshot_dirs(repo_root):
        sp = d / "snapshot.json"
        try:
            snap = load_snapshot_file(sp)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        for p in snap.products:
            seen.add(p.product_id)
    return sorted(seen)


def analyze_product_series(series_oldest_first: list[ProductSnapshot]) -> TrendSummary:
    """Core analysis for a time-ordered (oldest → newest) series."""
    pid = series_oldest_first[0].product_id if series_oldest_first else ""
    n = len(series_oldest_first)
    if n < 2:
        return TrendSummary(
            product_id=pid,
            window_size=n,
            trend_flags=[TrendFlag.INSUFFICIENT_DATA.value],
            summary=f"{pid or 'unknown'}: need at least 2 snapshots (have {n}).",
            confidence=0.0,
            recommended_interpretation=(
                "Capture more history snapshots (argus history snapshot) before relying on trends."
            ),
            drift_signals=[],
            metrics={"n": n},
        )

    m = series_metrics(series_oldest_first)
    dr = drift_signals(series_oldest_first, m)
    fl = assign_trend_flags(series_oldest_first, m, dr)
    conf = confidence_score(n, len(dr), len(fl))
    return TrendSummary(
        product_id=pid,
        window_size=n,
        trend_flags=fl,
        summary=build_summary_text(pid, fl, dr, m),
        confidence=conf,
        recommended_interpretation=build_interpretation(fl, dr),
        drift_signals=dr,
        metrics=m,
    )


def analyze_product(repo_root: Path, product_id: str) -> TrendSummary:
    """Load timeline from history and analyze (oldest-first series)."""
    tl = load_product_timeline(repo_root, product_id)
    if not tl:
        return TrendSummary(
            product_id=product_id,
            window_size=0,
            trend_flags=[TrendFlag.INSUFFICIENT_DATA.value],
            summary=f"{product_id}: no snapshot history.",
            confidence=0.0,
            recommended_interpretation=(
                "Run `argus history snapshot` periodically, then re-run trends."
            ),
            drift_signals=[],
            metrics={"n": 0},
        )
    series = [row[2] for row in reversed(tl)]
    return analyze_product_series(series)


def analyze_all_with_history(repo_root: Path) -> list[TrendSummary]:
    """One TrendSummary per product that appears in any snapshot."""
    out: list[TrendSummary] = []
    for pid in iter_product_ids_with_history(repo_root):
        out.append(analyze_product(repo_root, pid))
    return out
