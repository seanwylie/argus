"""Bounded iterative orchestration: evaluate → advance → persist → re-evaluate (single-process, deterministic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.orchestrator.advancement import advance_orchestration
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ACTION_STATUS_SKIPPED,
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_COMPLETE,
    ORCH_STATUS_ESCALATED,
    ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA,
    ORCHESTRATION_PROGRESSION_RUN_SCHEMA,
)
from argus.orchestrator.state_pass import (
    orchestration_advancement_path,
    write_orchestration_progression_run,
)


def _tuple_to_jsonable(obj: Any) -> Any:
    if isinstance(obj, tuple):
        return [_tuple_to_jsonable(x) for x in obj]
    return obj


def orchestration_state_fingerprint_jsonable(state: dict[str, Any]) -> Any:
    """JSON-serializable form of :func:`_state_progress_fingerprint`."""
    return _tuple_to_jsonable(_state_progress_fingerprint(state))


def _state_progress_fingerprint(state: dict[str, Any]) -> tuple[Any, ...]:
    """Semantic snapshot for convergence detection (no wall-clock fields)."""
    st = str(state.get("orchestration_status") or "")
    na = str(state.get("next_action") or "none")
    elig = state.get("eligible_actions") or []
    rows: list[tuple[str, tuple[str, ...]]] = []
    if isinstance(elig, list):
        for row in elig:
            if not isinstance(row, dict):
                continue
            aid = str(row.get("action_id") or "")
            rc = row.get("reason_codes") or []
            codes = tuple(sorted(str(x) for x in rc)) if isinstance(rc, list) else ()
            rows.append((aid, codes))
    return (st, na, tuple(rows))


def _should_stop_before_advance(state: dict[str, Any]) -> tuple[bool, str]:
    st = str(state.get("orchestration_status") or "")
    na = str(state.get("next_action") or "none")
    if st == ORCH_STATUS_BLOCKED_WAITING_INPUT:
        # In-process ``refinement_submit_reviews_in`` supplies durable reviews_in — advance may clear the wait.
        if na == ACTION_REFINEMENT_SUBMIT_REVIEWS_IN:
            return False, ""
        return True, "blocked_waiting_input"
    if st == ORCH_STATUS_BLOCKED_WAITING_APPROVAL:
        return True, "blocked_waiting_approval"
    if st == ORCH_STATUS_ESCALATED:
        return True, "escalated"
    if st == ORCH_STATUS_COMPLETE:
        return True, "complete"
    elig = state.get("eligible_actions") or []
    if not isinstance(elig, list) or len(elig) == 0:
        return True, "no_eligible_actions"
    if str(state.get("next_action") or "none") == "none":
        return True, "next_action_none"
    return False, ""


@dataclass
class ProgressionRunResult:
    product_id: str
    max_steps_requested: int
    steps_executed: int
    stopped_reason: str
    advancement_steps: list[dict[str, Any]] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)
    actions_taken: list[dict[str, Any]] = field(default_factory=list)
    terminal_status: str = ""
    terminal_reason: str = ""
    artifact_paths: list[str] = field(default_factory=list)

    def to_jsonable(self) -> dict[str, Any]:
        ts = self.terminal_status or str(self.final_state.get("orchestration_status") or "")
        tr = self.terminal_reason or self.stopped_reason
        out: dict[str, Any] = {
            "schema": ORCHESTRATION_PROGRESSION_RUN_SCHEMA,
            "product_id": self.product_id,
            "max_steps_requested": self.max_steps_requested,
            "steps_executed": self.steps_executed,
            "step_count": self.steps_executed,
            "stopped_reason": self.stopped_reason,
            "actions_taken": self.actions_taken,
            "terminal_status": ts,
            "terminal_reason": tr,
            "advancement_steps": self.advancement_steps,
            "final_state": self.final_state,
        }
        if self.artifact_paths:
            out["artifact_paths"] = self.artifact_paths
        return out


def build_progression_run_artifact_payload(
    result: ProgressionRunResult,
    *,
    started_at_utc: str,
    completed_at_utc: str,
) -> dict[str, Any]:
    fs = result.final_state
    ts = result.terminal_status or str(fs.get("orchestration_status") or "")
    tr = result.terminal_reason or result.stopped_reason
    return {
        "schema": ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA,
        "product_id": result.product_id,
        "started_at_utc": started_at_utc,
        "completed_at_utc": completed_at_utc,
        "max_steps_requested": result.max_steps_requested,
        "steps_executed": result.steps_executed,
        "step_count": result.steps_executed,
        "stopped_reason": result.stopped_reason,
        "actions_taken": result.actions_taken,
        "terminal_status": ts,
        "terminal_reason": tr,
        "orchestration_fingerprint": orchestration_state_fingerprint_jsonable(fs),
        "final_state_summary": {
            "schema": fs.get("schema"),
            "orchestration_status": fs.get("orchestration_status"),
            "next_action": fs.get("next_action"),
            "evaluated_at_utc": fs.get("evaluated_at_utc"),
        },
        "advancement_steps": result.advancement_steps,
        "final_state": fs,
    }


def _attach_progression_artifact(
    repo_root: Path,
    result: ProgressionRunResult,
    started_at_utc: str,
    write_artifact: bool,
) -> ProgressionRunResult:
    if not write_artifact:
        return result
    completed_at_utc = datetime.now(timezone.utc).isoformat()
    payload = build_progression_run_artifact_payload(
        result,
        started_at_utc=started_at_utc,
        completed_at_utc=completed_at_utc,
    )
    latest, gen = write_orchestration_progression_run(repo_root, payload)
    root = repo_root.resolve()
    result.artifact_paths = [str(latest.relative_to(root)), str(gen.relative_to(root))]
    return result


def run_orchestration_progression(
    repo_root: Path | str,
    product_id: str,
    *,
    max_steps: int = 8,
    refresh_state: bool = True,
    execute: bool = True,
    write_artifact: bool = True,
) -> ProgressionRunResult:
    """
    Iteratively: evaluate → (stop if terminal) → advance → persist → re-evaluate until
    stopping conditions or ``max_steps`` advances.

    When ``execute`` is True, each advance runs the in-process step executor for queued
    actions (same as ``advance --execute``). When False, only advancement intent is recorded.

    Each step overwrites ``runs/orchestration/latest/advancements/<product_id>.json``.
    When ``write_artifact`` is True (default), also writes ``argus.orchestration_progression_run_artifact.v1``
    under ``runs/orchestration/latest/progression_runs/<product_id>.json`` and
    ``runs/orchestration/progression_runs/generations/<run_id>.json``.
    """
    started_at_utc = datetime.now(timezone.utc).isoformat()
    root = Path(repo_root).resolve()
    pid = str(product_id).strip()
    if not pid:
        raise ValueError("product_id required")
    if max_steps < 0:
        raise ValueError("max_steps must be >= 0")

    steps: list[dict[str, Any]] = []
    taken: list[dict[str, Any]] = []
    stopped: str = ""
    final: dict[str, Any] = {}

    if max_steps == 0:
        final = evaluate_product_orchestration(root, pid)
        ts = str(final.get("orchestration_status") or "")
        result = ProgressionRunResult(
            product_id=pid,
            max_steps_requested=0,
            steps_executed=0,
            stopped_reason="max_steps_zero",
            advancement_steps=[],
            final_state=final,
            actions_taken=[],
            terminal_status=ts,
            terminal_reason="max_steps_zero",
        )
        return _attach_progression_artifact(root, result, started_at_utc, write_artifact)

    for _ in range(max_steps):
        state = evaluate_product_orchestration(root, pid)
        stop, reason = _should_stop_before_advance(state)
        if stop:
            stopped = reason
            final = state
            break

        fp_before = _state_progress_fingerprint(state)
        adv_path, payload = advance_orchestration(
            root, pid, refresh_state=refresh_state, execute=execute
        )
        step_idx = len(steps)
        rel_adv = str(adv_path.relative_to(root))
        steps.append(
            {
                "step_index": step_idx,
                "advancement_path_repo_relative": rel_adv,
                "advancement": payload,
            }
        )

        status = str(payload.get("action_status") or "")
        taken.append(
            {
                "step_index": step_idx,
                "selected_action": payload.get("selected_action"),
                "action_status": status,
                "advancement_path_repo_relative": rel_adv,
            }
        )

        if status == ACTION_STATUS_SKIPPED:
            stopped = "advance_skipped"
            final = evaluate_product_orchestration(root, pid)
            break
        if status == ACTION_STATUS_BLOCKED:
            stopped = "advance_blocked"
            final = evaluate_product_orchestration(root, pid)
            break

        if execute:
            if status == ACTION_STATUS_QUEUED_UNHANDLED:
                stopped = "queued_unhandled"
                final = evaluate_product_orchestration(root, pid)
                break
            if status == ACTION_STATUS_FAILED:
                stopped = "execution_failed"
                final = evaluate_product_orchestration(root, pid)
                break
            if status == ACTION_STATUS_EXECUTED:
                final = evaluate_product_orchestration(root, pid)
                fp_after = _state_progress_fingerprint(final)
                if fp_after == fp_before:
                    stopped = "state_unchanged_after_advance"
                    break
                continue
            stopped = f"unexpected_action_status:{status}"
            final = evaluate_product_orchestration(root, pid)
            break

        if status != ACTION_STATUS_QUEUED:
            stopped = f"unexpected_action_status:{status}"
            final = evaluate_product_orchestration(root, pid)
            break

        final = evaluate_product_orchestration(root, pid)
        fp_after = _state_progress_fingerprint(final)
        if fp_after == fp_before:
            stopped = "state_unchanged_after_advance"
            break
    else:
        stopped = "max_steps_reached"
        final = evaluate_product_orchestration(root, pid)

    ts = str(final.get("orchestration_status") or "")
    result = ProgressionRunResult(
        product_id=pid,
        max_steps_requested=max_steps,
        steps_executed=len(steps),
        stopped_reason=stopped,
        advancement_steps=steps,
        final_state=final,
        actions_taken=taken,
        terminal_status=ts,
        terminal_reason=stopped,
    )
    return _attach_progression_artifact(root, result, started_at_utc, write_artifact)


def progression_summary_lines(result: ProgressionRunResult, *, repo_root: Path) -> list[str]:
    """Human-readable lines for CLI (non-JSON)."""
    root = repo_root.resolve()
    lines = [
        f"product_id={result.product_id}",
        f"steps_executed={result.steps_executed}",
        f"step_count={result.steps_executed}",
        f"max_steps_requested={result.max_steps_requested}",
        f"stopped_reason={result.stopped_reason}",
        f"terminal_status={result.terminal_status or result.final_state.get('orchestration_status')}",
        f"terminal_reason={result.terminal_reason or result.stopped_reason}",
    ]
    for row in result.actions_taken:
        lines.append(
            f"  action: step={row.get('step_index')} "
            f"action_status={row.get('action_status')} "
            f"selected_action={row.get('selected_action')}"
        )
    for step in result.advancement_steps:
        ap = step.get("advancement_path_repo_relative") or ""
        adv = step.get("advancement") or {}
        sa = adv.get("selected_action")
        st = adv.get("action_status")
        lines.append(f"  step {step.get('step_index')}: {ap} — action_status={st} selected_action={sa}")
    fs = result.final_state
    lines.append(
        f"final_orchestration_status={fs.get('orchestration_status')} "
        f"next_action={fs.get('next_action')}"
    )
    rel = orchestration_advancement_path(root, result.product_id).relative_to(root)
    lines.append(f"latest_advancement={rel}")
    for ap in result.artifact_paths:
        lines.append(f"progression_artifact={ap}")
    return lines
