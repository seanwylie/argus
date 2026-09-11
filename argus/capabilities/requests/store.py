"""Filesystem persistence for capability requests."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.capabilities.requests.models import (
    CapabilityRequest,
    CapabilityRequestSource,
    CapabilityRequestStatus,
    is_terminal_status,
)
from argus.core.serialize import dumps_json, loads_json


def capability_requests_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "capabilities" / "requests"


def new_request_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"creq_{ts}_{secrets.token_hex(4)}"


def _path(repo_root: Path, request_id: str) -> Path:
    return capability_requests_dir(repo_root) / f"{request_id}.json"


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _record_from_dict(data: dict[str, Any]) -> CapabilityRequest:
    try:
        src = CapabilityRequestSource(str(data.get("source", "manual")))
    except ValueError:
        src = CapabilityRequestSource.MANUAL
    try:
        st = CapabilityRequestStatus(str(data.get("status", "open")))
    except ValueError:
        st = CapabilityRequestStatus.OPEN
    return CapabilityRequest(
        request_id=str(data.get("request_id", "")),
        title=str(data.get("title", "")),
        description=str(data.get("description", "")),
        source=src,
        status=st,
        capability_hint=str(data.get("capability_hint") or ""),
        product_id=(
            None if data.get("product_id") in (None, "") else str(data.get("product_id"))
        ),
        source_ref=dict(data.get("source_ref") or {}),
        created_at=str(data.get("created_at", "") or ""),
        updated_at=str(data.get("updated_at", "") or ""),
        resolution_note=str(data.get("resolution_note") or ""),
        schema=str(data.get("schema") or "argus.capability_request.v1"),
        metadata=dict(data.get("metadata") or {}),
    )


def save_request(repo_root: Path, req: CapabilityRequest) -> Path:
    d = capability_requests_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    p = _path(repo_root, req.request_id)
    payload = {
        "schema": req.schema,
        "request_id": req.request_id,
        "title": req.title,
        "description": req.description,
        "source": req.source.value,
        "status": req.status.value,
        "capability_hint": req.capability_hint,
        "product_id": req.product_id,
        "source_ref": dict(req.source_ref),
        "created_at": req.created_at,
        "updated_at": req.updated_at,
        "resolution_note": req.resolution_note,
        "metadata": dict(req.metadata),
    }
    p.write_text(dumps_json(payload), encoding="utf-8")
    return p


def load_request(repo_root: Path, request_id: str) -> CapabilityRequest | None:
    p = _path(repo_root, request_id)
    if not p.is_file():
        return None
    try:
        data = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    return _record_from_dict(data)


def list_requests(
    repo_root: Path,
    *,
    status: CapabilityRequestStatus | None = None,
) -> list[CapabilityRequest]:
    base = capability_requests_dir(repo_root)
    if not base.is_dir():
        return []
    out: list[CapabilityRequest] = []
    for p in sorted(base.glob("creq_*.json")):
        try:
            data = loads_json(p.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            continue
        if isinstance(data, dict):
            rec = _record_from_dict(data)
            if status is None or rec.status == status:
                out.append(rec)
    out.sort(key=lambda r: r.created_at or r.request_id)
    return out


def create_request(
    repo_root: Path,
    *,
    title: str,
    description: str,
    source: CapabilityRequestSource,
    capability_hint: str = "",
    product_id: str | None = None,
    source_ref: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> CapabilityRequest:
    now = _utc()
    req = CapabilityRequest(
        request_id=new_request_id(),
        title=title.strip(),
        description=description.strip(),
        source=source,
        status=CapabilityRequestStatus.OPEN,
        capability_hint=capability_hint.strip(),
        product_id=product_id.strip() if product_id else None,
        source_ref=dict(source_ref or {}),
        created_at=now,
        updated_at=now,
        metadata=dict(metadata or {}),
    )
    save_request(repo_root, req)
    return req


def update_request_status(
    repo_root: Path,
    request_id: str,
    *,
    status: CapabilityRequestStatus,
    resolution_note: str = "",
) -> CapabilityRequest:
    rec = load_request(repo_root, request_id)
    if rec is None:
        raise KeyError(f"unknown request_id: {request_id!r}")
    if is_terminal_status(rec.status):
        raise ValueError(f"request {request_id} is already terminal ({rec.status.value})")
    now = _utc()
    note = resolution_note.strip()
    merged_note = note if note else rec.resolution_note
    rec = CapabilityRequest(
        request_id=rec.request_id,
        title=rec.title,
        description=rec.description,
        source=rec.source,
        status=status,
        capability_hint=rec.capability_hint,
        product_id=rec.product_id,
        source_ref=dict(rec.source_ref),
        created_at=rec.created_at,
        updated_at=now,
        resolution_note=merged_note,
        schema=rec.schema,
        metadata=dict(rec.metadata),
    )
    save_request(repo_root, rec)
    return rec
