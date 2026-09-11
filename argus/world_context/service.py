"""
Build world context payloads from normalized signals (advisory only).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.world_context.creation_candidates import build_creation_candidates_payload
from argus.world_context.interpretation import build_interpretation_payload
from argus.world_context.persist import (
    WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
    WORLD_CONTEXT_INTERPRETATION_SCHEMA,
    WORLD_CONTEXT_SCHEMA,
    creation_candidates_output_dir,
    interpretation_output_dir,
    world_context_output_dir,
    write_creation_candidates_artifact,
    write_interpretation_artifact,
    write_world_context_artifact,
)
from argus.world_context.signal import normalize_signal

ADVISORY_DISCLAIMER = (
    "Advisory only — external signals do not authorize product creation or portfolio decisions."
)


def load_world_context(repo_root: Path) -> dict[str, Any] | None:
    """Read ``runs/world_context/latest.json`` if present and schema matches."""
    p = world_context_output_dir(repo_root) / "latest.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("schema") or "") != WORLD_CONTEXT_SCHEMA:
        return None
    return raw


def load_world_context_creation_candidates(repo_root: Path) -> dict[str, Any] | None:
    """Read ``runs/world_context/creation_candidates/latest.json`` if present and schema matches."""
    p = creation_candidates_output_dir(repo_root) / "latest.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("schema") or "") != WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA:
        return None
    return raw


def load_world_context_interpretation(repo_root: Path) -> dict[str, Any] | None:
    """Read ``runs/world_context/interpretation/latest.json`` if present and schema matches."""
    p = interpretation_output_dir(repo_root) / "latest.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("schema") or "") != WORLD_CONTEXT_INTERPRETATION_SCHEMA:
        return None
    return raw


def write_creation_candidates_from_world_payload(
    repo_root: Path,
    wc_payload: dict[str, Any],
    interpretation: dict[str, Any] | None = None,
) -> Path | None:
    """Derive advisory creation candidates and write ``creation_candidates/latest.json``."""
    interp = interpretation if interpretation is not None else build_interpretation_payload(wc_payload)
    if not interp:
        return None
    payload = build_creation_candidates_payload(wc_payload, interp)
    if not payload:
        return None
    return write_creation_candidates_artifact(repo_root, payload)


def write_interpretation_from_world_payload(repo_root: Path, wc_payload: dict[str, Any]) -> Path | None:
    """Derive interpretation from a world-context payload and write interpretation + creation candidates."""
    interp = build_interpretation_payload(wc_payload)
    if not interp:
        return None
    out = write_interpretation_artifact(repo_root, interp)
    write_creation_candidates_from_world_payload(repo_root, wc_payload, interp)
    return out


def build_world_context_payload(
    signals_in: list[dict[str, Any]],
    *,
    reference_now: datetime | None = None,
    stale_after_hours: float = 168.0,
) -> tuple[dict[str, Any], list[str]]:
    """
    Normalize inputs and build the JSON-serializable artifact body.

    Returns ``(payload, errors)`` where *errors* lists validation failures (one per bad row).
    """
    ref = reference_now or datetime.now(timezone.utc)
    normalized: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, raw in enumerate(signals_in):
        if not isinstance(raw, dict):
            errors.append(f"row {i}: not an object")
            continue
        n, err = normalize_signal(raw, reference_now=ref, stale_after_hours=stale_after_hours)
        if err:
            errors.append(f"row {i}: {err}")
            continue
        assert n is not None
        normalized.append(n)

    sources = sorted({str(s["source"]) for s in normalized})
    fresh_n = sum(1 for s in normalized if s.get("freshness_status") == "fresh")
    stale_n = sum(1 for s in normalized if s.get("freshness_status") == "stale")
    unk_n = sum(1 for s in normalized if s.get("freshness_status") == "unknown")

    headline = _headline_summary(normalized, sources)

    payload: dict[str, Any] = {
        "schema": WORLD_CONTEXT_SCHEMA,
        "generated_at_utc": ref.isoformat().replace("+00:00", "Z"),
        "advisory_only": True,
        "disclaimer": ADVISORY_DISCLAIMER,
        "summary": {
            "signal_count": len(normalized),
            "sources": sources,
            "fresh_count": fresh_n,
            "stale_count": stale_n,
            "unknown_freshness_count": unk_n,
            "headline": headline,
        },
        "signals": normalized,
    }
    return payload, errors


def _headline_summary(signals: list[dict[str, Any]], sources: list[str]) -> str:
    if not signals:
        return "No external signals recorded."
    parts = [f"{len(signals)} advisory signal(s)"]
    if sources:
        parts.append(f"from {', '.join(sources)}")
    types = sorted({str(s.get("signal_type")) for s in signals})
    if types:
        parts.append(f"types: {', '.join(types)}")
    return " — ".join(parts) + "."


def summarize_for_operator(payload: dict[str, Any] | None) -> str | None:
    """Legacy aggregate one-liner (headline + freshness + disclaimer). Prefer :func:`operator_advisory_from_world_context`."""
    if not payload:
        return None
    summ = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    h = str(summ.get("headline") or "").strip()
    if not h:
        return None
    fc = int(summ.get("fresh_count") or 0)
    sc = int(summ.get("stale_count") or 0)
    tail = f" Fresh: {fc}, stale: {sc}. {ADVISORY_DISCLAIMER}"
    return h + tail


def ingest_signals_from_json_file(path: Path) -> list[dict[str, Any]]:
    """Load a JSON array of signal objects from a file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("expected a JSON array of signal objects")
    return [x for x in raw if isinstance(x, dict)]


def write_world_context_from_signals(
    repo_root: Path,
    signals_in: list[dict[str, Any]],
    *,
    stale_after_hours: float = 168.0,
) -> tuple[Path | None, dict[str, Any], list[str]]:
    """Build payload and write artifact only when every row validates."""
    payload, errors = build_world_context_payload(
        signals_in,
        stale_after_hours=stale_after_hours,
    )
    if errors:
        return None, payload, errors
    out = write_world_context_artifact(repo_root, payload)
    write_interpretation_from_world_payload(repo_root, payload)
    return out, payload, []
