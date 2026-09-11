"""Read durable artifact paths into lightweight snapshots (no side effects)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.approval.models import ApprovalStatus
from argus.approval.store import list_records as list_approval_records
from argus.audit.bundle import ANGLE_IDS, bundle_audit_path
from argus.refinement.models import ArtifactType, RefinementSessionSnap
from argus.refinement.persistence import list_session_entries, read_json
from argus.refinement.queries import session_json_path


def parse_iso_timestamp(ts: str | None) -> datetime | None:
    if not ts or not str(ts).strip():
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class SignalsSnapshot:
    present: bool
    collected_at_utc: str | None
    path: str | None


@dataclass(frozen=True)
class AuditSnapshot:
    present: bool
    generated_at_utc: str | None
    path: str | None
    #: Per-angle ``angle_status`` from bundle (empty when absent or unreadable).
    angle_status: dict[str, str]


@dataclass(frozen=True)
class TemporalSnapshot:
    present: bool
    worst_freshness_status: str | None
    collected_at_utc: str | None
    path: str | None


@dataclass(frozen=True)
class ExecutionSnapshot:
    record_count: int
    path: str | None


def load_signals_snapshot(repo_root: Path, product_id: str) -> SignalsSnapshot:
    p = repo_root / "runs" / "signals" / "latest" / f"{product_id}.json"
    if not p.is_file():
        return SignalsSnapshot(present=False, collected_at_utc=None, path=None)
    raw = read_json(p)
    ts = None
    if raw:
        ts = str(raw.get("collected_at_utc") or "").strip() or None
    return SignalsSnapshot(present=True, collected_at_utc=ts, path=str(p))


def _angle_status_map(raw: dict[str, Any] | None) -> dict[str, str]:
    if not raw:
        return {}
    angles = raw.get("angles")
    if not isinstance(angles, dict):
        return {}
    out: dict[str, str] = {}
    for aid in ANGLE_IDS:
        block = angles.get(aid)
        if not isinstance(block, dict):
            continue
        st = str(block.get("angle_status", "") or "").strip()
        if st:
            out[aid] = st
    return out


def load_audit_snapshot(repo_root: Path, product_id: str) -> AuditSnapshot:
    p = bundle_audit_path(repo_root, product_id)
    if not p.is_file():
        return AuditSnapshot(present=False, generated_at_utc=None, path=None, angle_status={})
    raw = read_json(p)
    ts = None
    if raw:
        ts = str(raw.get("generated_at_utc") or "").strip() or None
    return AuditSnapshot(
        present=True,
        generated_at_utc=ts,
        path=str(p),
        angle_status=_angle_status_map(raw if isinstance(raw, dict) else None),
    )


def load_temporal_snapshot(repo_root: Path, product_id: str) -> TemporalSnapshot:
    """Latest derived temporal bundle under ``runs/temporal/latest/`` (``argus.temporal_bundle.v1``)."""
    p = repo_root.resolve() / "runs" / "temporal" / "latest" / f"{product_id}.json"
    if not p.is_file():
        return TemporalSnapshot(present=False, worst_freshness_status=None, collected_at_utc=None, path=None)
    raw = read_json(p)
    if not raw:
        return TemporalSnapshot(present=True, worst_freshness_status=None, collected_at_utc=None, path=str(p))
    ws = raw.get("worst_freshness_status")
    if ws is not None and not isinstance(ws, str):
        ws = str(ws)
    if isinstance(ws, str) and not ws.strip():
        ws = None
    cat = str(raw.get("collected_at_utc") or "").strip() or None
    return TemporalSnapshot(
        present=True,
        worst_freshness_status=ws if isinstance(ws, str) else None,
        collected_at_utc=cat,
        path=str(p),
    )


def load_execution_snapshot(repo_root: Path, product_id: str) -> ExecutionSnapshot:
    d = repo_root / "runs" / "execution" / product_id
    if not d.is_dir():
        return ExecutionSnapshot(record_count=0, path=None)
    n = len([x for x in d.glob("*.json") if x.is_file()])
    return ExecutionSnapshot(record_count=n, path=str(d))


def load_pending_approvals_for_product(repo_root: Path, product_id: str) -> list[dict[str, Any]]:
    """Pending approval records for ``product_id`` (execution gate; deterministic read)."""
    pid = str(product_id).strip()
    out: list[dict[str, Any]] = []
    for rec in list_approval_records(repo_root):
        if rec.product_id != pid:
            continue
        if rec.status != ApprovalStatus.PENDING:
            continue
        p = repo_root.resolve() / "runs" / "approval" / "records" / f"{rec.approval_id}.json"
        out.append(
            {
                "approval_id": rec.approval_id,
                "action_id": rec.action_id,
                "created_at": rec.created_at,
                "record_path": str(p) if p.is_file() else None,
            }
        )
    out.sort(key=lambda x: str(x.get("created_at") or x.get("approval_id")))
    return out


def list_refinement_sessions_for_product(repo_root: Path, product_id: str) -> list[RefinementSessionSnap]:
    out: list[RefinementSessionSnap] = []
    for row in list_session_entries(repo_root):
        if str(row.get("product_id", "")).strip() != product_id:
            continue
        sid = str(row.get("session_id", "")).strip()
        if not sid:
            continue
        sp = session_json_path(repo_root, sid)
        raw = read_json(sp)
        if not raw:
            continue
        try:
            at = str(raw.get("artifact_type", "")).strip()
            st = str(raw.get("status", "")).strip()
            cr = int(raw.get("current_round", 0))
            mx = int(raw.get("max_rounds", 4))
            upd = str(raw.get("updated_at_utc", "") or row.get("updated_at_utc", ""))
        except (TypeError, ValueError):
            continue
        out.append(
            RefinementSessionSnap(
                session_id=sid,
                artifact_type=at,
                product_id=product_id,
                status=st,
                current_round=cr,
                max_rounds=mx,
                updated_at_utc=upd,
            )
        )
    out.sort(key=lambda x: x.updated_at_utc or "", reverse=True)
    return out


def pick_latest_session(
    sessions: list[RefinementSessionSnap],
    artifact_type: ArtifactType,
) -> RefinementSessionSnap | None:
    want = artifact_type.value
    for s in sessions:
        if s.artifact_type == want:
            return s
    return None
