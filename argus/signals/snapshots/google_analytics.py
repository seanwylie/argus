"""
Google Analytics–style export (local JSON/CSV).

JSON::

    {
      "product_id": "optional",
      "observed_at": "...",
      "sessions": {"current": 5000, "prior": 4800},
      "conversion_rate": {"current": 0.02, "prior": 0.025}
    }
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.signals.snapshots.binding import parse_csv_dicts, resolve_product_id
from argus.signals.snapshots.models import CONVERSION_DOWN, TRACTION_SIGNAL, TRAFFIC_UP
from argus.signals.snapshots.normalize import business_signal_record, parse_observed_at, pct_change

ADAPTER_ID = "google_analytics_snapshot"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    return float(x)


def ingest_google_analytics_snapshot(snapshot_path: Path, repo_root: Path) -> list[SignalRecord]:
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

    sess = data.get("sessions") if isinstance(data.get("sessions"), dict) else None
    cur_s = _num(sess.get("current") if sess else data.get("sessions_current"))
    pri_s = _num(sess.get("prior") if sess else data.get("sessions_prior"))

    cr = data.get("conversion_rate") if isinstance(data.get("conversion_rate"), dict) else None
    cur_c = _num(cr.get("current") if cr else data.get("conversion_rate_current"))
    pri_c = _num(cr.get("prior") if cr else data.get("conversion_rate_prior"))

    observed = parse_observed_at(data.get("observed_at"))
    out: list[SignalRecord] = []

    spc = pct_change(cur_s, pri_s)
    if spc is not None and spc > 0.08:
        out.append(
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=TRAFFIC_UP,
                observed_at=observed,
                payload={
                    "snapshot_file": snapshot_path.name,
                    "adapter": ADAPTER_ID,
                    "metric": "sessions",
                    "sessions_current": cur_s,
                    "sessions_prior": pri_s,
                    "pct_change": round(spc, 6),
                },
                confidence=0.86,
            )
        )

    cpc = pct_change(cur_c, pri_c) if pri_c > 0 else None
    if cpc is not None and cur_c < pri_c * 0.92:
        out.append(
            business_signal_record(
                product_id=pid,
                adapter_source=ADAPTER_ID,
                business_signal=CONVERSION_DOWN,
                observed_at=observed,
                payload={
                    "snapshot_file": snapshot_path.name,
                    "adapter": ADAPTER_ID,
                    "metric": "conversion_rate",
                    "conversion_rate_current": cur_c,
                    "conversion_rate_prior": pri_c,
                    "pct_change": round(cpc, 6),
                },
                confidence=0.84,
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
                    "note": "no_strong_ga_trend",
                    "sessions_current": cur_s,
                    "sessions_prior": pri_s,
                },
                confidence=0.6,
            )
        )
    return out
