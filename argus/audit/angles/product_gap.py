"""Product Gap angle — declared vs on-disk coverage (four-state enum)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.audit.capability_map import build_capability_entries
from argus.audit.models import AuditSummary, now_utc
from argus.audit.scanner import bounded_walk, fingerprint_inputs
from argus.audit.summarize import counts_by_status, ensure_all_status_keys
from argus.core.models.product import ProductNode


def _summary_lines_from_summary(summary: AuditSummary) -> list[str]:
    c = summary.counts_by_status
    impl = c.get("implemented", 0)
    miss = c.get("missing", 0)
    part = c.get("partial", 0)
    unk = c.get("unknown", 0)
    lines = [
        f"product_gap: {impl} implemented, {miss} missing, {part} partial, {unk} unknown",
        f"product_gap: scan_depth={summary.scan_depth} fp={summary.inputs_fingerprint[:12]}",
    ]
    return lines[:8]


def run_product_gap_angle(
    repo_root: Path,
    product_id: str,
    product_root: Path,
    node: ProductNode,
    *,
    generated_at_utc: str | None = None,
) -> tuple[AuditSummary, dict[str, Any]]:
    """
    Build :class:`AuditSummary` and the ``angles.product_gap`` payload.

    ``summary_lines`` follow ``signal: value`` convention.
    """
    scanned, excluded = bounded_walk(product_root)
    scanned_rel: list[str] = []
    for s in scanned[:300]:
        scanned_rel.append(f"products/{product_id}/{s}")

    fp = fingerprint_inputs(repo_root, product_root, node)
    entries = build_capability_entries(product_id, product_root, node)
    counts = ensure_all_status_keys(counts_by_status(entries))

    ts = generated_at_utc or now_utc()
    notes: list[str] = [
        "quick scan only; unknown != missing",
        "no Cursor/deep analysis",
    ]
    summary = AuditSummary(
        product_id=product_id,
        generated_at_utc=ts,
        scan_depth="quick",
        scanned_paths=scanned_rel[:300],
        excluded_paths=excluded,
        capabilities=entries,
        counts_by_status=counts,
        notes=notes,
        inputs_fingerprint=fp,
    )

    lines = _summary_lines_from_summary(summary)
    angle: dict[str, Any] = {
        "schema": "argus.audit_angle.product_gap.v1",
        "angle_status": "active",
        "summary_lines": lines,
        "scan_depth": summary.scan_depth,
        "scanned_paths": summary.scanned_paths,
        "excluded_paths": summary.excluded_paths,
        "capabilities": [c.to_dict() for c in summary.capabilities],
        "counts_by_status": dict(summary.counts_by_status),
        "notes": list(summary.notes),
        "inputs_fingerprint": fp,
    }
    return summary, angle
