"""
Concise Phase 1 + Phase 2 approval visibility for operator triage (portfolio surfaces).

Does not replace policy files or full ``phase1_summary_for_product`` — adds **actionable labels**
and **one-line** summaries for queue/readiness views.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.project_permissions.approvals import load_approval_grants
from argus.project_permissions.gate import phase1_summary_for_product
from argus.project_permissions.schema import PHASE1_KEYS

PERMISSION_GATE_SUMMARY_SCHEMA = "argus.permission_gate_summary.v1"


def _pending_approval_dir(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "policy" / "pending_approvals" / str(product_id).strip()


def list_pending_approval_summaries(repo_root: Path, product_id: str, *, limit: int = 5) -> list[dict[str, Any]]:
    """Latest pending approval request artifacts (newest first), without creating directories."""
    base = _pending_approval_dir(repo_root, product_id)
    if not base.is_dir():
        return []
    paths = [p for p in base.glob("pending_*.json") if p.is_file()]
    paths.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[dict[str, Any]] = []
    for p in paths[: max(0, limit)]:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        reason = str(raw.get("reason") or "")
        out.append(
            {
                "artifact_path_repo_relative": str(raw.get("artifact_path_repo_relative") or ""),
                "orchestration_action_id": str(raw.get("orchestration_action_id") or ""),
                "phase1_policy_field": str(raw.get("phase1_policy_field") or ""),
                "reason_one_line": (reason.replace("\n", " ").strip()[:240] + ("…" if len(reason) > 240 else "")),
                "requested_at_utc": str(raw.get("requested_at_utc") or ""),
            },
        )
    return out


def summarize_active_grants(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Active approval grants: ``always`` fields and outstanding ``confirm_once`` (not consumed)."""
    data = load_approval_grants(repo_root, product_id)
    always_fields: list[str] = []
    confirm_once_outstanding: list[dict[str, str]] = []
    for g in data.get("grants") or []:
        if not isinstance(g, dict):
            continue
        kind = str(g.get("kind") or "")
        field = str(g.get("phase1_policy_field") or "")
        if kind == "always" and field:
            always_fields.append(field)
        elif kind == "confirm_once" and not g.get("consumed_at_utc"):
            confirm_once_outstanding.append(
                {
                    "phase1_policy_field": field,
                    "orchestration_action_id": str(g.get("orchestration_action_id") or ""),
                },
            )
    return {
        "always_fields": sorted(set(always_fields)),
        "confirm_once_outstanding": confirm_once_outstanding,
    }


def build_permission_gate_summary(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Single-product summary for operator triage: policy stance (compact), environment mismatch,
    pending approvals, active grants, and machine-friendly ``triage_labels``.
    """
    pid = str(product_id or "").strip()
    root = repo_root.resolve()
    pp = phase1_summary_for_product(root, pid, products_dir=products_dir)

    triage_labels: list[str] = []
    if pp.get("policy_load_error"):
        return {
            "schema": PERMISSION_GATE_SUMMARY_SCHEMA,
            "product_id": pid,
            "policy_valid": False,
            "policy_error": str(pp.get("policy_load_error") or ""),
            "policy_path": pp.get("policy_path"),
            "triage_labels": ["invalid_policy_file"],
            "policy_compact": None,
            "environment_mismatch": False,
            "environment_mismatch_rows": [],
            "pending_approvals": [],
            "grants": {"always_fields": [], "confirm_once_outstanding": []},
            "one_line": "invalid argus.policy.yaml — fix YAML/quoting (see policy_error)",
        }

    pol = pp.get("policy") or {}
    perms: dict[str, str] = dict((pol.get("permissions") or {}))
    mism = pp.get("policy_environment_mismatches") or []

    deny_keys = sorted(k for k in PHASE1_KEYS if perms.get(k) == "no")
    confirm_keys = sorted(k for k in PHASE1_KEYS if perms.get(k) == "confirm")
    if deny_keys:
        triage_labels.append("policy_denies_present")
    if confirm_keys:
        triage_labels.append("policy_confirm_required")

    compact_parts: list[str] = []
    for k in PHASE1_KEYS:
        v = perms.get(k)
        if v in ("no", "confirm"):
            compact_parts.append(f"{k}={v}")
    policy_compact = "; ".join(compact_parts) if compact_parts else "all keys yes (full allow where evaluated)"

    env_mismatch = bool(mism)
    if env_mismatch:
        triage_labels.append("environment_capability_gap")

    pending = list_pending_approval_summaries(root, pid, limit=5)
    if pending:
        triage_labels.append("pending_approval_artifacts")

    grants = summarize_active_grants(root, pid)
    if grants["always_fields"]:
        triage_labels.append("active_always_grant")
    if grants["confirm_once_outstanding"]:
        triage_labels.append("active_confirm_once_grant")

    # One line for queue rows (keep short)
    parts: list[str] = []
    if deny_keys:
        parts.append(f"deny:{','.join(deny_keys[:3])}{'+' if len(deny_keys) > 3 else ''}")
    if confirm_keys and not pending:
        parts.append(f"confirm:{len(confirm_keys)}keys")
    elif confirm_keys and pending:
        parts.append(f"confirm:{len(confirm_keys)}keys,pending:{len(pending)}")
    if env_mismatch:
        parts.append("env-gap")
    if grants["always_fields"]:
        parts.append(f"grant(always:{','.join(grants['always_fields'][:2])})")
    if pending:
        pa0 = pending[0]
        parts.append(f"wait:{pa0.get('orchestration_action_id') or '?'}")

    one_line = " | ".join(parts) if parts else "policy ok (no deny/confirm/pending flags)"

    return {
        "schema": PERMISSION_GATE_SUMMARY_SCHEMA,
        "product_id": pid,
        "policy_valid": True,
        "policy_error": None,
        "policy_path": pol.get("policy_path"),
        "triage_labels": sorted(set(triage_labels)),
        "policy_compact": policy_compact,
        "deny_keys": deny_keys,
        "confirm_keys": confirm_keys,
        "environment_mismatch": env_mismatch,
        "environment_mismatch_rows": list(mism) if mism else [],
        "pending_approvals": pending,
        "grants": grants,
        "one_line": one_line[:300],
    }


__all__ = [
    "PERMISSION_GATE_SUMMARY_SCHEMA",
    "build_permission_gate_summary",
    "list_pending_approval_summaries",
    "summarize_active_grants",
]
