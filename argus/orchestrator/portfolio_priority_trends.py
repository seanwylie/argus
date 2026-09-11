"""
Derived read-only trends over portfolio priority **generation** snapshots.

Reads ``runs/orchestration/generations/portfolio_priorities_*.json`` (newest-first window),
writes ``runs/orchestration/latest/portfolio_priority_trends.json``.
Does not alter scoring — only aggregates ranks across persisted snapshots.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.artifact_paths import (
    orchestration_generations_dir,
    portfolio_priority_trends_path,
)
from argus.orchestrator.portfolio_priorities import PORTFOLIO_PRIORITIES_SCHEMA

PORTFOLIO_PRIORITY_TRENDS_SCHEMA = "argus.portfolio_priority_trends.v1"

# Default rolling window: most recent N generation files (lexicographic UTC compact names sort chronologically).
DEFAULT_TREND_WINDOW = 5

# Lower rank number = higher urgency. ``latest`` materially better than ``average`` → rising.
# Integer-friendly threshold on (latest_rank - average_rank): positive means worse than average.
MATERIAL_RANK_DELTA = 1.5


def _portfolio_stability_and_score(rank_ones: list[str | None]) -> tuple[str, float]:
    """
    Lightweight portfolio stability from rank-1 churn only (inspectable).

    Let ``t`` = number of transitions between consecutive snapshots (``len(rank_ones) - 1``).
    Let ``c`` = how many of those transitions changed the top product.

    - **score** = ``min(1.0, c / max(t, 1))`` — fraction of transitions that changed #1.
    - **stable**: ``t == 0`` (single snapshot) OR ``c == 0`` (same top across the window).
    - **volatile**: ``t >= 2`` and ``c >= (t + 1) // 2`` — a majority of transitions changed #1.
    - **shifting**: ``c >= 1`` but not volatile — some movement, not majority churn.
    """
    if not rank_ones:
        return "stable", 0.0
    t = len(rank_ones) - 1
    if t <= 0:
        return "stable", 0.0
    c = 0
    for i in range(1, len(rank_ones)):
        a, b = rank_ones[i - 1], rank_ones[i]
        if a is None or b is None:
            continue
        if a != b:
            c += 1
    score = min(1.0, c / max(t, 1))
    if c == 0:
        return "stable", round(score, 3)
    majority = t >= 2 and c >= (t + 1) // 2
    if majority:
        return "volatile", round(score, 3)
    return "shifting", round(score, 3)


def _top_products_to_inspect(snapshots: list[dict[str, Any]], *, limit: int = 3) -> list[str]:
    """First ``limit`` product_ids by ascending rank from the newest snapshot in the window."""
    if not snapshots:
        return []
    last = snapshots[-1]
    rows = last.get("products") or []
    if not isinstance(rows, list):
        return []
    ranked: list[tuple[int, str]] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        pid = str(r.get("product_id") or "").strip()
        if not pid:
            continue
        try:
            rk = int(r.get("rank") or 0)
        except (TypeError, ValueError):
            continue
        if rk > 0:
            ranked.append((rk, pid))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [p for _, p in ranked[:limit]]


def _operator_recommendations(
    *,
    rank_ones: list[str | None],
    snapshots: list[dict[str, Any]],
    products_out: list[dict[str, Any]],
    portfolio_stability: str,
    top_products_to_inspect: list[str],
) -> list[str]:
    """
    Deterministic, informational strings only (no scheduling / execution).

    Rules (fixed order):
    1. Volatility / shifting nudge when portfolio_stability is volatile or shifting.
    2. Top 3 to inspect from latest snapshot ranks.
    3. If current #1 product has ``times_ranked_first`` >= 2, note repeated #1.
    4. Up to two ``rising`` products (product_id sort).
    """
    recs: list[str] = []
    if portfolio_stability == "volatile":
        recs.append("Portfolio attention is shifting; review recent orchestration changes.")
    elif portfolio_stability == "shifting" and len(rank_ones) > 1:
        recs.append("Portfolio priority has moved — scan the top-ranked products.")

    if top_products_to_inspect:
        recs.append("Top products to inspect now: " + ", ".join(top_products_to_inspect[:3]))

    latest_top = rank_ones[-1] if rank_ones else None
    if isinstance(latest_top, str) and latest_top.strip():
        row = next((p for p in products_out if p.get("product_id") == latest_top), None)
        if row:
            tf = int(row.get("times_ranked_first") or 0)
            if tf >= 2 and len(snapshots) >= 1:
                recs.append(
                    f"{latest_top} held rank #1 in {tf} of {len(snapshots)} snapshot(s) in the window."
                )

    rising_ids = sorted(
        str(p["product_id"]) for p in products_out if p.get("rising") and p.get("product_id")
    )
    for pid in rising_ids[:2]:
        recs.append(f"{pid} is rising in urgency vs the recent average rank.")

    return recs


def _parse_iso_utc_to_compact(iso_ts: str) -> str:
    """``2026-04-12T10:00:00+00:00`` → ``20260412T100000Z`` (UTC, second precision)."""
    s = iso_ts.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")


def generation_filename_for_payload(generated_at_utc: str) -> str:
    """Deterministic basename ``portfolio_priorities_<UTCcompact>.json``."""
    compact = _parse_iso_utc_to_compact(generated_at_utc)
    return f"portfolio_priorities_{compact}.json"


def _unique_generation_path(gen_dir: Path, generated_at_utc: str) -> Path:
    """Reserve a path under ``generations/``; if same-second collision, append ``_2``, ``_3``, …."""
    gen_dir.mkdir(parents=True, exist_ok=True)
    base_name = generation_filename_for_payload(generated_at_utc)
    p = gen_dir / base_name
    if not p.exists():
        return p
    stem = base_name[:-5]  # .json
    n = 2
    while True:
        q = gen_dir / f"{stem}_{n}.json"
        if not q.exists():
            return q
        n += 1


def write_portfolio_priorities_generation_copy(repo_root: Path, payload: dict[str, Any], text: str) -> Path | None:
    """
    Persist a duplicate of the latest portfolio priorities JSON under ``generations/``.

    Returns the path written, or ``None`` if ``generated_at_utc`` is missing/invalid.
    """
    ts = payload.get("generated_at_utc")
    if not isinstance(ts, str) or not ts.strip():
        return None
    try:
        dest = _unique_generation_path(orchestration_generations_dir(repo_root), ts.strip())
    except (OSError, ValueError, TypeError):
        return None
    dest.write_text(text, encoding="utf-8")
    return dest


def list_portfolio_priority_generation_paths(repo_root: Path) -> list[Path]:
    """All ``portfolio_priorities_*.json`` under ``runs/orchestration/generations/``, oldest first."""
    d = orchestration_generations_dir(repo_root)
    if not d.is_dir():
        return []
    paths = sorted(d.glob("portfolio_priorities_*.json"), key=lambda p: p.name)
    return paths


def _load_priority_snapshot(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("schema") or "") != PORTFOLIO_PRIORITIES_SCHEMA:
        return None
    return raw


def _rank_map_from_snapshot(raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    rows = raw.get("products") or []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").strip()
        if not pid:
            continue
        rk = row.get("rank")
        try:
            rki = int(rk) if rk is not None else 0
        except (TypeError, ValueError):
            continue
        if rki > 0:
            out[pid] = rki
    return out


def _score_map_from_snapshot(raw: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    rows = raw.get("products") or []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").strip()
        if not pid:
            continue
        sc = row.get("priority_score")
        try:
            out[pid] = int(sc) if sc is not None else 0
        except (TypeError, ValueError):
            out[pid] = 0
    return out


def _rank_one_id(raw: dict[str, Any]) -> str | None:
    rid = raw.get("recommended_product_id")
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    rm = _rank_map_from_snapshot(raw)
    if not rm:
        return None
    # Deterministic: smallest rank; tie-break product_id
    best = min(rm.values())
    candidates = sorted(pid for pid, r in rm.items() if r == best)
    return candidates[0] if candidates else None


def _churn_summary(rank_ones: list[str | None]) -> str:
    if not rank_ones:
        return "No portfolio priority snapshots in the configured window."
    if len(rank_ones) == 1:
        return "Single snapshot in window — churn not measured."
    changes = 0
    for i in range(1, len(rank_ones)):
        a, b = rank_ones[i - 1], rank_ones[i]
        if a is None or b is None:
            continue
        if a != b:
            changes += 1
    t = len(rank_ones) - 1
    return f"Top product changed {changes} of {t} time(s) between consecutive snapshots."


def _classify_flags(
    *,
    latest_rank: int,
    average_rank: float,
    n_samples: int,
) -> tuple[bool, bool, bool]:
    """
    Integer/rank-based trend flags (lower rank = more urgent).

    - **rising**: ``latest`` is materially *better* than the window average (at least MATERIAL_RANK_DELTA).
    - **falling**: ``latest`` is materially *worse* than the average.
    - **stable**: single sample, or movement below the material threshold (and not rising/falling).

    ``n_samples`` counts generations in the window where this product had a rank.
    """
    if n_samples < 1:
        return False, False, False
    if n_samples < 2:
        return False, False, True
    delta = float(latest_rank) - float(average_rank)
    rising = delta <= -MATERIAL_RANK_DELTA
    falling = delta >= MATERIAL_RANK_DELTA
    stable = not rising and not falling
    return rising, falling, stable


def _round_rank(avg: float) -> float:
    return round(avg, 2)


def _trend_summary(
    *,
    product_id: str,
    latest_rank: int,
    average_rank: float,
    n_samples: int,
    times_first: int,
    window_paths_n: int,
    rising: bool,
    falling: bool,
    stable: bool,
    best_rank: int,
    worst_rank: int,
) -> str:
    parts: list[str] = []
    if times_first > 0 and window_paths_n > 0:
        parts.append(f"Rank #1 in {times_first} of {window_paths_n} snapshot(s)")
    if n_samples >= 2:
        if rising:
            parts.append(
                f"Rising toward the top (latest rank {latest_rank} vs avg {_round_rank(average_rank)})"
            )
        elif falling:
            parts.append(
                f"Slipping vs recent average (latest rank {latest_rank} vs avg {_round_rank(average_rank)})"
            )
        elif stable and (worst_rank - best_rank) <= 1:
            parts.append("Stable priority across recent snapshots")
        elif stable:
            parts.append(
                f"Moderate movement (best {best_rank}, worst {worst_rank}; avg {_round_rank(average_rank)})"
            )
    elif n_samples == 1:
        parts.append("Only one snapshot includes this product in the window")
    if not parts:
        parts.append(f"product {product_id}: latest rank {latest_rank}")
    return "; ".join(parts)


def build_portfolio_priority_trends(
    repo_root: Path,
    *,
    window_size: int = DEFAULT_TREND_WINDOW,
) -> dict[str, Any]:
    """
    Aggregate ranks from the last ``window_size`` generation files (non-destructive read).

    Products are sorted by ``product_id`` for stable JSON diffs.
    """
    root = repo_root.resolve()
    if window_size < 1:
        window_size = 1
    all_paths = list_portfolio_priority_generation_paths(root)
    window_paths = all_paths[-window_size:] if len(all_paths) > window_size else all_paths

    ts = datetime.now(timezone.utc).isoformat()
    if not window_paths:
        return {
            "schema": PORTFOLIO_PRIORITY_TRENDS_SCHEMA,
            "schema_version": "1",
            "generated_at_utc": ts,
            "window_size": 0,
            "generations_considered": 0,
            "churn_summary": "No portfolio priority generation files on disk.",
            "portfolio_stability": "stable",
            "portfolio_stability_score": 0.0,
            "top_products_to_inspect": [],
            "operator_recommendations": [],
            "products": [],
        }

    snapshots: list[dict[str, Any]] = []
    for p in window_paths:
        snap = _load_priority_snapshot(p)
        if snap is not None:
            snapshots.append(snap)

    if not snapshots:
        return {
            "schema": PORTFOLIO_PRIORITY_TRENDS_SCHEMA,
            "schema_version": "1",
            "generated_at_utc": ts,
            "window_size": len(window_paths),
            "generations_considered": 0,
            "churn_summary": (
                "Generation file(s) present under runs/orchestration/generations/ but none were valid "
                f"{PORTFOLIO_PRIORITIES_SCHEMA} JSON."
            ),
            "portfolio_stability": "stable",
            "portfolio_stability_score": 0.0,
            "top_products_to_inspect": [],
            "operator_recommendations": [],
            "products": [],
        }

    rank_ones: list[str | None] = []
    for snap in snapshots:
        rank_ones.append(_rank_one_id(snap))

    # Collect all product ids seen in any snapshot in the window
    product_ids: set[str] = set()
    per_snap_ranks: list[dict[str, int]] = []
    per_snap_scores: list[dict[str, int]] = []
    for snap in snapshots:
        rm = _rank_map_from_snapshot(snap)
        sm = _score_map_from_snapshot(snap)
        per_snap_ranks.append(rm)
        per_snap_scores.append(sm)
        product_ids.update(rm.keys())

    products_out: list[dict[str, Any]] = []

    for pid in sorted(product_ids):
        ranks: list[int] = []
        scores: list[int] = []
        times_first = 0
        for rm in per_snap_ranks:
            if pid in rm:
                r = rm[pid]
                ranks.append(r)
                if r == 1:
                    times_first += 1
        for sm in per_snap_scores:
            if pid in sm:
                scores.append(sm[pid])

        # Latest = most recent chronologically within the window that includes this product.
        latest_rank = 0
        latest_score: int | None = None
        for idx in range(len(per_snap_ranks) - 1, -1, -1):
            rm = per_snap_ranks[idx]
            if pid in rm:
                latest_rank = rm[pid]
                latest_score = per_snap_scores[idx].get(pid)
                break

        n_samples = len(ranks)
        if n_samples == 0:
            continue

        avg_rank = sum(ranks) / n_samples
        best_rank = min(ranks)
        worst_rank = max(ranks)
        avg_score: float | None = None
        if scores:
            avg_score = round(sum(scores) / len(scores), 2)

        rising, falling, stable = _classify_flags(
            latest_rank=latest_rank,
            average_rank=avg_rank,
            n_samples=n_samples,
        )

        products_out.append(
            {
                "product_id": pid,
                "latest_rank": latest_rank,
                "average_rank": _round_rank(avg_rank),
                "best_rank": best_rank,
                "worst_rank": worst_rank,
                "times_ranked_first": times_first,
                "rising": rising,
                "falling": falling,
                "stable": stable,
                "trend_summary": _trend_summary(
                    product_id=pid,
                    latest_rank=latest_rank,
                    average_rank=avg_rank,
                    n_samples=n_samples,
                    times_first=times_first,
                    window_paths_n=len(snapshots),
                    rising=rising,
                    falling=falling,
                    stable=stable,
                    best_rank=best_rank,
                    worst_rank=worst_rank,
                ),
                "latest_priority_score": latest_score,
                "average_priority_score": avg_score,
            }
        )

    portfolio_stability, portfolio_stability_score = _portfolio_stability_and_score(rank_ones)
    top_inspect = _top_products_to_inspect(snapshots, limit=3)
    op_recs = _operator_recommendations(
        rank_ones=rank_ones,
        snapshots=snapshots,
        products_out=products_out,
        portfolio_stability=portfolio_stability,
        top_products_to_inspect=top_inspect,
    )

    return {
        "schema": PORTFOLIO_PRIORITY_TRENDS_SCHEMA,
        "schema_version": "1",
        "generated_at_utc": ts,
        "window_size": len(window_paths),
        "generations_considered": len(snapshots),
        "churn_summary": _churn_summary(rank_ones),
        "portfolio_stability": portfolio_stability,
        "portfolio_stability_score": portfolio_stability_score,
        "top_products_to_inspect": top_inspect,
        "operator_recommendations": op_recs,
        "products": products_out,
    }


def write_portfolio_priority_trends_artifact(
    repo_root: Path,
    *,
    window_size: int = DEFAULT_TREND_WINDOW,
) -> Path:
    """Write ``runs/orchestration/latest/portfolio_priority_trends.json``."""
    root = repo_root.resolve()
    payload = build_portfolio_priority_trends(root, window_size=window_size)
    path = portfolio_priority_trends_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return path


def read_portfolio_priority_trends_json(repo_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """
    Read ``runs/orchestration/latest/portfolio_priority_trends.json`` when present.

    Returns ``(payload, None)`` when readable and declares
    ``schema == argus.portfolio_priority_trends.v1``.
    Missing file: ``(None, None)``. Malformed / wrong schema: ``(None, short_error)``.
    """
    root = repo_root.resolve()
    path = portfolio_priority_trends_path(root)
    if not path.is_file():
        return None, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as e:
        return None, f"not valid JSON ({e})"
    if not isinstance(raw, dict):
        return None, "expected a JSON object"
    if str(raw.get("schema") or "") != PORTFOLIO_PRIORITY_TRENDS_SCHEMA:
        return None, f"schema is {raw.get('schema')!r} (expected {PORTFOLIO_PRIORITY_TRENDS_SCHEMA})"
    return raw, None
