"""Structured Cursor interpretation layer for collected signals (optional; not part of deterministic collection).

Contract: ``argus.signal_cursor_review.v1`` — validated on ingest. Does not alter
``runs/signals/latest/*.json`` deterministic bundles.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from argus.core.cursor_review_common import (
    MAX_REVIEW_EVIDENCE_REFS,
    MAX_REVIEW_LINES,
    validate_cursor_review_findings,
    validate_cursor_review_risks,
)
from argus.core.serialize import dumps_json

SIGNAL_CURSOR_REVIEW_SCHEMA = "argus.signal_cursor_review.v1"
SIGNAL_REVIEW_BUNDLE_SCHEMA = "argus.signal_review_bundle.v1"
PROVENANCE_SIGNAL_CURSOR_REVIEW = "cursor_signal_review"

SIGNAL_CURSOR_REVIEW_REQUIRED_KEYS: tuple[str, ...] = (
    "schema",
    "product_id",
    "summary_lines",
    "findings",
    "risks",
    "enhancements",
    "confidence",
    "repo_evidence_refs",
    "limitations",
    "provenance",
)


def validate_signal_cursor_review_v1(
    raw: dict[str, Any],
    *,
    expected_product_id: str | None = None,
) -> dict[str, Any]:
    """
    Validate and normalize ``argus.signal_cursor_review.v1``.

    Raises ``ValueError`` on contract violation.
    """
    missing = [k for k in SIGNAL_CURSOR_REVIEW_REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(f"signal_cursor_review missing required keys: {missing}")

    sch = str(raw.get("schema", ""))
    if sch != SIGNAL_CURSOR_REVIEW_SCHEMA:
        raise ValueError(
            f"signal_cursor_review.schema must be {SIGNAL_CURSOR_REVIEW_SCHEMA!r}, got {sch!r}",
        )

    pid = str(raw.get("product_id", "")).strip()
    if not pid:
        raise ValueError("signal_cursor_review.product_id must be a non-empty string")
    if expected_product_id is not None and pid != expected_product_id:
        raise ValueError(
            f"signal_cursor_review.product_id mismatch: payload has {pid!r} "
            f"but ingest expects {expected_product_id!r}",
        )

    prov = str(raw.get("provenance", "")).strip()
    if prov != PROVENANCE_SIGNAL_CURSOR_REVIEW:
        raise ValueError(
            f"signal_cursor_review.provenance must be exactly {PROVENANCE_SIGNAL_CURSOR_REVIEW!r}, "
            f"got {prov!r}",
        )

    sl = raw.get("summary_lines")
    if not isinstance(sl, list) or len(sl) < 1:
        raise ValueError("signal_cursor_review.summary_lines must be a non-empty array of strings")
    lines = [str(x).strip() for x in sl[:MAX_REVIEW_LINES] if str(x).strip()]
    if not lines:
        raise ValueError("signal_cursor_review.summary_lines must contain at least one non-empty line")

    try:
        conf = float(raw["confidence"])
    except (TypeError, ValueError) as e:
        raise ValueError("signal_cursor_review.confidence must be a number between 0 and 1") from e
    if not 0.0 <= conf <= 1.0:
        raise ValueError("signal_cursor_review.confidence must be between 0 and 1")

    refs_raw = raw.get("repo_evidence_refs")
    if not isinstance(refs_raw, list):
        raise ValueError("signal_cursor_review.repo_evidence_refs must be an array of strings")
    refs = [str(x).strip() for x in refs_raw[:MAX_REVIEW_EVIDENCE_REFS] if str(x).strip()]

    lim_raw = raw.get("limitations")
    if isinstance(lim_raw, str):
        limitations = [lim_raw.strip()[:2000]] if lim_raw.strip() else []
    elif isinstance(lim_raw, list):
        limitations = [str(x).strip() for x in lim_raw[:24] if str(x).strip()]
    else:
        raise ValueError("signal_cursor_review.limitations must be a string or array of strings")

    findings = validate_cursor_review_findings(raw.get("findings"), "findings")
    risks = validate_cursor_review_risks(raw.get("risks"))
    enhancements = validate_cursor_review_findings(raw.get("enhancements"), "enhancements")

    notes: list[str] = []
    if isinstance(raw.get("notes"), list):
        notes = [str(x) for x in raw["notes"][:16]]

    out: dict[str, Any] = {
        "schema": SIGNAL_CURSOR_REVIEW_SCHEMA,
        "product_id": pid,
        "summary_lines": lines,
        "findings": findings,
        "risks": risks,
        "enhancements": enhancements,
        "confidence": round(conf, 4),
        "repo_evidence_refs": refs,
        "limitations": limitations,
        "provenance": PROVENANCE_SIGNAL_CURSOR_REVIEW,
    }
    if notes:
        out["notes"] = notes
    return out


def fingerprint_deterministic_signal_bundle(data: dict[str, Any]) -> str:
    """
    Stable fingerprint over the deterministic collection JSON (for provenance).

    Uses product_id, collected_at_utc, record ids and types only (no payload bodies).
    """
    pid = str(data.get("product_id", ""))
    cat = str(data.get("collected_at_utc", ""))
    recs = data.get("records") or []
    parts: list[str] = [pid, cat]
    if isinstance(recs, list):
        parts.append(str(len(recs)))
        for item in recs:
            if isinstance(item, dict):
                parts.append(str(item.get("id", "")))
                parts.append(str(item.get("signal_type", "")))
                parts.append(str(item.get("source", "")))
    blob = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:32]
    return f"sha256:{digest}"


def signal_cursor_review_contract_prompt_block() -> str:
    """Exact contract text embedded in Cursor prompts (single root object)."""
    example = {
        "schema": SIGNAL_CURSOR_REVIEW_SCHEMA,
        "product_id": "your_product_id",
        "summary_lines": ["signals: metrics coverage looks thin for declared KPIs"],
        "findings": [
            {
                "title": "Example finding",
                "detail": "",
                "severity": "info",
                "evidence_refs": ["products/your_product_id/metrics/"],
            }
        ],
        "risks": [{"statement": "Example risk", "evidence_refs": []}],
        "enhancements": [
            {
                "title": "Example enhancement",
                "detail": "",
                "severity": "info",
                "evidence_refs": [],
            }
        ],
        "confidence": 0.75,
        "repo_evidence_refs": ["relative/path/to/file"],
        "limitations": ["interpretation only; does not replace deterministic signal collection"],
        "provenance": PROVENANCE_SIGNAL_CURSOR_REVIEW,
    }
    return (
        "Return **one JSON object** (no markdown fences). Root schema must be "
        f'"{SIGNAL_CURSOR_REVIEW_SCHEMA}".\n'
        "This object is **only** the Cursor interpretation layer; Argus merges it into "
        "`runs/signals/review/<product_id>.json` under `signal_review`. "
        "Do not restate raw signal payloads as facts without citing repo paths.\n"
        "Required keys: "
        + ", ".join(SIGNAL_CURSOR_REVIEW_REQUIRED_KEYS)
        + ".\n"
        "Focus: signal quality, missing instrumentation, weak definitions, collection blind spots — "
        "grounded in repository files.\n"
        "Template (fill all fields):\n"
        + json.dumps(example, indent=2)
    )


def review_fingerprint(review: dict[str, Any]) -> str:
    """Stable hash of normalized review for history dedup."""
    normalized = validate_signal_cursor_review_v1(dict(review))
    return hashlib.sha256(dumps_json(normalized).encode("utf-8")).hexdigest()[:24]
