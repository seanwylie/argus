"""
Shared structural validation for Cursor-style review sections.

Used by ``argus.signal_cursor_review.v1``, ``argus.orchestration_cursor_review.v1``, and
``argus.audit_cursor_scan.v1`` for the repeated **findings**, **risks**, and **enhancements**
nested shapes (title/detail/severity/evidence_refs; risk statements; list caps).

Each contract module keeps its own schema id, required root keys, provenance strings, and
error prefixes on the top-level validator; this module only validates the shared nested
list/object structure so signal and orchestration do not depend on each other's packages.
"""

from __future__ import annotations

from typing import Any

# Caps aligned across contracts that embed these sections.
MAX_REVIEW_LINES = 24
MAX_REVIEW_FINDINGS = 48
MAX_REVIEW_RISKS = 48
MAX_REVIEW_ENHANCEMENTS = 48  # legacy audit paths; findings/enhancements lists use MAX_REVIEW_FINDINGS in validators
MAX_REVIEW_EVIDENCE_REFS = 64
SEVERITIES = frozenset(("info", "warn", "fail"))


def validate_cursor_review_findings(raw: Any, label: str) -> list[dict[str, Any]]:
    """Validate a findings- or enhancements-shaped array (``label`` is ``findings`` or ``enhancements``)."""
    if not isinstance(raw, list):
        raise ValueError(f"{label} must be an array")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:MAX_REVIEW_FINDINGS]):
        if not isinstance(item, dict):
            raise ValueError(f"{label}[{i}] must be an object with title and evidence_refs")
        title = str(item.get("title", "")).strip()
        if not title:
            raise ValueError(f"{label}[{i}].title is required and must be non-empty")
        sev = str(item.get("severity", "info")).lower().strip()
        if sev not in SEVERITIES:
            raise ValueError(f"{label}[{i}].severity must be one of: info, warn, fail")
        refs_raw = item.get("evidence_refs")
        if not isinstance(refs_raw, list):
            raise ValueError(f"{label}[{i}].evidence_refs must be an array of strings")
        refs = [str(x).strip() for x in refs_raw[:MAX_REVIEW_EVIDENCE_REFS] if str(x).strip()]
        detail = item.get("detail")
        detail_s = str(detail).strip() if detail is not None else ""
        out.append(
            {
                "title": title,
                "detail": detail_s,
                "severity": sev,
                "evidence_refs": refs,
            }
        )
    return out


def validate_cursor_review_risks(raw: Any) -> list[dict[str, Any]]:
    """Validate the shared ``risks`` array (string or object entries)."""
    if not isinstance(raw, list):
        raise ValueError("risks must be an array")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(raw[:MAX_REVIEW_RISKS]):
        if isinstance(item, str):
            st = item.strip()
            if not st:
                raise ValueError(f"risks[{i}] must be a non-empty string or an object")
            out.append({"statement": st, "evidence_refs": []})
            continue
        if not isinstance(item, dict):
            raise ValueError(f"risks[{i}] must be a string or object with statement and evidence_refs")
        st = str(item.get("statement", "")).strip()
        if not st:
            raise ValueError(f"risks[{i}].statement is required and must be non-empty")
        refs_raw = item.get("evidence_refs")
        if refs_raw is not None and not isinstance(refs_raw, list):
            raise ValueError(f"risks[{i}].evidence_refs must be an array of strings")
        refs = (
            [str(x).strip() for x in refs_raw[:MAX_REVIEW_EVIDENCE_REFS] if str(x).strip()]
            if isinstance(refs_raw, list)
            else []
        )
        out.append({"statement": st, "evidence_refs": refs})
    return out
