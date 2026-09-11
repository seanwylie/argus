"""
AWS Cost Explorer–style snapshot (local JSON/CSV).

JSON::

    {
      "product_id": "optional",
      "observed_at": "...",
      "cost_usd": {"current_month": 450, "prior_month": 200}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.binding import parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.models import COST_SPIKE, TRACTION_SIGNAL
from argus.signals.snapshots.normalize import business_signal_record, parse_observed_at, pct_change

ADAPTER_ID = "aws_cost_snapshot"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    return float(x)


def ingest_aws_cost_snapshot(snapshot_path: Path, repo_root: Path) -> list[SignalRecord]:
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

    cst = data.get("cost_usd") if isinstance(data.get("cost_usd"), dict) else None
    cur = _num(cst.get("current_month") if cst else data.get("cost_current"))
    pri = _num(cst.get("prior_month") if cst else data.get("cost_prior"))
    observed = parse_observed_at(data.get("observed_at"))
    pc = pct_change(cur, pri)

    base = {
        "snapshot_file": snapshot_path.name,
        "adapter": ADAPTER_ID,
        "cost_current": cur,
        "cost_prior": pri,
    }
    if pc is None:
        return [
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=TRACTION_SIGNAL,
                observed_at=observed,
                payload={**base, "note": "prior_cost_zero"},
                confidence=0.55,
            )
        ]
    if pc > 0.25:
        sig = COST_SPIKE
        conf = 0.9
    else:
        sig = TRACTION_SIGNAL
        conf = 0.65

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
