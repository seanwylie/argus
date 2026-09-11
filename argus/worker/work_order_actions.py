"""
Durable work order approval / activation transitions (steward → inspectable artifacts).

Actions are persisted separately from the stamped work order JSON; loaders merge action
history so ``status`` reflects the **effective** state while ``status_stamped`` keeps the
on-disk issuance snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json, to_jsonable
from argus.worker.work_orders import WORK_ORDER_SCHEMA, work_orders_dir

WORK_ORDER_ACTION_SCHEMA: Final = "argus.work_order_action.v1"

ACTION_APPROVE: Final = "approve"
ACTION_REJECT: Final = "reject"
ACTION_CANCEL: Final = "cancel"
ACTION_REOPEN: Final = "reopen"

_ACTION_TYPES: Final = frozenset({ACTION_APPROVE, ACTION_REJECT, ACTION_CANCEL, ACTION_REOPEN})


def work_order_actions_dir(repo_root: Path) -> Path:
    """``runs/worker/work_orders/actions/``"""
    return work_orders_dir(repo_root) / "actions"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_action_id(*, acted_at_utc: str, work_order_id: str, action_type: str) -> str:
    """Deterministic id (stable for tests)."""
    base = f"{acted_at_utc}|{work_order_id}|{action_type}".encode("utf-8")
    digest = hashlib.sha256(base).hexdigest()[:12]
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})",
        str(acted_at_utc).strip(),
    )
    if m:
        ts = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}"
    else:
        ts = "unknown"
    return f"act_{ts}_{digest}"


def _actions_subdir(repo_root: Path, work_order_id: str) -> Path:
    w = str(work_order_id).strip()
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "_", w).strip("_") or "unknown"
    return work_order_actions_dir(Path(repo_root).resolve()) / (safe[:240] if len(safe) > 240 else safe)


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def load_actions_for_work_order(repo_root: Path, work_order_id: str) -> list[dict[str, Any]]:
    """Load valid ``argus.work_order_action.v1`` records for this work order, oldest first."""
    root = Path(repo_root).resolve()
    woid = str(work_order_id).strip()
    if not woid:
        return []
    d = _actions_subdir(root, woid)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in d.glob("*.json"):
        raw = _load_json(path)
        if not raw or str(raw.get("schema") or "") != WORK_ORDER_ACTION_SCHEMA:
            continue
        if str(raw.get("work_order_id") or "").strip() != woid:
            continue
        out.append(raw)

    def sort_key(a: dict[str, Any]) -> tuple[str, str]:
        return (str(a.get("acted_at_utc") or ""), str(a.get("action_id") or ""))

    return sorted(out, key=sort_key)


def compute_effective_status(stamped_status: str, actions: list[dict[str, Any]]) -> str:
    """Fold action history: each record's ``resulting_status`` replaces the running state."""
    s = str(stamped_status).strip()
    for a in actions:
        rs = str(a.get("resulting_status") or "").strip()
        if rs:
            s = rs
    return s


def _action_history_summary(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "action_id": str(a.get("action_id") or ""),
            "action_type": str(a.get("action_type") or ""),
            "acted_at_utc": str(a.get("acted_at_utc") or ""),
            "acted_by": str(a.get("acted_by") or ""),
            "resulting_status": str(a.get("resulting_status") or ""),
        }
        for a in actions
    ]


def overlay_work_order_view(repo_root: Path, stamped: dict[str, Any]) -> dict[str, Any]:
    """
    Copy of the stamped work order with:

    * ``status`` — effective status after action history
    * ``status_stamped`` — ``status`` field from the JSON file (issuance snapshot)
    * ``action_history`` — condensed chronological actions (inspectability)
    """
    woid = str(stamped.get("work_order_id") or "").strip()
    actions = load_actions_for_work_order(repo_root, woid)
    stamped_st = str(stamped.get("status") or "").strip()
    eff = compute_effective_status(stamped_st, actions)
    out = dict(stamped)
    out["status_stamped"] = stamped_st
    out["status"] = eff
    out["action_history"] = _action_history_summary(actions)
    return out


def _resulting_status_for_action(current: str, action_type: str) -> str:
    """Return new status or raise ValueError."""
    c = str(current).strip()
    at = str(action_type).strip()
    if at == ACTION_APPROVE:
        if c == "pending_approval":
            return "approved"
        raise ValueError(f"approve is only valid from pending_approval (current={c!r})")
    if at == ACTION_REJECT:
        if c in ("pending_approval", "approved"):
            return "rejected"
        raise ValueError(f"reject requires pending_approval or approved (current={c!r})")
    if at == ACTION_CANCEL:
        if c in ("pending_approval", "approved"):
            return "cancelled"
        raise ValueError(f"cancel requires pending_approval or approved (current={c!r})")
    if at == ACTION_REOPEN:
        if c in ("rejected", "cancelled"):
            return "pending_approval"
        raise ValueError(f"reopen requires rejected or cancelled (current={c!r})")
    raise ValueError(f"unknown action_type {at!r}")


def create_work_order_action_payload(
    *,
    work_order_id: str,
    action_type: str,
    acted_by: str,
    note: str,
    previous_status: str,
    resulting_status: str,
    acted_at_utc: str | None = None,
    action_id: str | None = None,
) -> dict[str, Any]:
    sat = acted_at_utc or _iso_now()
    at = str(action_type).strip()
    if at not in _ACTION_TYPES:
        raise ValueError(f"action_type must be one of {sorted(_ACTION_TYPES)}")
    woid = str(work_order_id).strip()
    if not woid:
        raise ValueError("work_order_id is required")
    aid = (action_id or "").strip() or new_action_id(
        acted_at_utc=sat, work_order_id=woid, action_type=at
    )
    return {
        "schema": WORK_ORDER_ACTION_SCHEMA,
        "action_id": aid,
        "work_order_id": woid,
        "action_type": at,
        "acted_at_utc": sat,
        "acted_by": str(acted_by).strip() or "steward",
        "note": str(note).strip(),
        "previous_status": str(previous_status).strip(),
        "resulting_status": str(resulting_status).strip(),
    }


def write_work_order_action(
    repo_root: Path,
    payload: dict[str, Any],
) -> Path:
    """Persist ``runs/worker/work_orders/actions/<work_order_id>/<action_id>.json``."""
    root = Path(repo_root).resolve()
    if str(payload.get("schema") or "") != WORK_ORDER_ACTION_SCHEMA:
        raise ValueError(f"payload.schema must be {WORK_ORDER_ACTION_SCHEMA!r}")
    woid = str(payload.get("work_order_id") or "").strip()
    aid = str(payload.get("action_id") or "").strip()
    if not woid or not aid:
        raise ValueError("work_order_id and action_id are required")
    d = _actions_subdir(root, woid)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{aid}.json"
    path.write_text(dumps_json(to_jsonable(dict(payload))) + "\n", encoding="utf-8")
    return path


def _load_stamped_work_order_only(repo_root: Path, work_order_id: str) -> dict[str, Any] | None:
    """Read stamped JSON only (no action overlay)."""
    root = Path(repo_root).resolve()
    woid = str(work_order_id).strip()
    if not woid:
        return None
    raw = _load_json(work_orders_dir(root) / f"{woid}.json")
    if not raw or str(raw.get("schema") or "") != WORK_ORDER_SCHEMA:
        return None
    return raw


def apply_work_order_action(
    repo_root: Path,
    *,
    work_order_id: str,
    action_type: str,
    acted_by: str = "steward",
    note: str = "",
    acted_at_utc: str | None = None,
    save: bool = True,
) -> dict[str, Any]:
    """
    Validate transition from current **effective** status, optionally write action artifact.

    Returns ``argus.worker.work_order_action_report.v1``.
    """
    root = Path(repo_root).resolve()
    woid = str(work_order_id).strip()
    if not woid:
        return {
            "schema": "argus.worker.work_order_action_report.v1",
            "action": None,
            "saved_path": None,
            "exit_code": 1,
            "error": "work_order_id is required",
        }

    raw = _load_stamped_work_order_only(root, woid)
    if not raw:
        return {
            "schema": "argus.worker.work_order_action_report.v1",
            "action": None,
            "saved_path": None,
            "exit_code": 1,
            "error": f"No stamped work order at runs/worker/work_orders/{woid}.json",
        }

    existing = load_actions_for_work_order(root, woid)
    stamped_st = str(raw.get("status") or "").strip()
    current_effective = compute_effective_status(stamped_st, existing)

    try:
        resulting = _resulting_status_for_action(current_effective, action_type)
    except ValueError as e:
        return {
            "schema": "argus.worker.work_order_action_report.v1",
            "action": None,
            "saved_path": None,
            "exit_code": 1,
            "error": str(e),
            "context": {
                "work_order_id": woid,
                "status_stamped": stamped_st,
                "effective_status_before": current_effective,
            },
        }

    payload = create_work_order_action_payload(
        work_order_id=woid,
        action_type=action_type,
        acted_by=acted_by,
        note=note,
        previous_status=current_effective,
        resulting_status=resulting,
        acted_at_utc=acted_at_utc,
    )

    saved_path: str | None = None
    if save:
        p = write_work_order_action(root, payload)
        saved_path = str(p)

    return {
        "schema": "argus.worker.work_order_action_report.v1",
        "action": payload,
        "saved_path": saved_path,
        "exit_code": 0,
        "error": None,
        "context": {
            "work_order_id": woid,
            "status_stamped": stamped_st,
            "effective_status_before": current_effective,
            "effective_status_after": resulting,
        },
    }


__all__ = [
    "ACTION_APPROVE",
    "ACTION_CANCEL",
    "ACTION_REJECT",
    "ACTION_REOPEN",
    "WORK_ORDER_ACTION_SCHEMA",
    "apply_work_order_action",
    "compute_effective_status",
    "create_work_order_action_payload",
    "load_actions_for_work_order",
    "new_action_id",
    "overlay_work_order_view",
    "work_order_actions_dir",
    "write_work_order_action",
]
