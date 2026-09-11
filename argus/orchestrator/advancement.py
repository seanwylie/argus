"""Deterministic orchestration advancement: record chosen next step; optional in-process execution."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.state_models import (
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_STATUS_BLOCKED,
    ACTION_STATUS_QUEUED,
    ACTION_STATUS_SKIPPED,
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCHESTRATION_ADVANCEMENT_SCHEMA,
    ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
)
from argus.orchestrator.state_pass import (
    attach_batch_advancement_links_to_index_payload,
    emit_orchestration_batch,
    orchestration_advancement_path,
    orchestration_batch_advancement_path,
    write_operator_summary_latest_batch,
    write_orchestration_state,
)


def _apply_execution_to_payload(
    repo_root: Path,
    product_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    from argus.orchestrator.step_executor import execute_orchestration_action

    aid = str(payload.get("selected_action") or "").strip()
    if not aid:
        return payload
    ts = datetime.now(timezone.utc).isoformat()
    result = execute_orchestration_action(repo_root, product_id, aid)
    out = dict(payload)
    out["executed_at_utc"] = ts
    detail = result.get("execution_detail")
    out["execution_detail"] = detail if isinstance(detail, dict) else {}
    err = result.get("execution_error")
    if err:
        out["execution_error"] = str(err)
    else:
        out.pop("execution_error", None)
    out["action_status"] = result["action_status"]
    return out


def orchestration_advancement_payload(
    state: dict[str, Any],
    *,
    selected_at_utc: str,
) -> dict[str, Any]:
    """
    Build a durable advancement record from an evaluated orchestration state dict.

    For non-waiting states, selects the row matching ``next_action`` when present in
    ``eligible_actions`` (same action as the state's prioritized ``next_action``); otherwise
    falls back to the first eligible row. Does not run subprocesses or external tools.
    """
    pid = str(state.get("product_id", ""))
    orch = str(state.get("orchestration_status", ""))
    eligible = state.get("eligible_actions") or []
    if not isinstance(eligible, list):
        eligible = []
    facts: dict[str, Any] = dict(state.get("eligibility_facts") or {})
    next_a = str(state.get("next_action") or "none")

    base: dict[str, Any] = {
        "schema": ORCHESTRATION_ADVANCEMENT_SCHEMA,
        "product_id": pid,
        "selected_at_utc": selected_at_utc,
        "source_eligibility_facts": facts,
        "snapshot_orchestration_status": orch,
        "snapshot_next_action": next_a,
    }

    if orch in (ORCH_STATUS_BLOCKED_WAITING_INPUT, ORCH_STATUS_BLOCKED_WAITING_APPROVAL):
        if not (
            orch == ORCH_STATUS_BLOCKED_WAITING_INPUT
            and next_a == ACTION_REFINEMENT_SUBMIT_REVIEWS_IN
        ):
            sa: str | None = None
            if next_a != "none":
                sa = next_a
            elif eligible and isinstance(eligible[0], dict):
                sa = str(eligible[0].get("action_id") or "") or None
            reason = (
                "orchestration_blocked_waiting_approval; resolve pending approval record(s) before execution"
                if orch == ORCH_STATUS_BLOCKED_WAITING_APPROVAL
                else (
                    "orchestration_blocked_waiting_input; refinement or operator input required "
                    "before this pass can queue executable work"
                )
            )
            base.update(
                {
                    "selected_action": sa,
                    "transition_reason": reason,
                    "action_status": ACTION_STATUS_BLOCKED,
                    "eligible_action_index": None,
                }
            )
            return base

    if not eligible:
        base.update(
            {
                "selected_action": None,
                "transition_reason": "no_eligible_actions; nothing to queue in this advance pass",
                "action_status": ACTION_STATUS_SKIPPED,
                "eligible_action_index": None,
            }
        )
        return base

    selected_idx = 0
    if next_a != "none":
        for i, row in enumerate(eligible):
            if isinstance(row, dict) and str(row.get("action_id") or "") == next_a:
                selected_idx = i
                break
    first = eligible[selected_idx]
    action_id = str(first.get("action_id", "")) if isinstance(first, dict) else ""
    reason = str(first.get("reason", "")) if isinstance(first, dict) else ""
    base.update(
        {
            "selected_action": action_id,
            "transition_reason": f"selected_eligible_action: {action_id} — {reason}",
            "action_status": ACTION_STATUS_QUEUED,
            "eligible_action_index": selected_idx,
        }
    )
    return base


def advance_orchestration(
    repo_root: Path | str,
    product_id: str,
    *,
    refresh_state: bool = True,
    execute: bool = False,
) -> tuple[Path, dict[str, Any]]:
    """
    Evaluate current state, compute advancement payload, write ``advancements/<id>.json``,
    optionally refresh ``latest/<id>.json``. Returns ``(advancement_path, payload)``.

    When ``execute`` is True and the payload ``action_status`` is ``queued``, runs the
    in-process step executor for ``selected_action`` and merges outcome fields
    (``action_status``, ``executed_at_utc``, ``execution_detail``, ``execution_error``).
    """
    root = Path(repo_root).resolve()
    state = evaluate_product_orchestration(root, product_id)
    ts = datetime.now(timezone.utc).isoformat()
    payload = orchestration_advancement_payload(state, selected_at_utc=ts)
    if execute and payload.get("action_status") == ACTION_STATUS_QUEUED:
        payload = _apply_execution_to_payload(root, product_id, payload)

    path = orchestration_advancement_path(root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")

    if refresh_state:
        write_orchestration_state(root, product_id)

    return path, payload


FAIRNESS_RULE_BATCH_ADVANCEMENT_V1 = "argus.batch_advancement_fairness.v1"

# Short rolling window of prior batch selections (persisted on ``batch_advancement.json``).
FAIRNESS_RECENT_HISTORY_MAX = 5
# If priority ``top`` appears at least this many times in that window, prefer the first
# actionable lower-ranked product (breaks A,B,A,B,A-style dominance without changing priority order).
FAIRNESS_DOMINANCE_COUNT_THRESHOLD = 3


def _load_last_batch_selected_product_id(repo_root: Path) -> str | None:
    """Previous ``batch_advancement.json`` selection (if any), for anti-starvation rotation."""
    p = orchestration_batch_advancement_path(repo_root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    sid = raw.get("selected_product_id")
    if sid is None:
        return None
    s = str(sid).strip()
    return s or None


def _load_recent_selected_product_ids(repo_root: Path) -> list[str]:
    """Rolling history from prior ``batch_advancement.json`` (oldest first, max :data:`FAIRNESS_RECENT_HISTORY_MAX`)."""
    p = orchestration_batch_advancement_path(repo_root)
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return []
    if not isinstance(raw, dict):
        return []
    h = raw.get("recent_selected_product_ids")
    if not isinstance(h, list):
        return []
    out: list[str] = []
    for x in h:
        s = str(x).strip()
        if s:
            out.append(s)
    return out[-FAIRNESS_RECENT_HISTORY_MAX:]


def _fairness_select_batch_product(
    ranked: list[str],
    prior: dict[str, Any],
    last_selected: str | None,
    recent_history: list[str],
) -> tuple[str, dict[str, Any]]:
    """
    Priority-first selection with two narrow fairness modifiers (rank order unchanged):

    1. If the priority ``top`` appears at least ``FAIRNESS_DOMINANCE_COUNT_THRESHOLD`` times in
       ``recent_history`` and another ranked product is actionable, select the first such
       alternative (deterministic).
    2. Else if ``top`` matches the last batch selection and another ranked product is actionable,
       select the first such alternative (repeat-top rotation).

    Otherwise keep priority-first selection.
    """
    entries: dict[str, dict[str, Any]] = {}
    for e in prior.get("entries") or []:
        if isinstance(e, dict):
            pid = str(e.get("product_id") or "").strip()
            if pid:
                entries[pid] = e

    def is_actionable(pid: str) -> bool:
        ent = entries.get(pid, {})
        pl = ent.get("priority_labels") if isinstance(ent, dict) else None
        if not isinstance(pl, dict):
            return False
        return bool(pl.get("has_eligible_actions"))

    hist = list(recent_history)[-FAIRNESS_RECENT_HISTORY_MAX:]
    fairness: dict[str, Any] = {
        "rule": FAIRNESS_RULE_BATCH_ADVANCEMENT_V1,
        "last_batch_selected_product_id": last_selected,
        "skipped_repeat_top_for_fairness": False,
        "skipped_top_due_to_recent_dominance": False,
        "recent_history_considered": hist,
        "fairness_history_window_max": FAIRNESS_RECENT_HISTORY_MAX,
        "fairness_dominance_count_threshold": FAIRNESS_DOMINANCE_COUNT_THRESHOLD,
        "top_occurrences_in_recent_history": 0,
        "selection_reason": "priority_rank_first",
    }

    if not ranked:
        fairness["selection_reason"] = "no_candidates"
        return "", fairness

    top = ranked[0]
    top_count = sum(1 for x in hist if x == top)
    fairness["top_occurrences_in_recent_history"] = top_count

    # 1) Short-history dominance: top over-represented in the rolling window.
    if (
        len(hist) >= FAIRNESS_DOMINANCE_COUNT_THRESHOLD - 1
        and top_count >= FAIRNESS_DOMINANCE_COUNT_THRESHOLD
    ):
        for j in range(1, len(ranked)):
            pid = ranked[j]
            if is_actionable(pid):
                fairness["skipped_top_due_to_recent_dominance"] = True
                fairness["selection_reason"] = "fairness_skip_dominant_in_recent_history"
                fairness["would_have_selected_without_fairness"] = top
                return pid, fairness

    # 2) Repeat-top rotation (same as before dominance memory).
    if not last_selected or top != last_selected:
        fairness["selection_reason"] = "priority_rank_first"
        return top, fairness

    for j in range(1, len(ranked)):
        pid = ranked[j]
        if is_actionable(pid):
            fairness["skipped_repeat_top_for_fairness"] = True
            fairness["selection_reason"] = "fairness_rotate_after_repeat_top"
            fairness["would_have_selected_without_fairness"] = top
            return pid, fairness

    fairness["selection_reason"] = "priority_rank_first_no_other_actionable"
    return top, fairness


def run_orchestration_batch_advance(
    repo_root: Path | str,
    product_ids: list[str],
    *,
    execute: bool = True,
    write_state_and_index: bool = True,
) -> tuple[Path, dict[str, Any]]:
    """
    Evaluate and prioritize ``product_ids``, then run :func:`advance_orchestration` for one
    product (one bounded step). Selection is priority-first with narrow fairness: a short rolling
    ``recent_selected_product_ids`` can skip an over-represented top rank, and repeat-top
    rotation still applies when the top matches only the last batch (see ``batch_advancement_fairness``).

    Writes ``runs/orchestration/latest/batch_advancement.json`` with selection and full
    advancement payload. When ``write_state_and_index`` is True, also writes per-product
    state and ``index.json`` via :func:`emit_orchestration_batch`.
    """
    root = Path(repo_root).resolve()
    pids = sorted({str(x).strip() for x in product_ids if str(x).strip()})
    if not pids:
        raise ValueError("product_ids required")

    index_payload, idx_path = emit_orchestration_batch(root, pids, write=write_state_and_index)
    prior = index_payload.get("cross_product_prioritization") if isinstance(index_payload, dict) else {}
    if not isinstance(prior, dict):
        prior = {}
    ranked = prior.get("ranked_product_ids") or []
    out = orchestration_batch_advancement_path(root)
    out.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).isoformat()

    if not ranked:
        prev_hist = _load_recent_selected_product_ids(root)
        body: dict[str, Any] = {
            "schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
            "evaluated_at_utc": ts,
            "product_ids_considered": pids,
            "selected_product_id": None,
            "reason": "no_ranked_products",
            "cross_product_prioritization": prior,
            "recent_selected_product_ids": prev_hist,
        }
        out.write_text(dumps_json(body) + "\n", encoding="utf-8")
        if write_state_and_index and idx_path is not None:
            enriched = attach_batch_advancement_links_to_index_payload(root, dict(index_payload))
            idx_path.write_text(dumps_json(enriched) + "\n", encoding="utf-8")
        write_operator_summary_latest_batch(root, body)
        return out, body

    last_selected = _load_last_batch_selected_product_id(root)
    recent_history = _load_recent_selected_product_ids(root)
    selected, fairness = _fairness_select_batch_product(
        [str(x) for x in ranked], prior, last_selected, recent_history
    )
    adv_path, adv_payload = advance_orchestration(
        root,
        selected,
        refresh_state=True,
        execute=execute,
    )
    try:
        rel_adv = str(adv_path.relative_to(root))
    except ValueError:
        rel_adv = str(adv_path)
    new_hist = (recent_history + [selected])[-FAIRNESS_RECENT_HISTORY_MAX:]
    body = {
        "schema": ORCHESTRATION_BATCH_ADVANCEMENT_SCHEMA,
        "evaluated_at_utc": ts,
        "product_ids_considered": pids,
        "selected_product_id": selected,
        "cross_product_prioritization": prior,
        "batch_advancement_fairness": fairness,
        "recent_selected_product_ids": new_hist,
        "advancement_artifact_path_repo_relative": rel_adv,
        "advancement_payload": adv_payload,
    }
    out.write_text(dumps_json(body) + "\n", encoding="utf-8")
    if write_state_and_index and idx_path is not None:
        enriched = attach_batch_advancement_links_to_index_payload(root, dict(index_payload))
        idx_path.write_text(dumps_json(enriched) + "\n", encoding="utf-8")
    write_operator_summary_latest_batch(root, body)
    return out, body
