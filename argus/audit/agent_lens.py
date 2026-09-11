"""Lens descriptions for optional Cursor / external agent audits (non-deterministic layer)."""

from __future__ import annotations

import hashlib
from typing import Any

from argus.audit.bundle import ANGLE_IDS
from argus.core.serialize import dumps_json

# `PROVENANCE_AGENT` is legacy full-angle ingest; prefer `cursor_scan` + `sources` on each angle.
PROVENANCE_AGENT = "agent_lens"
PROVENANCE_DETERMINISTIC_STUB = "deterministic_stub"

# All bundle angles accept an optional Cursor codebase scan (`argus.audit_cursor_scan.v1`).
CURSOR_SCAN_ANGLE_IDS: tuple[str, ...] = ANGLE_IDS
AGENT_LENS_ANGLE_IDS = CURSOR_SCAN_ANGLE_IDS  # backward compat


def agent_content_fingerprint(payload: dict[str, Any]) -> str:
    """Stable hash for ``inputs_fingerprint_by_angle`` (excludes volatile keys like ``ingested_at_utc``)."""
    keys = ("schema", "angle_status", "summary_lines", "priorities", "findings", "notes", "provenance")
    stable = {k: payload.get(k) for k in keys}
    raw = dumps_json(stable)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


# Human-facing: what to look for per angle (Cursor / external agent prompts).
LENS_BY_ANGLE: dict[str, str] = {
    "product_gap": (
        "Declared vs on-disk coverage: product.yaml and doctrine vs scripts, metrics paths, app/src scope; "
        "capability rows (implemented / partial / missing / unknown). Cite paths; do not invent files."
    ),
    "cost": (
        "Economics alignment for this product: declared monthly cost in yaml vs registry resources under "
        "config/economics and runs/economics; orphan or mapping gaps. Numbers from repo artifacts only."
    ),
    "quality": (
        "Test and CI signals: pyproject.toml, package.json, workflow files under .github/workflows; "
        "presence of lint/test tooling. Static inventory only—not a judgment of test quality."
    ),
    "security": (
        "Security posture of this product as implemented in-repo: secrets handling, auth boundaries, "
        "dependency/supply-chain surface, obvious injection or path issues, exposed credentials in files. "
        "Be conservative; cite file paths. Do not claim penetration testing—surface risks only."
    ),
    "compliance": (
        "Policy and audit-trail signals visible from repo + declared constraints: data handling hints, "
        "logging/retention mentions, approval/autonomy hooks in yaml, PII-adjacent code. "
        "Flag gaps vs what doctrine or product.yaml implies."
    ),
    "reliability": (
        "Runbooks, restarts, health checks, error handling, idempotency, timeouts/retries in scripts and app code. "
        "Operational readiness from static inspection only."
    ),
    "performance": (
        "Declared budgets, obvious hot paths, N+1 patterns, unbounded work in scripts, asset/heavy deps. "
        "No fabricated latency numbers—unknown is fine."
    ),
    "store_business": (
        "Commercial/distribution hooks: pricing fields, store URLs, experiment records, monetization paths. "
        "Alignment between yaml declarations and any local evidence."
    ),
    "ux": (
        "In-repo support for UX work: documentation that mentions flows/onboarding/navigation/accessibility, "
        "declared UI scope paths in product.yaml (audit.ux.scope_roots), components/routes/tokens filenames, "
        "and UI test/tooling paths. Report presence only—do not judge quality, taste, or real usability."
    ),
}
