"""Emit capability requests from execution, experiments, and advisors."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.advisors.llm import llm_config_from_env
from argus.capabilities.requests.models import (
    CapabilityRequest,
    CapabilityRequestSource,
    CapabilityRequestStatus,
)
from argus.capabilities.requests.store import create_request, list_requests
from argus.experiments.models import EvaluationVerdict, ExperimentEvaluation

AUTONOMY_POLICY_HINT = "autonomy.policy"


def record_autonomy_policy_block(
    repo_root: Path,
    reasons: list[str],
    *,
    action_path: str | None = None,
    action_id: str | None = None,
    product_id: str | None = None,
) -> CapabilityRequest:
    """Record when autonomy policy blocks execution (budget, mode, action types, etc.)."""
    pid = (product_id or "").strip()
    aid = (action_id or "").strip()
    for r in list_requests(repo_root):
        if r.capability_hint != AUTONOMY_POLICY_HINT:
            continue
        if (r.product_id or "") != pid:
            continue
        ref = r.source_ref or {}
        if str(ref.get("action_id") or "") != aid:
            continue
        if r.status in (CapabilityRequestStatus.OPEN, CapabilityRequestStatus.ACKNOWLEDGED):
            return r
    lines = "\n".join(f"- {x}" for x in reasons)
    ref: dict[str, Any] = {}
    if action_path:
        ref["action_path"] = action_path
    if action_id:
        ref["action_id"] = action_id
    return create_request(
        repo_root,
        title="Autonomy policy blocked execution",
        description=(
            "Argus cannot run this action under the current autonomy mode and policy:\n\n"
            f"{lines}\n\n"
            "Adjust runs/autonomy/autonomy.json, raise limits, or fulfill related operator steps, "
            "then mark this request fulfilled when unblocked."
        ),
        source=CapabilityRequestSource.AUTONOMY,
        capability_hint=AUTONOMY_POLICY_HINT,
        product_id=product_id.strip() or None,
        source_ref=ref,
    )


def record_execution_blocked(
    repo_root: Path,
    reasons: list[str],
    *,
    action_path: str | None = None,
    action_id: str | None = None,
    product_id: str | None = None,
) -> CapabilityRequest:
    """Record when ``argus execution run`` cannot proceed."""
    lines = "\n".join(f"- {r}" for r in reasons)
    ref: dict[str, Any] = {}
    if action_path:
        ref["action_path"] = action_path
    if action_id:
        ref["action_id"] = action_id
    return create_request(
        repo_root,
        title="Execution blocked — policy or validation",
        description=(
            "Argus refused to run this action before subprocess execution:\n\n"
            f"{lines}\n\n"
            "Resolve validation/sandbox/approval issues or adjust the action contract."
        ),
        source=CapabilityRequestSource.EXECUTION,
        capability_hint="execution.policy",
        product_id=product_id,
        source_ref=ref,
    )


def record_advisor_llm_gap(
    repo_root: Path,
    product_id: str,
    *,
    stub_only: bool,
    use_llm: bool | None,
) -> CapabilityRequest | None:
    """
    If LLM-backed advisors are unavailable (no provider config), record an ask.

    Skips when the user explicitly requested ``stub_only`` or ``use_llm=False``.
    """
    if stub_only or use_llm is False:
        return None
    if llm_config_from_env() is not None:
        return None
    pid = product_id.strip()
    for r in list_requests(repo_root):
        if r.capability_hint != "advisors.llm":
            continue
        if (r.product_id or "") != pid:
            continue
        if r.status in (CapabilityRequestStatus.OPEN, CapabilityRequestStatus.ACKNOWLEDGED):
            return None
    return create_request(
        repo_root,
        title="Advisor LLM provider not configured",
        description=(
            "Consultation used deterministic stubs because no OpenAI-compatible API "
            "configuration was found. Set ARGUS_LLM_ENABLED=1 and ARGUS_OPENAI_API_KEY "
            "(and optional ARGUS_OPENAI_BASE_URL / ARGUS_OPENAI_MODEL) to enable LLM "
            "responses for advisor runs."
        ),
        source=CapabilityRequestSource.ADVISOR,
        capability_hint="advisors.llm",
        product_id=product_id.strip() or None,
        source_ref={"product_id": product_id},
    )


def record_experiment_evaluation_gap(
    repo_root: Path,
    ev: ExperimentEvaluation,
) -> CapabilityRequest | None:
    """
    Record when an experiment evaluation is inconclusive or failed — may need
    richer signals or human interpretation.
    """
    if ev.verdict not in (EvaluationVerdict.INCONCLUSIVE, EvaluationVerdict.FAILED):
        return None
    for r in list_requests(repo_root):
        if r.source_ref.get("experiment_id") != ev.experiment_id:
            continue
        if r.status in (CapabilityRequestStatus.OPEN, CapabilityRequestStatus.ACKNOWLEDGED):
            return None
    title = (
        f"Experiment {ev.experiment_id} needs follow-up ({ev.verdict.value})"
    )
    desc = (
        f"Verdict: {ev.verdict.value}\n"
        f"Summary: {ev.summary}\n\n"
        "Consider additional metrics, longer windows, or human review."
    )
    return create_request(
        repo_root,
        title=title,
        description=desc,
        source=CapabilityRequestSource.EXPERIMENT,
        capability_hint="experiments.evaluation",
        product_id=ev.product_id,
        source_ref={
            "experiment_id": ev.experiment_id,
            "verdict": ev.verdict.value,
            "composite_score": ev.composite_score,
        },
        metadata={"reasons": ev.reasons[:20]},
    )
