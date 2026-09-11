"""Assemble versioned context packets (product.system + artifact.draft for V1)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.audit.bundle import (
    audit_coverage_from_bundle,
    compact_angle_lines_for_context,
    load_audit_bundle,
)
from argus.audit.cache import load_latest_audit
from argus.audit.models import AuditStatus
from argus.context.caps import ContextCaps
from argus.context.purposes import ContextPurpose
from argus.context.sources import doctrine_excerpt, strategy_summary
from argus.core.models.enums import SeverityLevel
from argus.core.serialize import to_jsonable
from argus.decision.stub_awareness import infer_stub_gap_context
from argus.findings.persistence import load_latest_findings
from argus.products.inventory import build_inventory
from argus.products.reporting import format_product_summary
from argus.refinement.models import ArtifactDraft
from argus.temporal.visibility import compute_product_temporal_visibility

PACKET_SCHEMA = "argus.context_bundle.v1"

_SEVERITY_ORDER: dict[str, int] = {
    SeverityLevel.CRITICAL.value: 4,
    SeverityLevel.HIGH.value: 3,
    SeverityLevel.MEDIUM.value: 2,
    SeverityLevel.LOW.value: 1,
    SeverityLevel.INFO.value: 0,
}


def _severity_key(f: Any) -> int:
    try:
        return _SEVERITY_ORDER.get(f.severity.value, 0)
    except Exception:
        return 0


def _compact_finding(f: Any) -> dict[str, Any]:
    return {
        "id": f.id,
        "kind": f.kind.value,
        "severity": f.severity.value,
        "title": (f.title or "")[:400],
        "summary": (f.summary or "")[:600],
    }


def _build_product_system(
    repo_root: Path,
    product_id: str | None,
    purpose: ContextPurpose,
    caps: ContextCaps,
) -> tuple[dict[str, Any], list[str], dict[str, int]]:
    root = repo_root.resolve()
    sources: list[str] = []
    omitted: dict[str, int] = {}

    if not product_id:
        return (
            {
                "present": False,
                "reason": "no product_id",
            },
            [],
            {},
        )

    sources.append(f"products/{product_id}/product.yaml")

    inv = build_inventory(root)
    if product_id not in inv.valid:
        return (
            {"present": False, "reason": f"product {product_id!r} not in inventory"},
            sources,
            {},
        )

    node = inv.valid[product_id].node
    prod_summary = format_product_summary(node)[: caps.max_product_summary_chars]
    sources.append(str(node.config_path))

    doc_ex = doctrine_excerpt(root, product_id, max_chars=caps.max_doctrine_chars)
    if doc_ex:
        dpath = node.product_root / "doctrine.yaml"
        if dpath.is_file():
            sources.append(str(dpath))

    mode, strat_text = strategy_summary(root, max_chars=caps.max_strategy_chars)
    sources.append("runs/strategy/current.json")

    findings_rows: list[dict[str, Any]] = []
    fb = load_latest_findings(root, product_id)
    if fb:
        sources.append(f"runs/findings/latest/{product_id}.json")
        sorted_f = sorted(fb.findings, key=_severity_key, reverse=True)
        take = sorted_f[: caps.max_findings]
        findings_rows = [_compact_finding(f) for f in take]
        n = len(sorted_f) - len(take)
        if n > 0:
            omitted["findings"] = n

    gap_ctx = infer_stub_gap_context(root)
    gap_ids = sorted(gap_ctx.capability_gap_ids)[: caps.max_capability_gap_ids]
    if len(gap_ctx.capability_gap_ids) > len(gap_ids):
        omitted["capability_gap_ids"] = len(gap_ctx.capability_gap_ids) - len(gap_ids)
    cap_path = root / "runs" / "capabilities" / "latest.json"
    if gap_ctx.capability_gap_ids and cap_path.is_file():
        sources.append("runs/capabilities/latest.json")

    temporal_compact: dict[str, Any] = {}
    if purpose in (ContextPurpose.REFINEMENT_GROUNDED, ContextPurpose.COUNCIL_IMPLEMENTATION_GROUNDED):
        tv = compute_product_temporal_visibility(root, product_id, node)
        temporal_compact = {
            "overall": tv.get("overall"),
            "temporal_requirement": tv.get("temporal_requirement"),
            "flags": (tv.get("flags") or [])[:8],
        }
        sources.append(f"runs/signals/latest/{product_id}.json (if present)")

    if purpose in (ContextPurpose.COUNCIL_OUTSIDER_PITCH, ContextPurpose.COUNCIL_OUTSIDER_MARKET):
        findings_rows = findings_rows[:2]
        gap_ids = gap_ids[:3]
        if doc_ex:
            doc_ex = doc_ex[:1200]
        prod_summary = prod_summary[: min(len(prod_summary), 2000)]
        bundle_note = "outsider_slice: limited product context; not authoritative for repo truth"
    else:
        bundle_note = None

    ps_core: dict[str, Any] = {
            "present": True,
            "product_id": product_id,
            "product_summary": prod_summary,
            "doctrine_excerpt": doc_ex,
            "strategy_mode": mode,
            "strategy_summary": strat_text,
            "findings_recent": findings_rows,
            "capability_gap_ids": gap_ids,
            "temporal_compact": temporal_compact,
    }
    if bundle_note:
        ps_core["context_slice_note"] = bundle_note
    return (
        ps_core,
        sources,
        omitted,
    )


def _build_audit_block(
    repo_root: Path,
    product_id: str,
    caps: ContextCaps,
) -> tuple[dict[str, Any] | None, list[str], dict[str, int]]:
    """Compact audit block: Product Gap summary + optional bundle coverage / angle summary_lines only."""
    a = load_latest_audit(repo_root, product_id)
    if not a:
        return None, [], {}

    def _ids(status: AuditStatus, limit: int) -> list[str]:
        return [e.capability_id for e in a.capabilities if e.status == status][:limit]

    omitted: dict[str, int] = {}
    impl = _ids(AuditStatus.IMPLEMENTED, caps.max_audit_implemented_ids)
    miss = _ids(AuditStatus.MISSING, caps.max_audit_missing_ids)
    part = _ids(AuditStatus.PARTIAL, caps.max_audit_partial_ids)
    unk = _ids(AuditStatus.UNKNOWN, caps.max_audit_unknown_ids)

    for status, lim, lst in (
        (AuditStatus.IMPLEMENTED, caps.max_audit_implemented_ids, impl),
        (AuditStatus.MISSING, caps.max_audit_missing_ids, miss),
        (AuditStatus.PARTIAL, caps.max_audit_partial_ids, part),
        (AuditStatus.UNKNOWN, caps.max_audit_unknown_ids, unk),
    ):
        total = sum(1 for e in a.capabilities if e.status == status)
        if total > len(lst):
            omitted[f"audit_{status.value}"] = total - len(lst)

    summary = {
        "audit_generated_at_utc": a.generated_at_utc,
        "scan_depth": a.scan_depth,
        "inputs_fingerprint": a.inputs_fingerprint,
        "coverage": {
            "scanned_paths": a.scanned_paths[:32],
            "excluded_paths": a.excluded_paths[:16],
            "depth": "quick",
        },
        "already_exists": impl,
        "known_missing": miss,
        "partial_capabilities": part,
        "unknown_capabilities": unk,
        "counts_by_status": dict(a.counts_by_status),
    }
    sources: list[str] = []
    block: dict[str, Any] = {"summary": summary}

    b = load_audit_bundle(repo_root, product_id)
    if b:
        block["audit_coverage"] = audit_coverage_from_bundle(b)
        block["angles"] = compact_angle_lines_for_context(
            b,
            max_lines_per_angle=caps.max_audit_summary_lines_per_angle,
        )
        block["inputs_fingerprint_bundle"] = b.get("inputs_fingerprint_bundle", "")
        sources.append(f"runs/audit/{product_id}/bundle.json")
    sources.append(f"runs/audit/{product_id}/latest.json")

    return block, sources, omitted


def _build_artifact_draft_packet(draft: ArtifactDraft, caps: ContextCaps) -> dict[str, Any]:
    content = draft.content or ""
    preview = content[: caps.max_artifact_content_preview]
    if len(content) > len(preview):
        preview = preview + "…"
    sf = draft.structured_fields
    try:
        sj = json.dumps(to_jsonable(sf), sort_keys=True)
    except (TypeError, ValueError):
        sj = "{}"
    if len(sj) > caps.max_structured_fields_json:
        sj = sj[: caps.max_structured_fields_json] + "…"
    return {
        "draft_id": draft.draft_id,
        "session_id": draft.session_id,
        "round_number": draft.round_number,
        "artifact_type": draft.artifact_type.value,
        "title": draft.title,
        "content_preview": preview,
        "content_char_count": len(content),
        "structured_fields_json": sj,
    }


def assemble_context_bundle(
    repo_root: Path,
    purpose: ContextPurpose,
    product_id: str | None,
    *,
    draft: ArtifactDraft | None = None,
    caps: ContextCaps | None = None,
) -> dict[str, Any]:
    """
    Build a versioned bundle with ``product.system`` and optionally ``artifact.draft``.

    Always includes ``context_sources`` and omission counts when data is capped.
    """
    c = caps or ContextCaps()
    root = repo_root.resolve()
    now = datetime.now(timezone.utc).isoformat()

    ps, src_prod, omitted_ps = _build_product_system(root, product_id, purpose, c)
    all_sources = list(dict.fromkeys(src_prod))

    bundle: dict[str, Any] = {
        "packet_schema": PACKET_SCHEMA,
        "packet_version": 1,
        "assembled_at_utc": now,
        "purpose": purpose.value,
        "context_sources": all_sources,
        "product": {"system": ps},
        "caps": {
            "max_findings": c.max_findings,
            "max_capability_gap_ids": c.max_capability_gap_ids,
            "max_doctrine_chars": c.max_doctrine_chars,
            "max_strategy_chars": c.max_strategy_chars,
        },
    }
    if omitted_ps:
        bundle["omitted_counts"] = omitted_ps

    if draft is not None:
        bundle["artifact"] = {"draft": _build_artifact_draft_packet(draft, c)}
        all_sources.append(f"runs/refinement/{draft.session_id}/drafts/round_{draft.round_number}.json")

    if product_id and purpose in (
        ContextPurpose.REFINEMENT_GROUNDED,
        ContextPurpose.IDEA_GENERATION,
        ContextPurpose.COUNCIL_IMPLEMENTATION_GROUNDED,
    ):
        aud, src_aud, omitted_aud = _build_audit_block(root, product_id, c)
        if aud:
            bundle["audit"] = aud
            all_sources.extend(src_aud)
            if omitted_aud:
                bundle.setdefault("omitted_counts", {}).update(omitted_aud)

    bundle["context_sources"] = list(dict.fromkeys(all_sources))
    return bundle


def refinement_grounded_bundle(
    repo_root: Path,
    product_id: str | None,
    draft: ArtifactDraft | None,
) -> dict[str, Any]:
    """Convenience: purpose ``refinement_grounded`` with optional draft."""
    return assemble_context_bundle(
        repo_root,
        ContextPurpose.REFINEMENT_GROUNDED,
        product_id,
        draft=draft,
    )
