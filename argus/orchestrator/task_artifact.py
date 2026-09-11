"""Durable orchestration task artifact (``runs/orchestration/tasks/latest/``)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.artifact_paths import (
    orchestration_advancement_path,
    orchestration_latest_path,
)
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_CONSIDER,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXECUTION_OUTCOMES_APPLY,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_TEMPORAL_REFRESH,
    ORCHESTRATION_TASK_SCHEMA,
)

# Stable human-facing labels; deterministic map from ``next_action`` / ``action_id``.
_TASK_TYPE_BY_ACTION: dict[str, str] = {
    ACTION_SIGNALS_COLLECT: "refresh_signals",
    ACTION_TEMPORAL_REFRESH: "refresh_temporal_from_signals",
    ACTION_AUDIT_RUN: "refresh_audit",
    ACTION_ORCHESTRATION_STATE_REFRESH: "refresh_orchestration_state",
    ACTION_REFINEMENT_START_PRODUCT_SPEC: "start_product_spec_refinement",
    ACTION_REFINEMENT_START_IDEA: "start_idea_refinement",
    ACTION_IMPLEMENTATION_PLAN_GENERATE: "generate_implementation_plan",
    ACTION_REFINEMENT_RUN: "refinement_run",
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN: "await_review_input",
    ACTION_ESCALATION_CONSIDER: "escalate",
    ACTION_ESCALATION_PACKET_GENERATE: "generate_escalation_packet",
    ACTION_EXECUTION_OUTCOMES_APPLY: "apply_execution_outcomes",
    ACTION_FINDINGS_GENERATE: "generate_findings",
    ACTION_DECISIONS_GENERATE: "generate_decisions",
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: "refresh_decisions_from_surfaced_findings",
    ACTION_IDEAS_GENERATE: "generate_ideas",
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: "refresh_ideas_from_surfaced_findings",
    ACTION_EXPERIMENTS_PROPOSE: "propose_experiments",
    ACTION_EXPERIMENTS_PRIORITIZE: "prioritize_experiments",
    ACTION_EXPERIMENTS_CREATE: "create_experiment",
    ACTION_EXPERIMENTS_ACTIVATE: "activate_experiment",
    ACTION_EXPERIMENTS_EVALUATE: "evaluate_experiments",
    ACTION_EXPERIMENTS_CLOSE_STALE: "close_stale_experiments",
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: "surface_experiment_findings",
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION: "strategy_refresh_from_decision_evolution",
    ACTION_PLANNING_REFRESH_FROM_STRATEGY: "planning_refresh_from_strategy",
}


def orchestration_task_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "orchestration" / "tasks" / "latest" / f"{product_id}.json"


def task_type_for_action(action_id: str) -> str:
    return _TASK_TYPE_BY_ACTION.get(action_id, action_id)


def _advancement_execution_overlay(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    """Subset of ``advancements/<id>.json`` when an execution outcome was recorded."""
    p = orchestration_advancement_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    st = raw.get("action_status")
    if not raw.get("executed_at_utc") and st not in (
        ACTION_STATUS_EXECUTED,
        ACTION_STATUS_FAILED,
        ACTION_STATUS_QUEUED_UNHANDLED,
    ):
        return None
    keys = (
        "action_status",
        "selected_action",
        "executed_at_utc",
        "execution_detail",
        "execution_error",
    )
    block = {k: raw[k] for k in keys if k in raw}
    return block or None


def _artifact_dependency_paths(artifacts: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for key in ("signals", "temporal", "audit", "execution"):
        sub = artifacts.get(key)
        if isinstance(sub, dict):
            p = sub.get("path")
            if p:
                out.append(str(p))
    return sorted(set(out))


def build_orchestration_task_payload(
    repo_root: Path,
    product_id: str,
    state: dict[str, Any],
) -> dict[str, Any] | None:
    na = state.get("next_action")
    if na in (None, "", "none"):
        return None
    if not isinstance(na, str):
        return None

    reason = ""
    reason_codes: list[str] = []
    for row in state.get("eligible_actions") or []:
        if not isinstance(row, dict):
            continue
        if row.get("action_id") == na:
            reason = str(row.get("reason") or "")
            rc = row.get("reason_codes") or []
            if isinstance(rc, list):
                reason_codes = [str(x) for x in rc]
            break

    root = repo_root.resolve()
    artifacts = state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}
    dep_paths = _artifact_dependency_paths(artifacts)
    rel_state = orchestration_latest_path(root, product_id).relative_to(root)

    payload: dict[str, Any] = {
        "schema": ORCHESTRATION_TASK_SCHEMA,
        "artifact_type": "orchestration_task",
        "product_id": product_id,
        "task_type": task_type_for_action(na),
        "action": na,
        "created_at": str(state.get("evaluated_at_utc") or ""),
        "status": "pending",
        "reason_codes": reason_codes,
        "blockers": list(state.get("blockers") or []),
        "source_state_ref": str(rel_state),
        "inputs": {
            "orchestration_status": state.get("orchestration_status"),
            "orchestration_status_reason": state.get("orchestration_status_reason"),
            "overall_status": state.get("overall_status"),
            "reason": reason,
            "eligibility_facts": state.get("eligibility_facts"),
        },
        "dependencies": {
            "artifact_paths": dep_paths,
        },
    }
    step = _advancement_execution_overlay(root, product_id)
    if step:
        payload["step_execution"] = step
    return payload


def sync_orchestration_task(repo_root: Path, product_id: str, state: dict[str, Any]) -> Path | None:
    """Write or remove the task file so it matches ``state`` (``next_action``)."""
    path = orchestration_task_path(repo_root, product_id)
    payload = build_orchestration_task_payload(repo_root, product_id, state)
    if payload is None:
        if path.is_file():
            path.unlink()
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return path
