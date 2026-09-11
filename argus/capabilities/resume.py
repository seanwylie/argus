"""
Resume autonomous work after capability requests are fulfilled.

Clears capability pauses, re-validates gated actions, and optionally enqueues
executable work while still respecting autonomy policy and approval rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.actions.models import ActionContract
from argus.approval.rules import evaluate_auto_approval
from argus.autonomy.controller import (
    check_autonomy_execution,
    load_state,
    remove_pause_for_request,
    save_state,
)
from argus.capabilities.requests.models import CapabilityRequestStatus, is_terminal_status
from argus.capabilities.requests.store import load_request
from argus.core.serialize import dumps_json
from argus.planning.models import PlanningActionsBundle
from argus.planning.plan_actions import build_planning_actions


def blocked_actions_path(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "autonomy" / "blocked_actions.json"


def sync_blocked_actions_artifact(repo_root: Path) -> Path:
    """
    Materialize ``runs/autonomy/blocked_actions.json`` from ``capability_pauses`` in state
    plus open capability requests (for operator visibility).
    """
    root = repo_root.resolve()
    st = load_state(root)
    pauses = st.get("capability_pauses")
    items: list[dict[str, Any]] = []
    if isinstance(pauses, list):
        for p in pauses:
            if not isinstance(p, dict):
                continue
            rid = str(p.get("request_id") or "")
            aid = str(p.get("action_id") or "")
            pid = str(p.get("product_id") or "")
            req = load_request(root, rid) if rid else None
            reason = "missing capability"
            if req and req.status in (CapabilityRequestStatus.OPEN, CapabilityRequestStatus.ACKNOWLEDGED):
                reason = f"pending capability request {rid} ({req.capability_hint or 'no hint'})"
            items.append(
                {
                    "action_id": aid,
                    "product_id": pid,
                    "request_id": rid or None,
                    "reason": reason,
                }
            )
    payload = {
        "schema": "argus.blocked_actions.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "items": items,
    }
    p = blocked_actions_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(payload), encoding="utf-8")
    return p


def _load_planning_bundle(repo_root: Path, products_dir: Path | None) -> PlanningActionsBundle:
    return build_planning_actions(repo_root, products_dir=products_dir)


def _contract_for_action(bundle: PlanningActionsBundle, action_id: str) -> ActionContract | None:
    for a in bundle.actions:
        if a.action_id == action_id:
            return a
    return None


def _append_executable(
    repo_root: Path,
    *,
    action_id: str,
    product_id: str,
    source: str,
) -> None:
    st = load_state(repo_root)
    q = st.get("executable_queue")
    if not isinstance(q, list):
        q = []
    # de-dup
    for row in q:
        if isinstance(row, dict) and row.get("action_id") == action_id and row.get("product_id") == product_id:
            return
    q.append(
        {
            "action_id": action_id,
            "product_id": product_id,
            "queued_at_utc": datetime.now(timezone.utc).isoformat(),
            "source": source,
        }
    )
    st["executable_queue"] = q
    save_state(repo_root, st)


@dataclass
class ResumeResult:
    cleared_pauses: list[str]
    revalidated: list[dict[str, Any]]
    queued: list[str]
    skipped: list[dict[str, Any]]


def resume_blocked_actions(
    repo_root: Path,
    *,
    product_id: str | None = None,
    products_dir: Path | None = None,
    auto_queue: bool = True,
) -> ResumeResult:
    """
    For capability pauses whose requests are fulfilled (or otherwise terminal),
    remove the pause and re-check autonomy + auto-approval. Optionally enqueue
    actions that pass policy checks.
    """
    root = repo_root.resolve()
    st = load_state(root)
    pauses = st.get("capability_pauses")
    if not isinstance(pauses, list) or not pauses:
        sync_blocked_actions_artifact(root)
        return ResumeResult(cleared_pauses=[], revalidated=[], queued=[], skipped=[])

    bundle = _load_planning_bundle(root, products_dir)
    cleared: list[str] = []
    revalidated: list[dict[str, Any]] = []
    queued: list[str] = []
    skipped: list[dict[str, Any]] = []

    for p in list(pauses):
        if not isinstance(p, dict):
            continue
        rid = str(p.get("request_id") or "")
        aid = str(p.get("action_id") or "")
        pid = str(p.get("product_id") or "")
        if product_id and pid != product_id:
            continue
        req = load_request(root, rid) if rid else None
        if req is None or not is_terminal_status(req.status):
            continue
        # Terminal: clear pause for fulfilled/rejected/cancelled
        remove_pause_for_request(root, rid)
        cleared.append(rid)
        if req.status != CapabilityRequestStatus.FULFILLED:
            skipped.append(
                {
                    "action_id": aid,
                    "product_id": pid,
                    "reason": f"request terminal with status {req.status.value} (not re-queued)",
                }
            )
            continue

        contract = _contract_for_action(bundle, aid)
        if contract is None:
            skipped.append(
                {
                    "action_id": aid,
                    "product_id": pid,
                    "reason": "action not found in current planning actions bundle",
                }
            )
            continue

        chk = check_autonomy_execution(root, contract)
        appr = evaluate_auto_approval(contract, repo_root=root)
        revalidated.append(
            {
                "action_id": aid,
                "product_id": pid,
                "autonomy_allowed": chk.allowed,
                "autonomy_reasons": chk.reasons,
                "auto_approve": appr.auto_approve,
                "approval_reasons": appr.reasons[:8],
            }
        )
        if auto_queue and chk.allowed:
            _append_executable(root, action_id=aid, product_id=pid, source="capability_resume")
            queued.append(aid)
        elif auto_queue:
            skipped.append(
                {
                    "action_id": aid,
                    "product_id": pid,
                    "reason": "autonomy policy still blocks this action after capability fulfillment",
                }
            )

    # If we cleared anything, state was updated by remove_pause; ensure consistency
    sync_blocked_actions_artifact(root)
    return ResumeResult(
        cleared_pauses=cleared,
        revalidated=revalidated,
        queued=queued,
        skipped=skipped,
    )


def append_resume_event(repo_root: Path, payload: dict[str, Any]) -> None:
    path = repo_root.resolve() / "runs" / "autonomy" / "resume_events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    line = dumps_json({**payload, "timestamp_utc": datetime.now(timezone.utc).isoformat()}, indent=None)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
