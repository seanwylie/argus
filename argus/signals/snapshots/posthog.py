"""
PostHog-style snapshot (local JSON/CSV).

JSON::

    {
      "product_id": "optional",
      "observed_at": "2026-01-15T12:00:00Z",
      "pageviews": {"current": 1200, "prior": 800}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.binding import parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.models import TRACTION_SIGNAL, TRAFFIC_DOWN, TRAFFIC_UP
from argus.signals.snapshots.normalize import business_signal_record, parse_observed_at, pct_change

ADAPTER_ID = "posthog_snapshot"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    return float(x)


def _extract(data: dict[str, Any]) -> tuple[float, float, Any]:
    pv = data.get("pageviews") or data.get("page_views") or {}
    if isinstance(pv, dict):
        return _num(pv.get("current")), _num(pv.get("prior")), data.get("observed_at")
    return _num(data.get("pageviews_current")), _num(data.get("pageviews_prior")), data.get("observed_at")


def ingest_posthog_snapshot(snapshot_path: Path, repo_root: Path) -> list[SignalRecord]:
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

    cur, prior, obs_raw = _extract(data)
    observed = parse_observed_at(obs_raw)
    pc = pct_change(cur, prior)
    base = {
        "snapshot_file": snapshot_path.name,
        "adapter": ADAPTER_ID,
        "pageviews_current": cur,
        "pageviews_prior": prior,
    }
    if pc is None:
        return [
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=TRACTION_SIGNAL,
                observed_at=observed,
                payload={**base, "note": "prior_pageviews_zero"},
                confidence=0.55,
            )
        ]
    if pc > 0.10:
        sig = TRAFFIC_UP
        conf = 0.88
    elif pc < -0.10:
        sig = TRAFFIC_DOWN
        conf = 0.88
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
