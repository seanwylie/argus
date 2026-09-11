"""
Stripe / revenue snapshot (local JSON/CSV).

JSON::

    {
      "product_id": "optional",
      "observed_at": "...",
      "mrr_usd": {"current": 1200, "prior": 1180}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.binding import parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.models import REVENUE_FLAT, TRACTION_SIGNAL
from argus.signals.snapshots.normalize import business_signal_record, parse_observed_at, pct_change

ADAPTER_ID = "stripe_revenue_snapshot"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    return float(x)


def ingest_stripe_revenue_snapshot(snapshot_path: Path, repo_root: Path) -> list[SignalRecord]:
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

    mrr = data.get("mrr_usd") if isinstance(data.get("mrr_usd"), dict) else None
    cur = _num(mrr.get("current") if mrr else data.get("mrr_current"))
    pri = _num(mrr.get("prior") if mrr else data.get("mrr_prior"))
    observed = parse_observed_at(data.get("observed_at"))
    pc = pct_change(cur, pri)

    base = {
        "snapshot_file": snapshot_path.name,
        "adapter": ADAPTER_ID,
        "mrr_current": cur,
        "mrr_prior": pri,
    }
    if pc is None:
        return [
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=REVENUE_FLAT,
                observed_at=observed,
                payload={**base, "note": "prior_mrr_zero"},
                confidence=0.5,
            )
        ]
    apc = abs(pc)
    if apc < 0.02:
        sig = REVENUE_FLAT
        conf = 0.82
    elif pc > 0.05:
        sig = TRACTION_SIGNAL
        conf = 0.85
    else:
        sig = TRACTION_SIGNAL
        conf = 0.7

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
