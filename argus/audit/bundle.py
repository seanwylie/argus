"""Multi-angle audit bundle (argus.audit_bundle.v1)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from argus.audit.models import AuditCapabilityEntry, AuditSummary

BUNDLE_SCHEMA = "argus.audit_bundle.v1"

# Stable ordering — all keys must appear in every bundle.
ANGLE_IDS: tuple[str, ...] = (
    "product_gap",
    "cost",
    "quality",
    "security",
    "compliance",
    "reliability",
    "performance",
    "store_business",
    "ux",
)


def bundle_audit_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "audit" / product_id / "bundle.json"


def compute_bundle_fingerprint(by_angle: dict[str, str]) -> str:
    keys = sorted(by_angle.keys())
    payload = "|".join(f"{k}:{by_angle[k]}" for k in keys)
    return hashlib.sha256(payload.encode()).hexdigest()[:24]


def audit_summary_from_bundle_dict(bundle: dict[str, Any]) -> AuditSummary | None:
    """Reconstruct Product Gap :class:`AuditSummary` from a bundle dict."""
    if str(bundle.get("schema", "")) != BUNDLE_SCHEMA:
        return None
    angles = bundle.get("angles") or {}
    pg = angles.get("product_gap")
    if not isinstance(pg, dict):
        return None
    pid = str(bundle.get("product_id", ""))
    caps = pg.get("capabilities") or []
    capabilities = [AuditCapabilityEntry.from_dict(x) for x in caps if isinstance(x, dict)]
    return AuditSummary(
        product_id=pid,
        generated_at_utc=str(bundle.get("generated_at_utc", "")),
        scan_depth=str(pg.get("scan_depth", "quick")),
        scanned_paths=[str(x) for x in (pg.get("scanned_paths") or [])],
        excluded_paths=[str(x) for x in (pg.get("excluded_paths") or [])],
        capabilities=capabilities,
        counts_by_status={str(k): int(v) for k, v in (pg.get("counts_by_status") or {}).items()},
        notes=[str(x) for x in (pg.get("notes") or [])],
        inputs_fingerprint=str(pg.get("inputs_fingerprint", "")),
        schema="argus.audit_summary.v1",
    )


def load_audit_bundle(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = bundle_audit_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def audit_coverage_from_bundle(bundle: dict[str, Any]) -> dict[str, list[str]]:
    """Partition angle ids by angle_status."""
    implemented: list[str] = []
    partial: list[str] = []
    stub: list[str] = []
    angles = bundle.get("angles") or {}
    for aid in ANGLE_IDS:
        a = angles.get(aid)
        if not isinstance(a, dict):
            continue
        st = str(a.get("angle_status", "stub"))
        if st == "active":
            implemented.append(aid)
        elif st == "partial":
            partial.append(aid)
        else:
            stub.append(aid)
    return {
        "implemented_angles": implemented,
        "partial_angles": partial,
        "stub_angles": stub,
    }


def compact_angle_lines_for_context(
    bundle: dict[str, Any],
    *,
    max_lines_per_angle: int = 6,
    max_angles: int = 9,
) -> dict[str, list[str]]:
    """summary_lines only per angle, capped — no raw payload."""
    out: dict[str, list[str]] = {}
    angles = bundle.get("angles") or {}
    for aid in ANGLE_IDS[:max_angles]:
        a = angles.get(aid)
        if not isinstance(a, dict):
            continue
        sl = a.get("summary_lines") or []
        if isinstance(sl, list):
            out[aid] = [str(x) for x in sl[:max_lines_per_angle]]
    return out
