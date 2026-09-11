"""
Content / channel snapshot (local JSON/CSV): YouTube, Substack, newsletters, Medium-style exports.

Use filenames containing ``content_platform``, ``youtube``, ``substack``, or ``channel_metrics``
so :mod:`argus.signals.snapshots.registry` dispatches here. Live APIs stay behind your own ETL;
Argus only ingests dropped files under ``products/<id>/metrics/snapshots/``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.binding import parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.models import TRACTION_SIGNAL, TRAFFIC_DOWN, TRAFFIC_UP
from argus.signals.snapshots.normalize import business_signal_record, parse_observed_at, pct_change

ADAPTER_ID = "content_platform_snapshot"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _extract_series(data: dict[str, Any]) -> tuple[float, float, Any]:
    """
    Prefer subscribers/followers with current/prior; else views_7d or impressions.
    """
    for key in ("subscribers", "followers", "members"):
        block = data.get(key)
        if isinstance(block, dict):
            return _num(block.get("current")), _num(block.get("prior")), data.get("observed_at")
    # Flat keys
    if "subscribers_current" in data or "followers_current" in data:
        cur = _num(data.get("subscribers_current") or data.get("followers_current"))
        pri = _num(data.get("subscribers_prior") or data.get("followers_prior"))
        return cur, pri, data.get("observed_at")
    views = data.get("views_7d") or data.get("views")
    if isinstance(views, dict):
        return _num(views.get("current")), _num(views.get("prior")), data.get("observed_at")
    if views is not None:
        return _num(views), max(0.0, _num(views) * 0.85), data.get("observed_at")
    return 0.0, 0.0, data.get("observed_at")


def ingest_content_platform_snapshot(snapshot_path: Path, repo_root: Path) -> list[SignalRecord]:
    text = snapshot_path.read_text(encoding="utf-8")
    if snapshot_path.suffix.lower() == ".csv":
        rows = parse_csv_dicts(text)
        if not rows:
            return []
        data = {k: v for k, v in rows[0].items()}
        pid = resolve_product_id(snapshot_path, repo_root, None, csv_first_row=rows[0])
    else:
        raw = json.loads(text)
        if not isinstance(raw, dict):
            return []
        data = raw
        pid = resolve_product_id(snapshot_path, repo_root, data)

    cur, prior, obs_raw = _extract_series(data)
    observed = parse_observed_at(obs_raw)
    platform = str(data.get("platform") or data.get("channel") or "content")
    pc = pct_change(cur, prior) if cur > 0 or prior > 0 else None

    base: dict[str, Any] = {
        "snapshot_file": snapshot_path.name,
        "adapter": ADAPTER_ID,
        "platform": platform,
        "engagement_current": cur,
        "engagement_prior": prior,
        "monetization_hint": str(data.get("monetization") or ""),
    }
    if pc is None:
        return [
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=TRACTION_SIGNAL,
                observed_at=observed,
                payload={**base, "note": "insufficient_prior_for_pct"},
                confidence=0.55,
            )
        ]
    if pc > 0.10:
        sig = TRAFFIC_UP
        conf = 0.85
    elif pc < -0.10:
        sig = TRAFFIC_DOWN
        conf = 0.85
    else:
        sig = TRACTION_SIGNAL
        conf = 0.72

    return [
        business_signal_record(
            product_id=pid,
            adapter_source=ADAPTER_ID,
            business_signal=sig,
            observed_at=observed,
            payload={**base, "pct_change": round(pc, 6)},
            confidence=conf,
        )
    ]
