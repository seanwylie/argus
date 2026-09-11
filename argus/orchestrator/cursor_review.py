"""Structured Cursor interpretation for orchestration state (additive; not deterministic truth)."""

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

ORCHESTRATION_CURSOR_REVIEW_SCHEMA = "argus.orchestration_cursor_review.v1"
ORCHESTRATION_REVIEW_BUNDLE_SCHEMA = "argus.orchestration_review_bundle.v1"
PROVENANCE_ORCHESTRATION_CURSOR_REVIEW = "cursor_orchestration_review"

ORCHESTRATION_CURSOR_REVIEW_REQUIRED_KEYS: tuple[str, ...] = (
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


def validate_orchestration_cursor_review_v1(
    raw: dict[str, Any],
    *,
    expected_product_id: str | None = None,
) -> dict[str, Any]:
    missing = [k for k in ORCHESTRATION_CURSOR_REVIEW_REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(f"orchestration_cursor_review missing required keys: {missing}")

    sch = str(raw.get("schema", ""))
    if sch != ORCHESTRATION_CURSOR_REVIEW_SCHEMA:
        raise ValueError(
            f"orchestration_cursor_review.schema must be {ORCHESTRATION_CURSOR_REVIEW_SCHEMA!r}, got {sch!r}",
        )

    pid = str(raw.get("product_id", "")).strip()
    if not pid:
        raise ValueError("orchestration_cursor_review.product_id must be a non-empty string")
    if expected_product_id is not None and pid != expected_product_id:
        raise ValueError(
            f"orchestration_cursor_review.product_id mismatch: payload has {pid!r} "
            f"but ingest expects {expected_product_id!r}",
        )

    prov = str(raw.get("provenance", "")).strip()
    if prov != PROVENANCE_ORCHESTRATION_CURSOR_REVIEW:
        raise ValueError(
            f"orchestration_cursor_review.provenance must be exactly "
            f"{PROVENANCE_ORCHESTRATION_CURSOR_REVIEW!r}, got {prov!r}",
        )

    sl = raw.get("summary_lines")
    if not isinstance(sl, list) or len(sl) < 1:
        raise ValueError("orchestration_cursor_review.summary_lines must be a non-empty array of strings")
    lines = [str(x).strip() for x in sl[:MAX_REVIEW_LINES] if str(x).strip()]
    if not lines:
        raise ValueError("orchestration_cursor_review.summary_lines must contain at least one non-empty line")

    try:
        conf = float(raw["confidence"])
    except (TypeError, ValueError) as e:
        raise ValueError("orchestration_cursor_review.confidence must be a number between 0 and 1") from e
    if not 0.0 <= conf <= 1.0:
        raise ValueError("orchestration_cursor_review.confidence must be between 0 and 1")

    refs_raw = raw.get("repo_evidence_refs")
    if not isinstance(refs_raw, list):
        raise ValueError("orchestration_cursor_review.repo_evidence_refs must be an array of strings")
    refs = [str(x).strip() for x in refs_raw[:MAX_REVIEW_EVIDENCE_REFS] if str(x).strip()]

    lim_raw = raw.get("limitations")
    if isinstance(lim_raw, str):
        limitations = [lim_raw.strip()[:2000]] if lim_raw.strip() else []
    elif isinstance(lim_raw, list):
        limitations = [str(x).strip() for x in lim_raw[:24] if str(x).strip()]
    else:
        raise ValueError("orchestration_cursor_review.limitations must be a string or array of strings")

    findings = validate_cursor_review_findings(raw.get("findings"), "findings")
    risks = validate_cursor_review_risks(raw.get("risks"))
    enhancements = validate_cursor_review_findings(raw.get("enhancements"), "enhancements")

    notes: list[str] = []
    if isinstance(raw.get("notes"), list):
        notes = [str(x) for x in raw["notes"][:16]]

    out: dict[str, Any] = {
        "schema": ORCHESTRATION_CURSOR_REVIEW_SCHEMA,
        "product_id": pid,
        "summary_lines": lines,
        "findings": findings,
        "risks": risks,
        "enhancements": enhancements,
        "confidence": round(conf, 4),
        "repo_evidence_refs": refs,
        "limitations": limitations,
        "provenance": PROVENANCE_ORCHESTRATION_CURSOR_REVIEW,
    }
    if notes:
        out["notes"] = notes
    return out


def fingerprint_orchestration_state(state: dict[str, Any]) -> str:
    """
    Stable fingerprint over orchestration state content (for review provenance).

    Excludes ``evaluated_at_utc`` so re-evaluation with unchanged artifacts matches the prior fingerprint.
    """
    pid = str(state.get("product_id", ""))
    os_ = str(state.get("orchestration_status", ""))
    na = str(state.get("next_action", ""))
    parts: list[str] = [pid, os_, na]
    elig = state.get("eligible_actions") or []
    if isinstance(elig, list):
        for row in elig:
            if isinstance(row, dict):
                parts.append(str(row.get("action_id", "")))
                parts.append(str(row.get("reason", ""))[:200])
    bl = state.get("blockers") or []
    if isinstance(bl, list):
        for b in bl:
            if isinstance(b, dict):
                parts.append(str(b.get("kind", "")))
                parts.append(str(b.get("session_id", "")))
    blob = "\n".join(parts).encode("utf-8")
    digest = hashlib.sha256(blob).hexdigest()[:32]
    return f"sha256:{digest}"


def orchestration_cursor_review_contract_prompt_block() -> str:
    example = {
        "schema": ORCHESTRATION_CURSOR_REVIEW_SCHEMA,
        "product_id": "your_product_id",
        "summary_lines": ["orchestration: eligible actions look order-sensitive vs repo layout"],
        "findings": [
            {
                "title": "Example finding",
                "detail": "",
                "severity": "info",
                "evidence_refs": ["products/your_product_id/"],
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
        "limitations": ["interpretation only; does not replace deterministic orchestration state"],
        "provenance": PROVENANCE_ORCHESTRATION_CURSOR_REVIEW,
    }
    return (
        "Return **one JSON object** (no markdown fences). Root schema must be "
        f'"{ORCHESTRATION_CURSOR_REVIEW_SCHEMA}".\n'
        "This object is **only** the Cursor interpretation layer; Argus merges it into "
        "`runs/orchestration/review/<product_id>.json` under `orchestration_review`. "
        "Deterministic truth remains `runs/orchestration/latest/<product_id>.json`.\n"
        "Required keys: "
        + ", ".join(ORCHESTRATION_CURSOR_REVIEW_REQUIRED_KEYS)
        + ".\n"
        "Focus: weak transition logic, missing prerequisites, fragile progression assumptions, "
        "likely blind spots for next steps — grounded in repository paths.\n"
        "Template (fill all fields):\n"
        + json.dumps(example, indent=2)
    )
