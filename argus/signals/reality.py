"""Deterministic signal “reality” classification from manifests, canonical rows, and recency metadata."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any

from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import ProductSignalManifest, ProductSignalManifestEntry
from argus.core.serialize import dumps_json
from argus.signals.manifest_bridge import match_manifest_entry
from argus.signals.persistence import SignalCollectionBundle, load_latest_bundle
from argus.temporal.persistence import temporal_latest_path

SIGNAL_REALITY_SCHEMA = "argus.signal_reality.v1"


class SignalRealityStatus(StrEnum):
    """Conservative operational classification — not business semantics."""

    REAL = "real"
    SHALLOW_REAL = "shallow_real"
    DECLARED_MISSING = "declared_missing"
    INVALID = "invalid"
    STALE = "stale"
    DEPRECATED = "deprecated"


def _payload(record: SignalRecord) -> dict[str, Any]:
    p = record.payload
    return p if isinstance(p, dict) else {}


def classify_signal_reality(
    record: SignalRecord,
    *,
    manifest_entry: ProductSignalManifestEntry | None,
) -> tuple[SignalRealityStatus, list[str]]:
    """
    Single-record classification. Rules are evaluated in a fixed order; first match wins.

    Uses :attr:`SignalRecord.canonical` when present; callers should normalize first.
    """
    c = record.canonical
    p = _payload(record)

    if c is None:
        return SignalRealityStatus.INVALID, ["no_canonical"]

    if manifest_entry is not None and not manifest_entry.enabled:
        return SignalRealityStatus.DEPRECATED, ["manifest_entry_disabled"]

    if p.get("deprecated") is True:
        return SignalRealityStatus.DEPRECATED, ["payload_deprecated_true"]

    if c.collection_status == "error" or c.value_type == "error":
        return SignalRealityStatus.INVALID, ["canonical_collection_or_value_error"]

    if c.collection_status == "missing" and manifest_entry is not None:
        return SignalRealityStatus.DECLARED_MISSING, ["canonical_collection_missing_with_manifest"]

    if c.freshness_status in ("aging", "stale"):
        return SignalRealityStatus.STALE, ["canonical_freshness_bucket_stale_or_aging"]

    if c.collection_status == "degraded":
        return SignalRealityStatus.SHALLOW_REAL, ["canonical_collection_degraded"]

    if c.freshness_status == "unknown":
        return SignalRealityStatus.SHALLOW_REAL, ["canonical_freshness_bucket_unknown"]

    if c.trust_level in ("low", "unverified") and c.collection_status == "ok":
        return SignalRealityStatus.SHALLOW_REAL, ["trust_level_not_medium_or_high"]

    if (
        c.collection_status == "ok"
        and c.freshness_status in ("realtime", "recent")
        and c.trust_level in ("high", "medium")
    ):
        return SignalRealityStatus.REAL, ["ok_fresh_trust_medium_or_high"]

    if c.collection_status == "ok":
        return SignalRealityStatus.SHALLOW_REAL, ["ok_fallback_not_promoted_to_real"]

    return SignalRealityStatus.SHALLOW_REAL, ["fallback_conservative"]


@dataclass(frozen=True)
class ManifestGapRow:
    manifest_signal_id: str
    reality_status: SignalRealityStatus
    reasons: list[str]


def manifest_gaps(
    *,
    manifest: ProductSignalManifest | None,
    records: list[SignalRecord],
    product_root: Path | None,
) -> list[ManifestGapRow]:
    """Enabled manifest entries with no matching collected row in this bundle."""
    if manifest is None or not manifest.signals:
        return []
    entries = list(manifest.signals)
    matched: set[str] = set()
    for r in records:
        e = match_manifest_entry(r, entries=entries, product_root=product_root)
        if e is not None:
            matched.add(e.id)
    out: list[ManifestGapRow] = []
    for e in entries:
        if not e.enabled:
            continue
        if e.id not in matched:
            out.append(
                ManifestGapRow(
                    manifest_signal_id=e.id,
                    reality_status=SignalRealityStatus.DECLARED_MISSING,
                    reasons=["no_record_matched_manifest_entry"],
                )
            )
    return out


def _temporal_sidecar_summary(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = temporal_latest_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        import json

        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"path": str(p), "readable": False}
    if not isinstance(raw, dict):
        return {"path": str(p), "readable": False}
    return {
        "path": str(p),
        "readable": True,
        "schema": raw.get("schema"),
        "record_count": raw.get("record_count"),
        "collected_at_utc": raw.get("collected_at_utc"),
    }


def build_signal_reality_report(
    repo_root: Path,
    bundle: SignalCollectionBundle,
    *,
    manifest: ProductSignalManifest | None,
    product_root: Path | None,
) -> dict[str, Any]:
    """Build inspectable JSON report (deterministic)."""
    entries = list(manifest.signals) if manifest is not None else []
    rows: list[dict[str, Any]] = []
    summary: dict[str, int] = {s.value: 0 for s in SignalRealityStatus}

    for r in bundle.records:
        ment: ProductSignalManifestEntry | None = None
        if entries:
            ment = match_manifest_entry(
                r,
                entries=entries,
                product_root=product_root,
                include_disabled=True,
            )
        status, reasons = classify_signal_reality(r, manifest_entry=ment)
        summary[status.value] = summary.get(status.value, 0) + 1
        mid = None
        if ment is not None:
            mid = ment.id
        elif r.canonical and r.canonical.provenance.get("manifest_signal_id"):
            mid = str(r.canonical.provenance.get("manifest_signal_id"))
        rows.append(
            {
                "scope": "record",
                "signal_id": r.id,
                "manifest_signal_id": mid,
                "reality_status": status.value,
                "reasons": reasons,
                "canonical": (
                    {
                        "collection_status": r.canonical.collection_status,
                        "freshness_status": r.canonical.freshness_status,
                        "trust_level": r.canonical.trust_level,
                        "value_type": r.canonical.value_type,
                    }
                    if r.canonical is not None
                    else None
                ),
            }
        )

    gaps = manifest_gaps(manifest=manifest, records=bundle.records, product_root=product_root)
    gap_json = [
        {
            "scope": "manifest_gap",
            "manifest_signal_id": g.manifest_signal_id,
            "reality_status": g.reality_status.value,
            "reasons": g.reasons,
        }
        for g in gaps
    ]
    for g in gaps:
        summary[g.reality_status.value] = summary.get(g.reality_status.value, 0) + 1

    gen_at = datetime.now(timezone.utc).isoformat()
    return {
        "schema": SIGNAL_REALITY_SCHEMA,
        "product_id": bundle.product_id,
        "generated_at_utc": gen_at,
        "source": {
            "bundle_collected_at_utc": bundle.collected_at_utc,
            "bundle_path_convention": f"runs/signals/latest/{bundle.product_id}.json",
        },
        "temporal_sidecar": _temporal_sidecar_summary(repo_root.resolve(), bundle.product_id),
        "record_classifications": rows,
        "manifest_gaps": gap_json,
        "summary": summary,
    }


def write_signal_reality_report(repo_root: Path, report: dict[str, Any]) -> Path:
    """Write ``runs/signals/reality/latest/<product_id>.json``."""
    root = repo_root.resolve()
    pid = str(report.get("product_id") or "")
    base = root / "runs" / "signals" / "reality" / "latest"
    base.mkdir(parents=True, exist_ok=True)
    out = base / f"{pid}.json"
    out.write_text(dumps_json(report), encoding="utf-8")
    return out


def load_or_classify(
    repo_root: Path,
    product_id: str,
    *,
    manifest: ProductSignalManifest | None,
    product_root: Path | None,
) -> dict[str, Any] | None:
    """
    Load latest signal bundle; ensure canonical rows (re-normalize if missing) and build report.
    """
    b = load_latest_bundle(repo_root, product_id)
    if b is None:
        return None
    from argus.signals.normalize import attach_canonical_to_records

    if any(r.canonical is None for r in b.records):
        from argus.core.models.validation import validate_signal_record

        try:
            collected_at = datetime.fromisoformat(b.collected_at_utc.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            collected_at = datetime.now(timezone.utc)
        norm = attach_canonical_to_records(
            b.records,
            collected_at,
            signal_manifest=manifest,
            product_root=product_root,
        )
        for r in norm:
            validate_signal_record(r)
        b = SignalCollectionBundle(
            product_id=b.product_id,
            collected_at_utc=b.collected_at_utc,
            repo_root=b.repo_root,
            records=norm,
            signal_continuity=b.signal_continuity,
            external_identity_verification=b.external_identity_verification,
        )
    # Fix source field in report — avoid docstring artifact
    return build_signal_reality_report(
        repo_root,
        b,
        manifest=manifest,
        product_root=product_root,
    )


