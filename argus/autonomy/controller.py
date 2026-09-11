"""Enforce autonomy policy before any action execution."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.actions.models import ActionContract
from argus.approval.store import has_approved_for_action
from argus.autonomy.models import AutonomyCheckResult, AutonomyMode
from argus.autonomy.operator_policy import effective_policy
from argus.autonomy.tiers import AutonomyTier, tier4_enabled


def autonomy_state_path(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "autonomy" / "state.json"


def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _effective_run_key() -> str:
    rid = os.environ.get("ARGUS_AUTONOMY_RUN_ID", "").strip()
    return rid if rid else "default"


def load_state(repo_root: Path) -> dict[str, Any]:
    p = autonomy_state_path(repo_root)
    if not p.is_file():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save_state(repo_root: Path, state: dict[str, Any]) -> None:
    p = autonomy_state_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _reset_day_if_needed(state: dict[str, Any]) -> None:
    day = _utc_day()
    if state.get("utc_day") != day:
        state["utc_day"] = day
        state["actions_executed_today"] = 0
        state["cost_usd_today"] = 0.0


def sync_capability_pauses(repo_root: Path) -> None:
    """Drop pause rows whose capability request is missing or terminal."""
    from argus.capabilities.requests.models import is_terminal_status
    from argus.capabilities.requests.store import load_request

    root = repo_root.resolve()
    state = load_state(root)
    pauses = state.get("capability_pauses")
    if not isinstance(pauses, list) or not pauses:
        return
    kept: list[dict[str, Any]] = []
    for p in pauses:
        if not isinstance(p, dict):
            continue
        rid = p.get("request_id")
        if not rid:
            continue
        rec = load_request(root, str(rid))
        if rec is None or is_terminal_status(rec.status):
            continue
        kept.append(p)
    if len(kept) != len(pauses):
        state["capability_pauses"] = kept
        save_state(root, state)


def capability_pause_block_reasons(repo_root: Path, contract: ActionContract) -> list[str]:
    """Reasons this action is still paused on an open capability request."""
    from argus.capabilities.requests.models import CapabilityRequestStatus
    from argus.capabilities.requests.store import load_request

    sync_capability_pauses(repo_root)
    root = repo_root.resolve()
    state = load_state(root)
    pauses = state.get("capability_pauses")
    if not isinstance(pauses, list):
        return []
    pid = contract.product_id.strip()
    aid = contract.action_id.strip()
    out: list[str] = []
    for p in pauses:
        if not isinstance(p, dict):
            continue
        if p.get("product_id") != pid or p.get("action_id") != aid:
            continue
        rid = p.get("request_id")
        rec = load_request(root, str(rid)) if rid else None
        if rec and rec.status in (CapabilityRequestStatus.OPEN, CapabilityRequestStatus.ACKNOWLEDGED):
            out.append(
                f"paused pending capability request {rid} — "
                "fulfill or cancel the request to resume autonomous execution for this action",
            )
    return out


def register_pause_for_request(repo_root: Path, request_id: str, contract: ActionContract) -> None:
    """Record that this action pair is gated until the capability request is resolved."""
    root = repo_root.resolve()
    state = load_state(root)
    pauses = state.get("capability_pauses")
    if not isinstance(pauses, list):
        pauses = []
    pid = contract.product_id.strip()
    aid = contract.action_id.strip()
    pauses = [
        p
        for p in pauses
        if not (isinstance(p, dict) and p.get("product_id") == pid and p.get("action_id") == aid)
    ]
    pauses.append(
        {
            "request_id": request_id,
            "product_id": pid,
            "action_id": aid,
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    state["capability_pauses"] = pauses
    save_state(root, state)


def remove_pause_for_request(repo_root: Path, request_id: str) -> None:
    """Clear pause rows when a capability request is fulfilled, rejected, or cancelled."""
    root = repo_root.resolve()
    state = load_state(root)
    pauses = state.get("capability_pauses")
    if not isinstance(pauses, list) or not pauses:
        return
    new_pauses = [
        p for p in pauses if not (isinstance(p, dict) and p.get("request_id") == request_id)
    ]
    if len(new_pauses) != len(pauses):
        state["capability_pauses"] = new_pauses
        save_state(root, state)


def _should_register_capability_pause(
    repo_root: Path,
    contract: ActionContract,
    reasons: list[str],
) -> bool:
    """
    Pause retries only when the block is not fixable by obtaining approval alone.

    Intrinsic safety denials (lifecycle, command heuristics) clear once a matching approval
    exists — we still record a capability request for visibility, but omit the pause row.
    """
    if any(r.startswith("paused pending capability request") for r in reasons):
        return False
    if has_approved_for_action(repo_root, contract.action_id, contract.product_id):
        return True
    joined = " ".join(reasons).lower()
    if "autonomy mode is off" in joined:
        return True
    if "budget" in joined or "max_actions" in joined or "cost cap" in joined:
        return True
    if "forbidden" in joined and "policy" in joined:
        return True
    if "not in allowed_action_types" in joined:
        return True
    if "manual" in joined and "safe_to_auto_execute" in joined:
        return True
    if "requires explicit requires_approval" in joined:
        return True
    return False


def _emit_autonomy_capability_request(
    repo_root: Path,
    contract: ActionContract,
    reasons: list[str],
    *,
    action_path: str | None = None,
) -> None:
    if any(r.startswith("paused pending capability request") for r in reasons):
        return
    try:
        from argus.capabilities.requests.integrations import record_autonomy_policy_block

        rec = record_autonomy_policy_block(
            repo_root,
            reasons,
            action_path=action_path,
            action_id=contract.action_id.strip() or None,
            product_id=contract.product_id.strip() or None,
        )
        if _should_register_capability_pause(repo_root, contract, reasons):
            register_pause_for_request(repo_root, rec.request_id, contract)
    except OSError:
        pass


def check_autonomy_execution(
    repo_root: Path,
    contract: ActionContract,
) -> AutonomyCheckResult:
    """
    Evaluate autonomy mode + policy. Does not mutate state.

    Call :func:`record_autonomy_execution` after a successful subprocess run,
    or :func:`record_autonomy_block` when execution is refused.
    """
    root = repo_root.resolve()
    sync_capability_pauses(root)
    pause_reasons = capability_pause_block_reasons(root, contract)
    if pause_reasons:
        return AutonomyCheckResult(
            allowed=False,
            reasons=pause_reasons,
            escalate_recommended=False,
        )

    mode, policy, tier = effective_policy(root)
    reasons: list[str] = []

    if tier == AutonomyTier.FULL_AUTONOMY and not tier4_enabled():
        reasons.append(
            "autonomy tier 4 (full) is not enabled — set ARGUS_ENABLE_TIER4=1 or lower tier "
            "(see docs/autonomy-rollout.md)"
        )

    if mode == AutonomyMode.OFF:
        return AutonomyCheckResult(
            allowed=False,
            reasons=["autonomy mode is OFF — execution is disabled"],
            escalate_recommended=False,
        )

    min_c = float(getattr(policy, "min_confidence_autonomous", 0.0) or 0.0)
    if min_c > 0.0:
        try:
            from argus.decision_assessment.persistence import load_latest_assessment

            ass = load_latest_assessment(root, contract.product_id)
            if ass is not None and float(ass.confidence_score) < min_c:
                return AutonomyCheckResult(
                    allowed=False,
                    reasons=[
                        f"decision confidence {float(ass.confidence_score):.2f} is below "
                        f"min_confidence_autonomous {min_c:.2f} (policy cap; adjust policy_overrides or assessments)"
                    ],
                    escalate_recommended=False,
                )
        except (OSError, TypeError, ValueError):
            pass

    at = contract.normalized_action_type()
    if not at:
        return AutonomyCheckResult(allowed=False, reasons=["action_type is missing or empty"])

    if policy.forbidden_action_types and at in policy.forbidden_action_types:
        reasons.append(f"action_type {at!r} is forbidden by autonomy policy")

    if policy.allowed_action_types and at not in policy.allowed_action_types:
        reasons.append(
            f"action_type {at!r} is not in allowed_action_types "
            f"({', '.join(policy.allowed_action_types)})"
        )

    if mode == AutonomyMode.MANUAL and contract.safe_to_auto_execute:
        reasons.append(
            "autonomy mode MANUAL disallows safe_to_auto_execute — require explicit operator execution"
        )

    if contract.safe_to_auto_execute:
        if policy.auto_execute_types and at not in policy.auto_execute_types:
            reasons.append(
                f"action_type {at!r} is not permitted for auto-execution under this autonomy policy"
            )
    elif at in policy.require_approval_for and not contract.requires_approval:
        reasons.append(
            f"action_type {at!r} requires explicit requires_approval under autonomy policy"
        )

    state = load_state(root)
    _reset_day_if_needed(state)
    actions = int(state.get("actions_executed_today", 0))
    cost_day = float(state.get("cost_usd_today", 0.0))
    est = float(contract.estimated_cost_usd or 0.0)

    if policy.max_actions_per_run <= 0:
        reasons.append("autonomy policy max_actions_per_run is 0 — no executions permitted")
    elif actions >= policy.max_actions_per_run:
        reasons.append(
            f"autonomy budget exhausted: {actions}/{policy.max_actions_per_run} "
            "executions for the current UTC day (see max_actions_per_run)"
        )

    if cost_day + est > policy.max_cost_per_day:
        reasons.append(
            f"autonomy cost cap would be exceeded: estimated day total "
            f"{cost_day + est:.2f} USD > max_cost_per_day {policy.max_cost_per_day:.2f} USD"
        )

    esc = float(policy.escalation_thresholds.get("block_streak", 999.0))
    streak = int(float(state.get("block_streak", 0)))
    next_streak = streak + 1
    escalate = bool(reasons) and next_streak >= esc

    if reasons:
        return AutonomyCheckResult(
            allowed=False,
            reasons=reasons,
            escalate_recommended=escalate,
        )

    # Without a stored human approval, require intrinsic safety (aligned with auto-approval rules).
    if not has_approved_for_action(root, contract.action_id, contract.product_id):
        from argus.autonomy.safe_execution import evaluate_safe_autonomy

        intrinsic = evaluate_safe_autonomy(contract, repo_root=root)
        if not intrinsic.autonomous:
            return AutonomyCheckResult(
                allowed=False,
                reasons=list(intrinsic.reasons),
                escalate_recommended=False,
            )

        try:
            from argus.decision_assessment.models import EscalationRecommendation
            from argus.decision_assessment.persistence import load_latest_assessment

            ass = load_latest_assessment(root, contract.product_id)
            if (
                ass is not None
                and ass.escalation_recommendation == EscalationRecommendation.ESCALATE_HUMAN
            ):
                return AutonomyCheckResult(
                    allowed=False,
                    reasons=[
                        "decision context recommends human review before autonomous execution "
                        "(see runs/decision_assessment/latest/ or: argus confidence explain "
                        + contract.product_id
                        + ")",
                    ],
                    escalate_recommended=True,
                )
        except OSError:
            pass

    return AutonomyCheckResult(allowed=True, reasons=[], escalate_recommended=False)


def record_autonomy_block(repo_root: Path, *, reason_preview: str | None = None) -> None:
    """Increment block streak after a refused execution attempt."""
    root = repo_root.resolve()
    state = load_state(root)
    _reset_day_if_needed(state)
    state["block_streak"] = int(state.get("block_streak", 0)) + 1
    ev = state.get("recent_guardrail_events")
    if not isinstance(ev, list):
        ev = []
    ev.append(
        {
            "at_utc": datetime.now(timezone.utc).isoformat(),
            "kind": "execution_block",
            "reason": (reason_preview or "")[:500],
        }
    )
    state["recent_guardrail_events"] = ev[-40:]
    save_state(root, state)


def record_autonomy_execution(repo_root: Path, contract: ActionContract) -> None:
    """Increment successful execution counters and reset block streak."""
    root = repo_root.resolve()
    state = load_state(root)
    _reset_day_if_needed(state)
    state["actions_executed_today"] = int(state.get("actions_executed_today", 0)) + 1
    est = float(contract.estimated_cost_usd or 0.0)
    state["cost_usd_today"] = float(state.get("cost_usd_today", 0.0)) + est
    state["block_streak"] = 0
    state["last_run_key"] = _effective_run_key()
    save_state(root, state)


def enforce_autonomy_or_raise(
    repo_root: Path,
    contract: ActionContract,
    *,
    action_path: str | None = None,
) -> None:
    """Raise :class:`~argus.execution.engine.ExecutionBlocked` if autonomy denies execution."""
    res = check_autonomy_execution(repo_root, contract)
    if res.allowed:
        return
    record_autonomy_block(repo_root, reason_preview="; ".join(res.reasons) if res.reasons else None)
    _emit_autonomy_capability_request(repo_root, contract, res.reasons, action_path=action_path)
    from argus.execution.engine import ExecutionBlocked

    raise ExecutionBlocked([f"autonomy: {r}" for r in res.reasons])


def autonomy_denial_message(
    repo_root: Path,
    contract: ActionContract,
    *,
    action_path: str | None = None,
) -> str | None:
    """Return a single stderr line for legacy execute paths, or None if allowed."""
    res = check_autonomy_execution(repo_root, contract)
    if res.allowed:
        return None
    record_autonomy_block(repo_root, reason_preview="; ".join(res.reasons) if res.reasons else None)
    _emit_autonomy_capability_request(repo_root, contract, res.reasons, action_path=action_path)
    return "Autonomy policy blocked execution: " + "; ".join(res.reasons)
