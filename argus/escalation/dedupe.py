"""De-duplicate noisy escalation packets (same product + same trigger set within a time window)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.escalation.packet import latest_dir


def _parse_created_at(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def rule_fingerprint(rule_ids: list[str]) -> str:
    """Stable key for a set of escalation rule ids."""
    return "|".join(sorted({str(x).strip() for x in rule_ids if str(x).strip()}))


def find_recent_duplicate_packet(
    repo_root: Path,
    product_id: str,
    rule_ids: list[str],
    *,
    hours: float = 24.0,
) -> dict[str, Any] | None:
    """
    If a packet exists under ``runs/escalations/latest/`` for ``product_id`` with the same
    ``triggering_rules`` (order-independent) and ``created_at`` within ``hours``, return its index row.
    """
    root = repo_root.resolve()
    lat = latest_dir(root)
    if not lat.is_dir():
        return None
    fp = rule_fingerprint(rule_ids)
    if not fp:
        return None
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    for p in sorted(lat.glob("esc_*.json"), key=lambda x: x.stat().st_mtime_ns, reverse=True):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        if str(data.get("product_id") or "") != product_id:
            continue
        tr = data.get("triggering_rules") or []
        if not isinstance(tr, list):
            continue
        if rule_fingerprint([str(x) for x in tr]) != fp:
            continue
        dt = _parse_created_at(data.get("created_at"))
        if dt is None or dt < cutoff:
            continue
        return {
            "packet_id": data.get("packet_id", p.stem),
            "created_at": data.get("created_at"),
            "path_repo": str(p.relative_to(root)),
        }
    return None


def summarize_escalation_groups(
    repo_root: Path,
    *,
    limit_packets: int = 500,
) -> dict[str, Any]:
    """
    Group recent packets by ``(product_id, rule_fingerprint)`` for operator dashboards.

    Preserves per-product detail via ``examples`` (latest packet id per group).
    """
    root = repo_root.resolve()
    lat = latest_dir(root)
    groups: dict[str, dict[str, Any]] = {}
    if not lat.is_dir():
        return {"schema": "argus.escalation_dedupe_summary.v1", "groups": [], "total_packets_seen": 0}

    rows: list[Path] = sorted(
        lat.glob("esc_*.json"),
        key=lambda x: x.stat().st_mtime_ns,
        reverse=True,
    )[:limit_packets]

    for p in rows:
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        pid = str(data.get("product_id") or "")
        tr = data.get("triggering_rules") or []
        if not isinstance(tr, list):
            continue
        fp = rule_fingerprint([str(x) for x in tr])
        key = f"{pid}::{fp}"
        if key not in groups:
            groups[key] = {
                "product_id": pid,
                "rule_fingerprint": fp,
                "count": 0,
                "latest_packet_id": data.get("packet_id"),
                "latest_created_at": data.get("created_at"),
            }
        groups[key]["count"] += 1

    out = sorted(groups.values(), key=lambda g: (g["count"], g.get("product_id") or ""), reverse=True)
    return {
        "schema": "argus.escalation_dedupe_summary.v1",
        "groups": out[:80],
        "total_packets_seen": len(rows),
    }
