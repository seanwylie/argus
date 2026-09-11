"""Match collected :class:`SignalRecord` rows to product ``signal_manifest`` declarations."""

from __future__ import annotations

from pathlib import Path

from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import ManifestTrustLevel, ProductSignalManifestEntry


def _norm_path_fragment(s: str) -> str:
    return s.strip().replace("\\", "/").lstrip("./")


def _path_matches(
    rel: str,
    manifest_path: str,
    product_root: Path | None,
) -> bool:
    mp = _norm_path_fragment(manifest_path)
    if not rel or not mp:
        return False
    if rel == mp or rel.endswith("/" + mp):
        return True
    if product_root is not None:
        try:
            cand = (product_root / rel).resolve()
            target = (product_root / mp).resolve()
            return cand == target
        except (OSError, ValueError):
            return False
    return False


def _ref_matches(
    payload: dict[str, object],
    record: SignalRecord,
    source_ref: str,
) -> bool:
    eref = source_ref.strip()
    if not eref:
        return False
    psr = str(payload.get("source_ref") or "").strip()
    if psr and psr == eref:
        return True
    if record.source.strip() == eref:
        return True
    if psr and (psr.endswith(eref) or eref in psr):
        return True
    if record.source.endswith(eref):
        return True
    return False


def match_manifest_entry(
    record: SignalRecord,
    *,
    entries: list[ProductSignalManifestEntry],
    product_root: Path | None,
    include_disabled: bool = False,
) -> ProductSignalManifestEntry | None:
    """
    First manifest entry that matches this record (manifest list order).

    Requires ``entry.source_type == record.signal_type``. Then:
    - If only ``path`` is set: path match.
    - If only ``source_ref`` is set: source_ref match.
    - If both are set: both must match.

    By default only **enabled** entries are considered. Use ``include_disabled=True`` when
    classifying rows against deprecated (disabled) declarations.
    """
    p = record.payload if isinstance(record.payload, dict) else {}
    rel = _norm_path_fragment(str(p.get("file") or p.get("path") or ""))

    for e in entries:
        if not include_disabled and not e.enabled:
            continue
        if e.source_type != record.signal_type:
            continue
        has_path = bool(e.path and str(e.path).strip())
        has_ref = bool(e.source_ref and str(e.source_ref).strip())

        path_ok = _path_matches(rel, str(e.path or ""), product_root) if has_path else True
        ref_ok = _ref_matches(p, record, str(e.source_ref or "")) if has_ref else True

        if has_path and has_ref:
            if path_ok and ref_ok:
                return e
        elif has_path:
            if path_ok:
                return e
        elif has_ref:
            if ref_ok:
                return e

    return None


def canonical_trust_from_manifest(t: ManifestTrustLevel) -> str:
    """Map manifest trust to canonical ``trust_level`` strings."""
    return {
        ManifestTrustLevel.AUTHORITATIVE: "high",
        ManifestTrustLevel.DERIVED: "medium",
        ManifestTrustLevel.HEURISTIC: "low",
        ManifestTrustLevel.UNKNOWN: "unverified",
    }[t]
