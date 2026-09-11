"""
Deterministic signal continuity between successive collections (no forecasting).

Compares prior ``runs/signals/latest`` snapshot to the current normalized batch using
stable continuity keys (manifest id or source/type/ref), not per-run UUID ``SignalRecord.id``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.temporal.recency import bucket_for_age, observation_age_seconds

CONTINUITY_SCHEMA = "argus.signal_continuity.v1"
_MAX_EVENTS = 200


def _utc_parse(iso: str | None) -> datetime | None:
    if not iso or not str(iso).strip():
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def continuity_key(record: SignalRecord) -> str:
    """
    Stable key for the same logical signal across runs (deterministic, no UUID).

    Prefers manifest signal id; else canonical category + source + source_ref; else legacy payload hints.
    """
    c = record.canonical
    if c is not None:
        prov = c.provenance if isinstance(c.provenance, dict) else {}
        mid = prov.get("manifest_signal_id")
        if isinstance(mid, str) and mid.strip():
            return f"m:{record.product_id}:{mid.strip()}"
        ref = (c.source_ref or "").strip() or "unknown"
        cat = (c.category or "").strip() or record.signal_type.value
        return f"c:{record.product_id}|{record.signal_type.value}|{record.source}|{ref}|{cat}"

    p = record.payload if isinstance(record.payload, dict) else {}
    ref = p.get("file") or p.get("source_ref") or p.get("check") or record.source
    ref_s = str(ref).strip() if ref is not None else ""
    return f"l:{record.product_id}|{record.signal_type.value}|{record.source}|{ref_s}"


def _bucket_for_record(record: SignalRecord, collected_at: datetime) -> str:
    age = observation_age_seconds(record.observed_at, collected_at)
    return bucket_for_age(age).value


def _window_bounds(record: SignalRecord) -> tuple[str | None, str | None]:
    c = record.canonical
    if c is not None:
        ws, we = c.source_window_start, c.source_window_end
        if ws or we:
            return ws, we
    p = record.payload if isinstance(record.payload, dict) else {}
    ws = p.get("source_window_start") or p.get("window_start")
    we = p.get("source_window_end") or p.get("window_end")
    ws_s = str(ws).strip() if isinstance(ws, str) and ws.strip() else None
    we_s = str(we).strip() if isinstance(we, str) and we.strip() else None
    return ws_s, we_s


def _row_stub(record: SignalRecord) -> dict[str, Any]:
    return {
        "continuity_key": continuity_key(record),
        "signal_type": record.signal_type.value,
        "source": record.source,
    }


def _trim(lst: list[Any]) -> list[Any]:
    return lst[:_MAX_EVENTS]


def compute_signal_continuity(
    prior_records: list[SignalRecord],
    current_records: list[SignalRecord],
    *,
    prior_collected_at_utc: str | None,
    current_collected_at: datetime,
) -> dict[str, Any]:
    """
    Compare two collections at ``current_collected_at`` (reference clock for buckets).

    Does not interpret business meaning; only structural presence, bucket regression, and window edges.
    """
    cur_iso = current_collected_at.isoformat()
    compared = prior_collected_at_utc is not None

    cur_map: dict[str, SignalRecord] = {}
    for r in current_records:
        cur_map[continuity_key(r)] = r

    if not compared:
        return {
            "schema": CONTINUITY_SCHEMA,
            "compared": False,
            "prior_collected_at_utc": None,
            "current_collected_at_utc": cur_iso,
            "keys_prior": 0,
            "keys_current": len(cur_map),
            "appeared": [],
            "disappeared": [],
            "freshness_regressed": [],
            "window_continuity_broken": [],
        }

    prior_ref = _utc_parse(prior_collected_at_utc)
    if prior_ref is None:
        prior_ref = current_collected_at

    prior_map: dict[str, SignalRecord] = {}
    for r in prior_records:
        prior_map[continuity_key(r)] = r

    prior_keys = frozenset(prior_map.keys())
    cur_keys = frozenset(cur_map.keys())

    appeared_k = sorted(cur_keys - prior_keys)
    disappeared_k = sorted(prior_keys - cur_keys)

    appeared = _trim([_row_stub(cur_map[k]) for k in appeared_k])
    disappeared = _trim([_row_stub(prior_map[k]) for k in disappeared_k])

    _order = ("realtime", "recent", "aging", "stale", "unknown")

    def _rank(b: str) -> int:
        try:
            return _order.index(b)
        except ValueError:
            return len(_order)

    freshness_regressed: list[dict[str, Any]] = []
    for k in sorted(prior_keys & cur_keys):
        pr = prior_map[k]
        cr = cur_map[k]
        pb = _bucket_for_record(pr, prior_ref)
        cb = _bucket_for_record(cr, current_collected_at)
        if _rank(cb) > _rank(pb):
            freshness_regressed.append(
                {
                    "continuity_key": k,
                    "prior_bucket": pb,
                    "current_bucket": cb,
                }
            )
    freshness_regressed = _trim(freshness_regressed)

    window_broken: list[dict[str, Any]] = []
    for k in sorted(prior_keys & cur_keys):
        pr = prior_map[k]
        cr = cur_map[k]
        pws, pwe = _window_bounds(pr)
        cws, cwe = _window_bounds(cr)
        p_end = _utc_parse(pwe) if pwe else None
        c_start = _utc_parse(cws) if cws else None

        if p_end is not None and c_start is not None and c_start < p_end:
            window_broken.append(
                {
                    "continuity_key": k,
                    "kind": "overlap",
                    "prior_window_end": pwe,
                    "current_window_start": cws,
                }
            )
        had_prior_win = bool((pws or pwe))
        has_cur_win = bool((cws or cwe))
        if had_prior_win and not has_cur_win:
            window_broken.append(
                {
                    "continuity_key": k,
                    "kind": "metadata_lost",
                    "detail": "prior had source window fields; current missing",
                }
            )

    window_broken = _trim(window_broken)

    return {
        "schema": CONTINUITY_SCHEMA,
        "compared": compared,
        "prior_collected_at_utc": prior_collected_at_utc,
        "current_collected_at_utc": cur_iso,
        "keys_prior": len(prior_keys),
        "keys_current": len(cur_keys),
        "appeared": appeared,
        "disappeared": disappeared,
        "freshness_regressed": freshness_regressed,
        "window_continuity_broken": window_broken,
    }
