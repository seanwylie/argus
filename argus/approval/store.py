"""Filesystem persistence under ``runs/approval/records/``."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.approval.models import ApprovalRecord, ApprovalStatus
from argus.core.serialize import dumps_json, loads_json


def approval_records_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "approval" / "records"


def new_approval_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"appr_{ts}_{secrets.token_hex(4)}"


def _path_for(repo_root: Path, approval_id: str) -> Path:
    return approval_records_dir(repo_root) / f"{approval_id}.json"


def save_record(repo_root: Path, rec: ApprovalRecord) -> Path:
    d = approval_records_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    p = _path_for(repo_root, rec.approval_id)
    payload = {
        "schema": rec.schema,
        "approval_id": rec.approval_id,
        "action_id": rec.action_id,
        "product_id": rec.product_id,
        "status": rec.status.value,
        "reason": rec.reason,
        "created_at": rec.created_at,
        "decided_at": rec.decided_at,
        "metadata": dict(rec.metadata),
    }
    p.write_text(dumps_json(payload), encoding="utf-8")
    return p


def load_record(repo_root: Path, approval_id: str) -> ApprovalRecord | None:
    p = _path_for(repo_root, approval_id)
    if not p.is_file():
        return None
    try:
        data = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return _record_from_dict(data)


def _record_from_dict(data: dict[str, Any]) -> ApprovalRecord:
    st = str(data.get("status", "pending"))
    try:
        status = ApprovalStatus(st)
    except ValueError:
        status = ApprovalStatus.PENDING
    return ApprovalRecord(
        approval_id=str(data.get("approval_id", "")),
        action_id=str(data.get("action_id", "")),
        product_id=str(data.get("product_id", "")),
        status=status,
        reason=str(data.get("reason", "") or ""),
        created_at=str(data.get("created_at", "") or ""),
        decided_at=(
            None
            if data.get("decided_at") in (None, "")
            else str(data.get("decided_at"))
        ),
        schema=str(data.get("schema", "argus.approval.v1")),
        metadata=dict(data.get("metadata") or {}),
    )


def list_records(repo_root: Path) -> list[ApprovalRecord]:
    base = approval_records_dir(repo_root)
    if not base.is_dir():
        return []
    out: list[ApprovalRecord] = []
    for p in sorted(base.glob("appr_*.json")):
        try:
            data = loads_json(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict):
            out.append(_record_from_dict(data))
    out.sort(key=lambda r: r.created_at or r.approval_id)
    return out


def create_pending(
    repo_root: Path,
    *,
    action_id: str,
    product_id: str,
    reason: str = "",
) -> ApprovalRecord:
    """Create a new pending approval request."""
    now = datetime.now(timezone.utc).isoformat()
    rec = ApprovalRecord(
        approval_id=new_approval_id(),
        action_id=action_id.strip(),
        product_id=product_id.strip(),
        status=ApprovalStatus.PENDING,
        reason=reason.strip(),
        created_at=now,
        decided_at=None,
    )
    save_record(repo_root, rec)
    return rec


def approve(repo_root: Path, approval_id: str, *, note: str = "") -> ApprovalRecord:
    rec = load_record(repo_root, approval_id)
    if rec is None:
        raise KeyError(f"unknown approval_id: {approval_id!r}")
    if rec.status != ApprovalStatus.PENDING:
        raise ValueError(f"approval {approval_id} is not pending (status={rec.status.value})")
    now = datetime.now(timezone.utc).isoformat()
    rec = ApprovalRecord(
        approval_id=rec.approval_id,
        action_id=rec.action_id,
        product_id=rec.product_id,
        status=ApprovalStatus.APPROVED,
        reason=rec.reason,
        created_at=rec.created_at,
        decided_at=now,
        metadata={**rec.metadata, "approval_note": note},
    )
    save_record(repo_root, rec)
    return rec


def reject(repo_root: Path, approval_id: str, *, reason: str) -> ApprovalRecord:
    rec = load_record(repo_root, approval_id)
    if rec is None:
        raise KeyError(f"unknown approval_id: {approval_id!r}")
    if rec.status != ApprovalStatus.PENDING:
        raise ValueError(f"approval {approval_id} is not pending (status={rec.status.value})")
    now = datetime.now(timezone.utc).isoformat()
    rec = ApprovalRecord(
        approval_id=rec.approval_id,
        action_id=rec.action_id,
        product_id=rec.product_id,
        status=ApprovalStatus.REJECTED,
        reason=(rec.reason + "\n" if rec.reason else "") + f"rejected: {reason.strip()}",
        created_at=rec.created_at,
        decided_at=now,
        metadata=rec.metadata,
    )
    save_record(repo_root, rec)
    return rec


def has_approved_for_action(
    repo_root: Path,
    action_id: str,
    product_id: str,
) -> bool:
    """True if there is at least one **approved** record for this action and product."""
    aid = action_id.strip()
    pid = product_id.strip()
    latest: tuple[str, ApprovalRecord] | None = None
    for rec in list_records(repo_root):
        if rec.action_id != aid or rec.product_id != pid:
            continue
        if rec.status != ApprovalStatus.APPROVED:
            continue
        decided = rec.decided_at or rec.created_at
        if latest is None or decided > latest[0]:
            latest = (decided, rec)
    return latest is not None
