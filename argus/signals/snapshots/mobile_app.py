"""
Mobile / app store metrics snapshot (local JSON/CSV).

JSON::

    {
      "product_id": "optional",
      "observed_at": "...",
      "dau": {"current": 3, "prior": 400},
      "retention_d7": {"current": 0.05, "prior": 0.22}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.binding import parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.models import NO_USAGE, RETENTION_DROP, TRACTION_SIGNAL
from argus.signals.snapshots.normalize import business_signal_record, parse_observed_at, pct_change

ADAPTER_ID = "mobile_app_metrics_snapshot"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    return float(x)


def ingest_mobile_app_snapshot(snapshot_path: Path, repo_root: Path) -> list[SignalRecord]:
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

    dau_o = data.get("dau") if isinstance(data.get("dau"), dict) else None
    cur_d = _num(dau_o.get("current") if dau_o else data.get("dau_current"))
    pri_d = _num(dau_o.get("prior") if dau_o else data.get("dau_prior"))

    ret = data.get("retention_d7") if isinstance(data.get("retention_d7"), dict) else None
    cur_r = _num(ret.get("current") if ret else data.get("retention_d7_current"))
    pri_r = _num(ret.get("prior") if ret else data.get("retention_d7_prior"))

    observed = parse_observed_at(data.get("observed_at"))
    out: list[SignalRecord] = []

    d_pc = pct_change(cur_d, pri_d)
    if cur_d < 10 and pri_d > 50 and (d_pc is None or d_pc < -0.5):
        out.append(
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=NO_USAGE,
                observed_at=observed,
                payload={
                    "snapshot_file": snapshot_path.name,
                    "adapter": ADAPTER_ID,
                    "dau_current": cur_d,
                    "dau_prior": pri_d,
                },
                confidence=0.88,
            )
        )

    r_pc = pct_change(cur_r, pri_r) if pri_r > 0 else None
    if r_pc is not None and r_pc < -0.25:
        out.append(
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=RETENTION_DROP,
                observed_at=observed,
                payload={
                    "snapshot_file": snapshot_path.name,
                    "adapter": ADAPTER_ID,
                    "retention_d7_current": cur_r,
                    "retention_d7_prior": pri_r,
                    "pct_change": round(r_pc, 6),
                },
                confidence=0.86,
            )
        )

    if not out:
        out.append(
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=TRACTION_SIGNAL,
                observed_at=observed,
                payload={
                    "snapshot_file": snapshot_path.name,
                    "adapter": ADAPTER_ID,
                    "note": "no_mobile_alert_threshold",
                    "dau_current": cur_d,
                    "dau_prior": pri_d,
                },
                confidence=0.58,
            )
        )
    return out
