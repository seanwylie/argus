"""Light audit-aware nudges to idea scores (deterministic, explainable)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from argus.audit.cache import load_latest_audit
from argus.audit.models import AuditCapabilityEntry, AuditStatus
from argus.idea_generation.models import Idea


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in re.split(r"[^a-z0-9]+", text) if len(t) > 2}


def _entry_tokens(entry: AuditCapabilityEntry) -> set[str]:
    parts = set(_tokens(entry.capability_id))
    parts |= _tokens(entry.description)
    return parts


def apply_audit_to_ideas(
    ideas: list[Idea],
    repo_root: Path,
    product_id: str | None,
) -> dict[str, Any]:
    """
    Nudge scores when ideas overlap audited **implemented** capabilities (down-rank duplicates)
    or **missing** gaps (small boost). **Unknown** statuses are not penalized.
    """
    out: dict[str, Any] = {"applied": False, "product_id": product_id}
    if not product_id:
        out["reason"] = "no_product_scope"
        return out

    audit = load_latest_audit(repo_root, product_id)
    if audit is None:
        out["reason"] = "no_audit_artifact"
        out["hint"] = f"run `argus audit run --product-id {product_id}`"
        return out

    implemented: list[AuditCapabilityEntry] = [e for e in audit.capabilities if e.status == AuditStatus.IMPLEMENTED]
    missing: list[AuditCapabilityEntry] = [e for e in audit.capabilities if e.status == AuditStatus.MISSING]
    unknown: list[AuditCapabilityEntry] = [e for e in audit.capabilities if e.status == AuditStatus.UNKNOWN]

    n_impl = 0
    n_miss = 0

    for idea in ideas:
        blob = f"{idea.title} {idea.description} {idea.rationale}"
        it = _tokens(blob)
        best_impl_overlap = 0
        best_miss_overlap = 0
        best_impl_id = ""
        best_miss_id = ""
        for e in implemented:
            ov = len(it & _entry_tokens(e))
            if ov > best_impl_overlap:
                best_impl_overlap = ov
                best_impl_id = e.capability_id
        for e in missing:
            ov = len(it & _entry_tokens(e))
            if ov > best_miss_overlap:
                best_miss_overlap = ov
                best_miss_id = e.capability_id

        adj: dict[str, Any] = {"audit_fingerprint": audit.inputs_fingerprint[:12]}
        changed = False

        # Conservative: need at least 2 overlapping tokens with an entry
        if best_impl_overlap >= 2:
            factor = 0.92
            idea.expected_value_score = max(0.0, min(1.0, idea.expected_value_score * factor))
            idea.novelty_score = max(0.0, min(1.0, idea.novelty_score * 0.96))
            adj["nudge"] = "implemented_overlap"
            adj["detail"] = f"overlap with implemented capability {best_impl_id!r} (tokens={best_impl_overlap})"
            adj["evidence_factor"] = factor
            changed = True
            n_impl += 1
        elif best_miss_overlap >= 2:
            factor = 1.04
            idea.expected_value_score = max(0.0, min(1.0, idea.expected_value_score * factor))
            adj["nudge"] = "missing_gap_support"
            adj["detail"] = f"overlap with missing capability {best_miss_id!r} (tokens={best_miss_overlap})"
            adj["evidence_factor"] = factor
            changed = True
            n_miss += 1
        else:
            uo = max((len(it & _entry_tokens(e)) for e in unknown), default=0)
            if uo >= 3:
                adj["note"] = f"possible overlap with unknown audit rows (tokens={uo}); scores unchanged"

        if changed or len(adj) > 1:
            idea.audit_adjustment = adj

    out["applied"] = True
    out["audit_generated_at_utc"] = audit.generated_at_utc
    out["nudges_implemented_overlap"] = n_impl
    out["nudges_missing_gap"] = n_miss
    out["ideas_count"] = len(ideas)
    return out
