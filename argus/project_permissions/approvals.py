"""
Phase 2: durable operator approval responses for Phase 1 ``confirm`` policy values.

Artifact-backed (no UI). Refusal remains the default when no grant applies.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from argus.core.serialize import dumps_json

PENDING_APPROVAL_SCHEMA = "argus.pending_approval_request.v1"
APPROVAL_GRANTS_SCHEMA = "argus.approval_grants.v1"
APPROVAL_RESPONSE_AUDIT_SCHEMA = "argus.approval_response_audit.v1"

ApprovalResponseKind = Literal["confirm_once", "always", "no"]

SUGGESTED_OPERATOR_CHOICES = [
    {
        "response": "confirm_once",
        "description": "Allow the next execution for this product, policy field, and orchestration action only; then revoke.",
    },
    {
        "response": "always",
        "description": "Allow all future executions for this product and policy field until revoked (edit grants file or remove entry).",
    },
    {
        "response": "no",
        "description": "Decline approval; execution stays blocked (recorded for audit only).",
    },
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def pending_approvals_dir(repo_root: Path, product_id: str) -> Path:
    d = repo_root.resolve() / "runs" / "policy" / "pending_approvals" / str(product_id).strip()
    d.mkdir(parents=True, exist_ok=True)
    return d


def approval_grants_path(repo_root: Path, product_id: str) -> Path:
    """Path to grants file; does not create directories (reads must be side-effect free)."""
    root = repo_root.resolve()
    return root / "runs" / "policy" / "approval_grants" / f"{str(product_id).strip()}.json"


def approval_response_audit_dir(repo_root: Path) -> Path:
    d = repo_root.resolve() / "runs" / "policy" / "approval_response_audit"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_approval_grants(repo_root: Path, product_id: str) -> dict[str, Any]:
    path = approval_grants_path(repo_root, product_id)
    if not path.is_file():
        return {
            "schema": APPROVAL_GRANTS_SCHEMA,
            "product_id": str(product_id).strip(),
            "grants": [],
        }
    import json

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema": APPROVAL_GRANTS_SCHEMA,
            "product_id": str(product_id).strip(),
            "grants": [],
        }
    if not isinstance(raw, dict):
        return {
            "schema": APPROVAL_GRANTS_SCHEMA,
            "product_id": str(product_id).strip(),
            "grants": [],
        }
    raw.setdefault("schema", APPROVAL_GRANTS_SCHEMA)
    raw.setdefault("grants", [])
    if not isinstance(raw["grants"], list):
        raw["grants"] = []
    return raw


def save_approval_grants(repo_root: Path, product_id: str, body: dict[str, Any]) -> Path:
    path = approval_grants_path(repo_root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(body)
    out["schema"] = APPROVAL_GRANTS_SCHEMA
    out["product_id"] = str(product_id).strip()
    path.write_text(dumps_json(out) + "\n", encoding="utf-8")
    return path


def write_pending_approval_request(
    repo_root: Path,
    *,
    product_id: str,
    orchestration_action_id: str,
    phase1_policy_field: str,
    reason: str,
    execution_path: str,
    phase1_decision_audit_path_repo_relative: str,
) -> Path:
    """Write when policy ``confirm`` blocks orchestration (no grant)."""
    root = repo_root.resolve()
    pid = str(product_id).strip()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_action = "".join(c if c.isalnum() or c in "-_" else "_" for c in orchestration_action_id.strip())[:80]
    fname = f"pending_{ts}_{safe_action}.json"
    out_dir = pending_approvals_dir(root, pid)
    path = out_dir / fname
    try:
        rel_self = str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel_self = str(path)
    body: dict[str, Any] = {
        "schema": PENDING_APPROVAL_SCHEMA,
        "product_id": pid,
        "orchestration_action_id": str(orchestration_action_id).strip(),
        "phase1_policy_field": str(phase1_policy_field).strip(),
        "reason": str(reason or "").strip(),
        "requested_at_utc": _utc_now_iso(),
        "execution_path": str(execution_path or "").strip(),
        "phase1_decision_audit_path_repo_relative": str(phase1_decision_audit_path_repo_relative or "").strip(),
        "suggested_operator_choices": list(SUGGESTED_OPERATOR_CHOICES),
        "artifact_path_repo_relative": rel_self,
    }
    path.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return path


def write_approval_response_audit(
    repo_root: Path,
    *,
    product_id: str,
    phase1_policy_field: str,
    response: ApprovalResponseKind,
    orchestration_action_id: str | None,
    note: str | None,
) -> Path:
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fname = f"response_{ts}_{product_id}_{phase1_policy_field}.json"
    path = approval_response_audit_dir(root) / fname
    body: dict[str, Any] = {
        "schema": APPROVAL_RESPONSE_AUDIT_SCHEMA,
        "product_id": str(product_id).strip(),
        "phase1_policy_field": str(phase1_policy_field).strip(),
        "response": response,
        "orchestration_action_id": orchestration_action_id,
        "note": (note or "").strip() or None,
        "responded_at_utc": _utc_now_iso(),
    }
    path.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return path


def record_operator_response(
    repo_root: Path,
    *,
    product_id: str,
    phase1_policy_field: str,
    response: ApprovalResponseKind,
    orchestration_action_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """
    Record operator choice. ``confirm_once`` requires ``orchestration_action_id`` for orchestration scope.

    ``no`` writes audit only (no allow grant). ``always`` / ``confirm_once`` add or update grant rows.
    """
    pid = str(product_id).strip()
    field = str(phase1_policy_field).strip()
    write_approval_response_audit(
        repo_root,
        product_id=pid,
        phase1_policy_field=field,
        response=response,
        orchestration_action_id=orchestration_action_id,
        note=note,
    )
    if response == "no":
        return {"ok": True, "recorded": "audit_only", "response": "no"}

    data = load_approval_grants(repo_root, pid)
    grants: list[dict[str, Any]] = list(data.get("grants") or [])

    if response == "always":
        # Remove prior always for same field (replace)
        grants = [g for g in grants if not (g.get("kind") == "always" and g.get("phase1_policy_field") == field)]
        grants.append(
            {
                "grant_id": str(uuid.uuid4()),
                "kind": "always",
                "phase1_policy_field": field,
                "responded_at_utc": _utc_now_iso(),
                "note": (note or "").strip() or None,
            },
        )
        data["grants"] = grants
        path = save_approval_grants(repo_root, pid, data)
        return {"ok": True, "recorded": "grant", "approval_grants_path": str(path), "kind": "always"}

    if response == "confirm_once":
        if not orchestration_action_id or not str(orchestration_action_id).strip():
            raise ValueError("confirm_once requires --orchestration-action <action_id>")
        aid = str(orchestration_action_id).strip()
        # One active confirm_once per (field, action)
        grants = [
            g
            for g in grants
            if not (
                g.get("kind") == "confirm_once"
                and g.get("phase1_policy_field") == field
                and g.get("orchestration_action_id") == aid
                and not g.get("consumed_at_utc")
            )
        ]
        grants.append(
            {
                "grant_id": str(uuid.uuid4()),
                "kind": "confirm_once",
                "phase1_policy_field": field,
                "orchestration_action_id": aid,
                "consumed_at_utc": None,
                "responded_at_utc": _utc_now_iso(),
                "note": (note or "").strip() or None,
            },
        )
        data["grants"] = grants
        path = save_approval_grants(repo_root, pid, data)
        return {"ok": True, "recorded": "grant", "approval_grants_path": str(path), "kind": "confirm_once", "grant_id": grants[-1]["grant_id"]}

    raise ValueError(f"unknown response {response!r}")


def find_grant_for_confirm(
    repo_root: Path,
    product_id: str,
    phase1_policy_field: str,
    orchestration_action_id: str | None,
) -> dict[str, Any] | None:
    """Return a grant dict that satisfies confirm for this scope, or None."""
    pid = str(product_id).strip()
    field = str(phase1_policy_field).strip()
    data = load_approval_grants(repo_root, pid)
    aid = str(orchestration_action_id).strip() if orchestration_action_id else ""
    for g in data.get("grants") or []:
        if not isinstance(g, dict):
            continue
        if g.get("phase1_policy_field") != field:
            continue
        kind = g.get("kind")
        if kind == "always":
            return g
        if kind == "confirm_once" and aid and g.get("orchestration_action_id") == aid:
            if g.get("consumed_at_utc"):
                continue
            return g
    return None


def consume_confirm_once_grant(repo_root: Path, product_id: str, grant_id: str) -> bool:
    """Mark one confirm_once grant consumed after successful execution."""
    pid = str(product_id).strip()
    gid = str(grant_id).strip()
    data = load_approval_grants(repo_root, pid)
    grants: list[dict[str, Any]] = list(data.get("grants") or [])
    now = _utc_now_iso()
    found = False
    for g in grants:
        if g.get("grant_id") == gid and g.get("kind") == "confirm_once":
            g["consumed_at_utc"] = now
            found = True
            break
    if not found:
        return False
    data["grants"] = grants
    save_approval_grants(repo_root, pid, data)
    return True


def revoke_always_grant(repo_root: Path, product_id: str, phase1_policy_field: str) -> bool:
    """Remove an always grant for a field (operator maintenance)."""
    pid = str(product_id).strip()
    field = str(phase1_policy_field).strip()
    data = load_approval_grants(repo_root, pid)
    grants = [g for g in (data.get("grants") or []) if not (g.get("kind") == "always" and g.get("phase1_policy_field") == field)]
    if len(grants) == len(data.get("grants") or []):
        return False
    data["grants"] = grants
    save_approval_grants(repo_root, pid, data)
    return True


__all__ = [
    "APPROVAL_GRANTS_SCHEMA",
    "APPROVAL_RESPONSE_AUDIT_SCHEMA",
    "ApprovalResponseKind",
    "PENDING_APPROVAL_SCHEMA",
    "SUGGESTED_OPERATOR_CHOICES",
    "approval_grants_path",
    "consume_confirm_once_grant",
    "find_grant_for_confirm",
    "load_approval_grants",
    "pending_approvals_dir",
    "record_operator_response",
    "revoke_always_grant",
    "write_pending_approval_request",
    "write_approval_response_audit",
]
