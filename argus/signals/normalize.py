"""Deterministic canonical signal normalization (collection-time).

**Product signal manifest** (``signals.yaml`` / inline ``product.yaml``) participates in:

- Matching records to declarations (:func:`attach_canonical_to_records` /
  :func:`argus.signals.manifest_bridge.match_manifest_entry` and explicit
  ``payload.manifest_signal_id``).
- **Category** and **trust** on canonical rows (:mod:`argus.signals.category_mapping`).
- **freshness_sla** string on canonical rows — see :func:`resolve_freshness_sla`.

**freshness_sla precedence** (first non-empty wins; all deterministic):

1. Matched **manifest entry** ``freshness_sla`` (product declaration).
2. Adapter **payload** ``freshness_metadata`` (seconds → ``Ns``, or string ``sla``).
3. Adapter **payload** top-level ``freshness_sla`` (number → ``Ns``, or non-empty string).

Collection-time **observation recency** (``canonical.freshness_status``: realtime / recent / …)
uses :func:`argus.temporal.recency.compute_freshness` against ``collected_at`` — separate from SLA text.

**Local snapshot / adapter manifests** (which files parsers bind to) live under
``argus/signals/snapshots/`` and ``config/adapters.json`` — not the product manifest above.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import ProductSignalManifest, ProductSignalManifestEntry
from argus.signals.category_mapping import resolve_canonical_category
from argus.signals.manifest_bridge import (
    canonical_trust_from_manifest,
    match_manifest_entry,
)
from argus.signals.manifest_collect import PLACEHOLDER_SOURCE
from argus.temporal.recency import compute_freshness

# Recorded on ``canonical.provenance`` when SLA string is set.
SLA_SOURCE_MANIFEST = "product_signal_manifest"
SLA_SOURCE_PAYLOAD_METADATA = "payload_freshness_metadata"
SLA_SOURCE_PAYLOAD_TOP_NUMERIC = "payload_freshness_sla_seconds"
SLA_SOURCE_PAYLOAD_TOP_STRING = "payload_freshness_sla_string"
SLA_SOURCE_NONE = "none"


def _iso(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _infer_collection_status(record: SignalRecord) -> str:
    p = record.payload if isinstance(record.payload, dict) else {}
    explicit = p.get("collection_status")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    tags = {str(t).lower() for t in (record.tags or [])}
    if p.get("error") is not None and p.get("error") != "":
        return "error"
    if "io_error" in tags or "parse_error" in tags:
        return "error"
    if p.get("ok") is False and (p.get("error") or ""):
        return "error"
    note = str(p.get("note") or "").lower()
    if "no " in note and "found" in note:
        return "missing"
    if p.get("ok") is False:
        return "degraded"
    return "ok"


def _infer_value_and_type(record: SignalRecord, status: str) -> tuple[Any, str]:
    if status == "error":
        return None, "error"
    if status in ("missing", "unsupported_source_type"):
        return None, "missing"
    p = record.payload if isinstance(record.payload, dict) else {}
    if status == "degraded":
        return None, "unknown"

    if "data" in p and isinstance(p["data"], dict):
        return p["data"], "object"
    if "samples" in p:
        return p.get("samples"), "array"
    if "line_count" in p or "format" in p:
        return {k: p[k] for k in ("format", "line_count", "file") if k in p} or None, "object"
    # Scalar-ish
    for key in ("value", "count", "mrr", "page_views"):
        if key in p and p[key] is not None:
            v = p[key]
            if isinstance(v, bool):
                return v, "boolean"
            if isinstance(v, (int, float)):
                return v, "number"
            if isinstance(v, str):
                return v, "string"
    return p if p else None, "object" if p else "missing"


def _unit_from_payload(payload: dict[str, Any]) -> str | None:
    for k in ("unit", "metric_unit", "currency"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def resolve_canonical_unit(
    payload: dict[str, Any],
    manifest_entry: ProductSignalManifestEntry | None,
) -> tuple[str | None, str]:
    """Manifest ``unit`` overrides payload; second value is ``unit_source`` for provenance."""
    if manifest_entry is not None:
        u = manifest_entry.unit
        if isinstance(u, str) and u.strip():
            return u.strip(), "product_signal_manifest"
    p = _unit_from_payload(payload)
    if p:
        return p, "payload"
    return None, "none"


def _source_type_from_adapter(source: str, *, legacy_signal_type: str) -> str:
    s = source.lower()
    if s == PLACEHOLDER_SOURCE:
        return legacy_signal_type
    if s in ("filesystem", "metrics_file", "cost_file", "analytics_file", "heartbeat"):
        return "filesystem" if s == "filesystem" else "adapter"
    if s == "execution":
        return "execution"
    if "snapshot" in s or s == "temporal_snapshots":
        return "snapshot"
    if s == "adapter_layer":
        return "adapter"
    return "adapter"


def _trust_level(confidence: float | None) -> str:
    if confidence is None:
        return "unverified"
    if confidence >= 0.85:
        return "high"
    if confidence >= 0.5:
        return "medium"
    return "low"


def resolve_freshness_sla(
    payload: dict[str, Any],
    manifest_entry: ProductSignalManifestEntry | None,
) -> tuple[str | None, str]:
    """
    Return ``(sla_string_or_none, sla_source_key)`` using fixed precedence.

    ``sla_source_key`` is one of :mod:`argus.signals.normalize` ``SLA_SOURCE_*`` constants.
    """
    if manifest_entry is not None:
        m = manifest_entry.freshness_sla.strip() if manifest_entry.freshness_sla else ""
        if m:
            return m, SLA_SOURCE_MANIFEST
    s = _freshness_sla_from_payload(payload)
    if s:
        return s, SLA_SOURCE_PAYLOAD_METADATA
    raw = payload.get("freshness_sla")
    if isinstance(raw, (int, float)) and raw > 0:
        return f"{int(raw)}s", SLA_SOURCE_PAYLOAD_TOP_NUMERIC
    if isinstance(raw, str) and raw.strip():
        return raw.strip(), SLA_SOURCE_PAYLOAD_TOP_STRING
    return None, SLA_SOURCE_NONE


def _freshness_sla_from_payload(payload: dict[str, Any]) -> str | None:
    fm = payload.get("freshness_metadata")
    if not isinstance(fm, dict):
        return None
    # common shapes from temporal.freshness
    for k in ("max_age_seconds", "sla_seconds", "ttl_seconds"):
        v = fm.get(k)
        if isinstance(v, (int, float)):
            return f"{int(v)}s"
    s = fm.get("sla")
    if isinstance(s, str) and s.strip():
        return s.strip()
    return None


def _window_from_payload(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    def _one(key: str) -> str | None:
        v = payload.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
        return None

    ws = _one("source_window_start") or _one("window_start")
    we = _one("source_window_end") or _one("window_end")
    return ws, we


def build_canonical_signal(
    record: SignalRecord,
    *,
    collected_at: datetime,
    manifest_entry: ProductSignalManifestEntry | None = None,
) -> CanonicalSignal:
    """Build a :class:`CanonicalSignal` from a legacy record (deterministic, no I/O)."""
    p = record.payload if isinstance(record.payload, dict) else {}
    status = _infer_collection_status(record)
    value, value_type = _infer_value_and_type(record, status)
    ref_time = collected_at if collected_at.tzinfo else collected_at.replace(tzinfo=timezone.utc)
    _score, bucket = compute_freshness(record.observed_at, reference_time=ref_time)
    freshness_status = bucket.value

    ws, we = _window_from_payload(p)
    prov = {
        "adapter_source": record.source,
        "legacy_signal_type": record.signal_type.value,
        "tags": list(record.tags or []),
    }
    if isinstance(p, dict) and p:
        prov["payload_keys"] = sorted(p.keys())[:32]

    category, category_source = resolve_canonical_category(record, manifest_entry)
    prov["category_source"] = category_source
    if manifest_entry is not None:
        prov["manifest_signal_id"] = manifest_entry.id

    trust_level = (
        canonical_trust_from_manifest(manifest_entry.trust_level)
        if manifest_entry is not None
        else _trust_level(record.confidence)
    )

    sla_str, sla_src = resolve_freshness_sla(p, manifest_entry)
    prov["freshness_sla_source"] = sla_src
    unit_str, unit_src = resolve_canonical_unit(p, manifest_entry)
    if unit_src != "none":
        prov["unit_source"] = unit_src

    return CanonicalSignal(
        signal_id=record.id,
        product_id=record.product_id,
        category=category,
        value=value,
        value_type=value_type,
        unit=unit_str,
        source_type=_source_type_from_adapter(
            record.source,
            legacy_signal_type=record.signal_type.value,
        ),
        source_ref=str(p.get("file") or p.get("path") or record.source or "unknown"),
        provenance=prov,
        observed_at=_iso(record.observed_at),
        collected_at=_iso(collected_at),
        source_window_start=ws,
        source_window_end=we,
        freshness_sla=sla_str,
        freshness_status=freshness_status,
        trust_level=trust_level,
        collection_status=status,
    )


def _manifest_entry_for_record(
    record: SignalRecord,
    entries: list[ProductSignalManifestEntry],
    product_root: Path | None,
) -> ProductSignalManifestEntry | None:
    """Resolve manifest row for a record: explicit ``manifest_signal_id`` or first match."""
    p = record.payload if isinstance(record.payload, dict) else {}
    mid = p.get("manifest_signal_id")
    if isinstance(mid, str) and mid.strip():
        sid = mid.strip()
        for e in entries:
            if e.id == sid:
                return e
    if entries:
        return match_manifest_entry(record, entries=entries, product_root=product_root)
    return None


def attach_canonical_to_records(
    records: list[SignalRecord],
    collected_at: datetime,
    *,
    signal_manifest: ProductSignalManifest | None = None,
    product_root: Path | None = None,
) -> list[SignalRecord]:
    """Return new list with ``canonical`` set on each record."""
    entries = list(signal_manifest.signals) if signal_manifest is not None else []
    out: list[SignalRecord] = []
    for r in records:
        ment = (
            _manifest_entry_for_record(r, entries, product_root)
            if entries
            else None
        )
        canon = build_canonical_signal(r, collected_at=collected_at, manifest_entry=ment)
        out.append(replace(r, canonical=canon))
    return out
