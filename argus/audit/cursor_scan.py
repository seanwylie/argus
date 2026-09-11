"""Structured Cursor codebase scan payloads (optional layer on deterministic audit).

Contract: ``argus.audit_cursor_scan.v1`` — validated on ingest; see docs/model-contracts.md
and ``docs/audit-cursor-scan-contract.md``.
"""

from __future__ import annotations

import hashlib
from typing import Any

from argus.audit.agent_lens import PROVENANCE_AGENT
from argus.core.cursor_review_common import (
    MAX_REVIEW_ENHANCEMENTS,
    MAX_REVIEW_EVIDENCE_REFS,
    MAX_REVIEW_FINDINGS,
    MAX_REVIEW_LINES,
    SEVERITIES,
    validate_cursor_review_findings,
    validate_cursor_review_risks,
)
from argus.core.serialize import dumps_json

CURSOR_SCAN_SCHEMA = "argus.audit_cursor_scan.v1"
CURSOR_SCAN_BATCH_SCHEMA = "argus.audit_cursor_scan_batch.v1"
PROVENANCE_CURSOR_SCAN = "cursor_codebase_scan"

# Ingest must reject payloads missing any of these keys (after normalization).
CURSOR_SCAN_REQUIRED_KEYS: tuple[str, ...] = (
    "schema",
    "angle_id",
    "summary_lines",
    "findings",
    "risks",
    "enhancements",
    "confidence",
    "repo_evidence_refs",
    "limitations",
    "provenance",
)


def validate_cursor_scan_v1(
    raw: dict[str, Any],
    *,
    expected_angle_id: str | None = None,
) -> dict[str, Any]:
    """
    Validate and normalize ``argus.audit_cursor_scan.v1``.

    Raises ``ValueError`` with a specific message on any contract violation.
    """
    missing = [k for k in CURSOR_SCAN_REQUIRED_KEYS if k not in raw]
    if missing:
        raise ValueError(f"cursor_scan missing required keys: {missing}")

    sch = str(raw.get("schema", ""))
    if sch != CURSOR_SCAN_SCHEMA:
        raise ValueError(f"cursor_scan.schema must be {CURSOR_SCAN_SCHEMA!r}, got {sch!r}")

    aid = str(raw.get("angle_id", "")).strip()
    if not aid:
        raise ValueError("cursor_scan.angle_id must be a non-empty string")
    if expected_angle_id is not None and aid != expected_angle_id:
        raise ValueError(
            f"cursor_scan.angle_id mismatch: payload has {aid!r} but ingest angle is {expected_angle_id!r}",
        )

    prov = str(raw.get("provenance", "")).strip()
    if prov != PROVENANCE_CURSOR_SCAN:
        raise ValueError(f"cursor_scan.provenance must be exactly {PROVENANCE_CURSOR_SCAN!r}, got {prov!r}")

    sl = raw.get("summary_lines")
    if not isinstance(sl, list) or len(sl) < 1:
        raise ValueError("cursor_scan.summary_lines must be a non-empty array of strings")
    lines = [str(x).strip() for x in sl[:MAX_REVIEW_LINES] if str(x).strip()]
    if not lines:
        raise ValueError("cursor_scan.summary_lines must contain at least one non-empty line")

    try:
        conf = float(raw["confidence"])
    except (TypeError, ValueError) as e:
        raise ValueError("cursor_scan.confidence must be a number between 0 and 1") from e
    if not 0.0 <= conf <= 1.0:
        raise ValueError("cursor_scan.confidence must be between 0 and 1")

    refs_raw = raw.get("repo_evidence_refs")
    if not isinstance(refs_raw, list):
        raise ValueError("cursor_scan.repo_evidence_refs must be an array of strings")
    refs = [str(x).strip() for x in refs_raw[:MAX_REVIEW_EVIDENCE_REFS] if str(x).strip()]

    lim_raw = raw.get("limitations")
    if isinstance(lim_raw, str):
        limitations = [lim_raw.strip()[:2000]] if lim_raw.strip() else []
    elif isinstance(lim_raw, list):
        limitations = [str(x).strip() for x in lim_raw[:24] if str(x).strip()]
    else:
        raise ValueError("cursor_scan.limitations must be a string or array of strings")

    findings = validate_cursor_review_findings(raw.get("findings"), "findings")
    risks = validate_cursor_review_risks(raw.get("risks"))
    enhancements = validate_cursor_review_findings(raw.get("enhancements"), "enhancements")

    notes: list[str] = []
    if isinstance(raw.get("notes"), list):
        notes = [str(x) for x in raw["notes"][:16]]

    out: dict[str, Any] = {
        "schema": CURSOR_SCAN_SCHEMA,
        "angle_id": aid,
        "summary_lines": lines,
        "findings": findings,
        "risks": risks,
        "enhancements": enhancements,
        "confidence": round(conf, 4),
        "repo_evidence_refs": refs,
        "limitations": limitations,
        "provenance": PROVENANCE_CURSOR_SCAN,
    }
    if notes:
        out["notes"] = notes
    return out


def legacy_agent_payload_to_cursor_scan(raw: dict[str, Any], *, angle_id: str) -> dict[str, Any]:
    """Map pre-contract agent ingest shape into ``argus.audit_cursor_scan.v1``."""
    sl = raw.get("summary_lines")
    if not isinstance(sl, list) or not sl:
        raise ValueError("legacy ingest: summary_lines required")
    findings_legacy = raw.get("findings")
    findings: list[dict[str, Any]] = []
    if isinstance(findings_legacy, list) and findings_legacy:
        for it in findings_legacy[:MAX_REVIEW_FINDINGS]:
            if isinstance(it, dict):
                sev = str(it.get("severity", "info")).lower()
                if sev not in SEVERITIES:
                    sev = "info"
                er = it.get("evidence_refs")
                refs = [str(x).strip() for x in er] if isinstance(er, list) else []
                findings.append(
                    {
                        "title": str(it.get("title", "finding"))[:500],
                        "detail": str(it.get("detail", "")),
                        "severity": sev,
                        "evidence_refs": refs,
                    }
                )
            else:
                findings.append(
                    {
                        "title": str(it)[:500],
                        "detail": "",
                        "severity": "info",
                        "evidence_refs": [],
                    }
                )

    enhancements: list[dict[str, Any]] = []
    pri = raw.get("priorities")
    if isinstance(pri, list):
        for i, it in enumerate(pri[:MAX_REVIEW_ENHANCEMENTS]):
            if isinstance(it, dict):
                er = it.get("evidence_refs")
                refs = [str(x).strip() for x in er] if isinstance(er, list) else []
                enhancements.append(
                    {
                        "title": str(it.get("title", f"priority_{i}"))[:500],
                        "detail": str(it.get("detail", "")),
                        "severity": "info",
                        "evidence_refs": refs,
                    }
                )
            else:
                enhancements.append(
                    {
                        "title": str(it)[:500],
                        "detail": "",
                        "severity": "info",
                        "evidence_refs": [],
                    }
                )

    try:
        conf = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))

    return validate_cursor_scan_v1(
        {
            "schema": CURSOR_SCAN_SCHEMA,
            "angle_id": angle_id,
            "summary_lines": [str(x) for x in sl[:MAX_REVIEW_LINES]],
            "findings": findings,
            "risks": [],
            "enhancements": enhancements,
            "confidence": conf,
            "repo_evidence_refs": [],
            "limitations": ["legacy agent payload — prefer full cursor_scan contract on next export"],
            "provenance": PROVENANCE_CURSOR_SCAN,
        },
        expected_angle_id=angle_id,
    )


def normalize_stored_cursor_scan(prev_cs: dict[str, Any]) -> dict[str, Any]:
    """Map older stored shapes (e.g. ``opportunities``) for fingerprint / display."""
    d = dict(prev_cs)
    if "opportunities" in d and "enhancements" not in d:
        opp = d.pop("opportunities")
        if not isinstance(opp, list) or not opp:
            d["enhancements"] = []
        elif isinstance(opp[0], str):
            d["enhancements"] = [
                {"title": str(x), "detail": "", "severity": "info", "evidence_refs": []} for x in opp
            ]
        else:
            d["enhancements"] = validate_cursor_review_findings(opp, "enhancements")
    return d


def cursor_scan_fingerprint(scan: dict[str, Any]) -> str:
    """Stable hash for bundle fingerprinting (excludes ingested_at_utc)."""
    scan = normalize_stored_cursor_scan(dict(scan))
    keys = (
        "schema",
        "angle_id",
        "summary_lines",
        "findings",
        "risks",
        "enhancements",
        "confidence",
        "repo_evidence_refs",
        "limitations",
        "notes",
        "provenance",
    )
    stable = {k: scan.get(k) for k in keys}
    raw = dumps_json(stable)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def merge_summary_lines(
    deterministic_lines: list[str] | None,
    cursor_scan: dict[str, Any] | None,
    *,
    max_lines: int = 16,
) -> list[str]:
    """Combined view: when only static, preserve original lines; with Cursor, prefix sources."""
    out: list[str] = []
    det = [str(x).strip() for x in (deterministic_lines or []) if str(x).strip()]
    if not cursor_scan:
        return det[:max_lines]
    for s in det:
        out.append(f"[static] {s}" if not s.startswith("[static]") else s)
        if len(out) >= max_lines:
            return out[:max_lines]
    if isinstance(cursor_scan.get("summary_lines"), list):
        for x in cursor_scan["summary_lines"]:
            s = str(x).strip()
            if s:
                out.append(f"[cursor] {s}" if not s.startswith("[cursor]") else s)
            if len(out) >= max_lines:
                break
    return out[:max_lines]


def merge_deterministic_with_prior_cursor(
    deterministic: dict[str, Any],
    prior_angle: dict[str, Any] | None,
    *,
    angle_id: str,
) -> dict[str, Any]:
    """
    Fresh deterministic payload + optional preserved ``cursor_scan`` from prior bundle.

    Migrates legacy agent-only angles (``provenance`` == agent_lens, no ``cursor_scan``)
    into a ``cursor_scan`` envelope so deterministic can replace the outer payload.

    Stores ``deterministic_summary_lines`` (raw runner lines) for later re-merge after ingest.
    """
    out = dict(deterministic)
    raw_det = list(out.get("summary_lines") or [])
    raw_det = [str(x) for x in raw_det] if isinstance(raw_det, list) else []
    out["deterministic_summary_lines"] = raw_det

    cs: dict[str, Any] | None = None
    if isinstance(prior_angle, dict):
        prev_cs = prior_angle.get("cursor_scan")
        if isinstance(prev_cs, dict) and str(prev_cs.get("schema")) == CURSOR_SCAN_SCHEMA:
            cs = normalize_stored_cursor_scan(prev_cs)
        elif prior_angle.get("provenance") == PROVENANCE_AGENT and "cursor_scan" not in prior_angle:
            try:
                cs = legacy_agent_payload_to_cursor_scan(prior_angle, angle_id=angle_id)
            except ValueError:
                cs = None

    out["summary_lines"] = merge_summary_lines(raw_det, cs, max_lines=16)
    if cs:
        out["cursor_scan"] = cs
    out["sources"] = {
        "deterministic": True,
        "cursor_scan": bool(cs),
    }
    out.pop("provenance", None)
    out.pop("ingested_at_utc", None)
    return out


def reapply_cursor_merge(angle: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``summary_lines`` from stored deterministic lines + ``cursor_scan`` (e.g. after ingest)."""
    out = dict(angle)
    det = out.get("deterministic_summary_lines")
    if not isinstance(det, list) or not det:
        det = _strip_source_prefixes(list(out.get("summary_lines") or []))
    cs = out.get("cursor_scan") if isinstance(out.get("cursor_scan"), dict) else None
    if cs:
        cs = normalize_stored_cursor_scan(cs)
        out["cursor_scan"] = cs
    out["summary_lines"] = merge_summary_lines([str(x) for x in det], cs, max_lines=16)
    out["sources"] = {
        "deterministic": True,
        "cursor_scan": bool(cs and str(cs.get("schema")) == CURSOR_SCAN_SCHEMA),
    }
    return out


def _strip_source_prefixes(lines: list[str]) -> list[str]:
    out: list[str] = []
    for x in lines:
        s = str(x).strip()
        if s.startswith("[static] "):
            s = s[len("[static] ") :].strip()
        elif s.startswith("[cursor] "):
            continue
        if s:
            out.append(s)
    return out


def angle_inputs_fingerprint(deterministic_fp: str, cursor_scan: dict[str, Any] | None) -> str:
    """Per-angle fingerprint: deterministic hash plus optional cursor scan content."""
    if not cursor_scan:
        return deterministic_fp
    return f"{deterministic_fp}:{cursor_scan_fingerprint(cursor_scan)}"


def cursor_scan_contract_prompt_block() -> str:
    """Exact contract text embedded in Cursor prompts (single angle object)."""
    return (
        "Each value under `angles` MUST be a JSON object with these keys (all required):\n"
        f'  "schema": "{CURSOR_SCAN_SCHEMA}"\n'
        '  "angle_id": "<same as the key in angles>"\n'
        '  "summary_lines": ["non-empty strings, repo-grounded"]\n'
        '  "findings": [ { "title": str, "detail": str, "severity": "info"|"warn"|"fail", "evidence_refs": [str] } ]\n'
        '  "risks": [ { "statement": str, "evidence_refs": [str] } ]  OR legacy: [ string, ... ] normalized on ingest\n'
        '  "enhancements": [ { "title", "detail", "severity", "evidence_refs" } ]  (same shape as findings)\n'
        "  \"confidence\": number in [0, 1]\n"
        '  "repo_evidence_refs": [ "repo-relative paths you relied on" ]\n'
        '  "limitations": [ "what you could not verify" ]  (array of strings, may be empty)\n'
        f'  "provenance": "{PROVENANCE_CURSOR_SCAN}"\n'
        "Optional: \"notes\": [ string ].\n"
        "Do not merge deterministic Argus output into these objects; this is the Cursor interpretation only.\n"
    )


def cursor_scan_single_object_contract_prompt_block() -> str:
    """Contract for a root JSON object (single-angle file; ``argus audit ingest-agent --angle <id>``)."""
    return (
        "The root JSON object MUST have these keys (all required):\n"
        f'  "schema": "{CURSOR_SCAN_SCHEMA}"\n'
        '  "angle_id": "<must match the audit angle for this prompt>"\n'
        '  "summary_lines": ["non-empty strings, repo-grounded"]\n'
        '  "findings": [ { "title": str, "detail": str, "severity": "info"|"warn"|"fail", "evidence_refs": [str] } ]\n'
        '  "risks": [ { "statement": str, "evidence_refs": [str] } ]  OR legacy: [ string, ... ] normalized on ingest\n'
        '  "enhancements": [ { "title", "detail", "severity", "evidence_refs" } ]  (same shape as findings)\n'
        "  \"confidence\": number in [0, 1]\n"
        '  "repo_evidence_refs": [ "repo-relative paths you relied on" ]\n'
        '  "limitations": [ "what you could not verify" ]  (array of strings, may be empty)\n'
        f'  "provenance": "{PROVENANCE_CURSOR_SCAN}"\n'
        "Optional: \"notes\": [ string ].\n"
        "Do not paste deterministic runner text wholesale; synthesize from repo inspection.\n"
    )
