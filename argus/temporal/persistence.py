"""Filesystem layout for temporal signal artifacts (mirrors ``runs/signals/`` patterns)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.core.serialize import dumps_json
from argus.temporal.enrichment import bundle_payload_dict, temporal_signals_from_records


def temporal_collections_base(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "temporal" / "collections"


def temporal_latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "temporal" / "latest" / f"{product_id}.json"


def temporal_summary_path(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "temporal" / "summary.json"


def write_temporal_artifacts_for_collection(
    repo_root: Path,
    product_id: str,
    records: list[SignalRecord],
    collected_at: datetime,
    *,
    reference_time: datetime | None = None,
    signal_continuity: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    """
    Write temporal bundle for one product collection run.

    Returns ``(collections_path, latest_path)``.
    """
    root = repo_root.resolve()
    temporal = temporal_signals_from_records(
        records,
        fetched_at=collected_at,
        reference_time=reference_time,
    )
    iso = collected_at.isoformat()
    payload = bundle_payload_dict(
        product_id=product_id,
        repo_root=str(root),
        collected_at_utc=iso,
        temporal_signals=temporal,
        signal_continuity=signal_continuity,
    )
    ts = collected_at.strftime("%Y%m%dT%H%M%SZ")
    base = temporal_collections_base(root)
    base.mkdir(parents=True, exist_ok=True)
    coll_path = base / f"{ts}_{product_id}.json"
    coll_path.write_text(dumps_json(payload), encoding="utf-8")

    latest = temporal_latest_path(root, product_id)
    latest.parent.mkdir(parents=True, exist_ok=True)
    latest.write_text(dumps_json(payload), encoding="utf-8")
    return coll_path, latest


def load_latest_temporal_bundle(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = temporal_latest_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def refresh_temporal_from_signals_latest(
    repo_root: Path,
    product_id: str,
    *,
    reference_time: datetime | None = None,
) -> bool:
    """
    Recompute temporal bundle from ``runs/signals/latest/<product_id>.json`` without re-running adapters.
    """
    from argus.signals.persistence import load_latest_bundle

    b = load_latest_bundle(repo_root, product_id)
    if b is None:
        return False
    collected = datetime.fromisoformat(b.collected_at_utc.replace("Z", "+00:00"))
    write_temporal_artifacts_for_collection(
        repo_root,
        product_id,
        b.records,
        collected,
        reference_time=reference_time,
        signal_continuity=b.signal_continuity,
    )
    return True
