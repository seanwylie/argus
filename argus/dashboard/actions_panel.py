"""Planning ActionContracts + approval + execution history for the static dashboard."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.actions.models import ActionContract
from argus.approval.models import ApprovalStatus
from argus.approval.store import list_records
from argus.core.serialize import loads_json
from argus.dashboard.diagnostics import DashboardDiagnostics
from argus.planning.plan_actions import build_planning_actions


def _risk_level(contract: ActionContract) -> str:
    """Heuristic risk tier for operator preview (not a substitute for dry-run)."""
    cmd = (contract.command or "").lower()
    if "&&" in cmd or "portfolio refresh" in cmd:
        return "high"
    if any(
        x in cmd
        for x in (
            "lifecycle report",
            "escalation generate",
            "economics analyze",
        )
    ):
        return "high"
    if any(
        x in cmd
        for x in (
            "decisions generate",
            "findings generate",
            "experiments evaluate",
            "experiments create",
        )
    ):
        return "medium"
    return "low"


def _approval_status(repo_root: Path, contract: ActionContract) -> str:
    """How this action sits relative to runs/approval/ records and contract flags."""
    aid = contract.action_id.strip()
    pid = contract.product_id.strip()
    matches = [r for r in list_records(repo_root) if r.action_id == aid and r.product_id == pid]
    if matches:
        last = matches[-1]
        if last.status == ApprovalStatus.APPROVED:
            return "approved"
        if last.status == ApprovalStatus.PENDING:
            return "pending_record"
        if last.status == ApprovalStatus.REJECTED:
            return "rejected"
    if contract.safe_to_auto_execute or not contract.requires_approval:
        return "auto_eligible"
    return "needs_approval"


def _source_group(action_id: str, pri: set[str], exp: set[str], dec: set[str]) -> str:
    if action_id in pri:
        return "priority"
    if action_id in exp:
        return "experiment"
    if action_id in dec:
        return "decision"
    return "unknown"


def _contract_to_row(
    repo_root: Path,
    c: ActionContract,
    *,
    pri_ids: set[str],
    exp_ids: set[str],
    dec_ids: set[str],
) -> dict[str, Any]:
    st = _approval_status(repo_root, c)
    return {
        "action_id": c.action_id,
        "product_id": c.product_id,
        "action_type": c.action_type,
        "command": c.command,
        "expected_outcome": c.expected_outcome,
        "rollback_notes": c.rollback_notes,
        "risk_level": _risk_level(c),
        "approval_status": st,
        "requires_approval": c.requires_approval,
        "safe_to_auto_execute": c.safe_to_auto_execute,
        "source_group": _source_group(c.action_id, pri_ids, exp_ids, dec_ids),
    }


def _load_execution_history(
    repo_root: Path,
    *,
    limit: int = 40,
    diag: DashboardDiagnostics | None = None,
    strict: bool = False,
) -> list[dict[str, Any]]:
    base = repo_root.resolve() / "runs" / "execution"
    if not base.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for d in base.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        rj = d / "run.json"
        if not rj.is_file():
            continue
        try:
            raw = loads_json(rj.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            if diag is not None:
                diag.json_failure(rj, e, strict=strict, label="execution_run_json_invalid")
            continue
        if not isinstance(raw, dict):
            if diag is not None:
                diag.warn("execution_run_json_shape", f"Expected object in {rj}", path=rj)
            continue
        rid = str(raw.get("run_id") or d.name)
        rows.append(
            {
                "run_id": rid,
                "action_id": str(raw.get("action_id") or ""),
                "product_id": str(raw.get("product_id") or ""),
                "command": str(raw.get("command") or ""),
                "status": str(raw.get("status") or ""),
                "exit_code": raw.get("exit_code"),
                "started_at": str(raw.get("started_at") or ""),
                "finished_at": str(raw.get("finished_at") or ""),
                "path_from_dashboard": f"../execution/{rid}/run.json",
                "path_repo": f"runs/execution/{rid}/run.json",
            }
        )
    rows.sort(key=lambda x: x.get("started_at") or "", reverse=True)
    return rows[:limit]


def build_actions_panel(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    diagnostics: DashboardDiagnostics | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """
    Proposed planning actions with risk + approval status, plus execution history.

    Planning actions are regenerated from the same logic as ``argus planning actions``
    so the dashboard stays current without a prior ``runs/planning/actions.json`` write.
    """
    root = repo_root.resolve()
    err: str | None = None
    try:
        bundle = build_planning_actions(root, products_dir=products_dir)
    except Exception as e:  # pragma: no cover - defensive
        err = str(e)
        if diagnostics is not None:
            diagnostics.warn("planning_actions_build_failed", err)
        return {
            "schema": "argus.dashboard_actions.v1",
            "error": err,
            "proposed": [],
            "pending_approvals": [],
            "auto_eligible": [],
            "approved_actions": [],
            "rejected_actions": [],
            "execution_history": _load_execution_history(
                root, diag=diagnostics, strict=strict
            ),
        }

    pri = set(bundle.priority_action_ids)
    exp = set(bundle.experiment_action_ids)
    dec = set(bundle.decision_action_ids)

    proposed: list[dict[str, Any]] = []
    for c in bundle.actions:
        proposed.append(_contract_to_row(root, c, pri_ids=pri, exp_ids=exp, dec_ids=dec))

    pending_approvals: list[dict[str, Any]] = []
    auto_eligible: list[dict[str, Any]] = []
    approved_actions: list[dict[str, Any]] = []
    rejected_actions: list[dict[str, Any]] = []

    for row in proposed:
        st = row["approval_status"]
        if st in ("needs_approval", "pending_record"):
            pending_approvals.append(row)
        elif st == "auto_eligible":
            auto_eligible.append(row)
        elif st == "approved":
            approved_actions.append(row)
        elif st == "rejected":
            rejected_actions.append(row)

    return {
        "schema": "argus.dashboard_actions.v1",
        "planning_bundle_generated_at_utc": bundle.generated_at_utc,
        "weekly_plan_at_utc": bundle.weekly_plan_at_utc,
        "proposed": proposed,
        "pending_approvals": pending_approvals,
        "auto_eligible": auto_eligible,
        "approved_actions": approved_actions,
        "rejected_actions": rejected_actions,
        "execution_history": _load_execution_history(
            root, diag=diagnostics, strict=strict
        ),
        "artifact_links": {
            "planning_actions_repo": "runs/planning/actions.json",
            "planning_actions_from_dashboard": "../planning/actions.json",
            "approval_records_repo": "runs/approval/records/",
            "approval_records_from_dashboard": "../approval/records/",
            "execution_root_repo": "runs/execution/",
            "execution_root_from_dashboard": "../execution/",
        },
    }
