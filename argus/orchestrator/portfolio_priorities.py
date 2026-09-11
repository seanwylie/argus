"""
Portfolio-level orchestration prioritization (inspectable, deterministic).

Writes ``runs/orchestration/latest/portfolio_priorities.json`` — ranked products for
operator attention — plus timestamped copies under ``runs/orchestration/generations/``
and a derived ``runs/orchestration/latest/portfolio_priority_trends.json`` (see
:mod:`argus.orchestrator.portfolio_priority_trends`). Does not change per-product
eligibility or execution.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.artifact_paths import portfolio_priorities_path
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.freshness_plain_language import (
    freshness_explanation_lines,
    freshness_summary_sentence,
)
from argus.orchestrator.state_models import (
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
    ORCH_STATUS_COMPLETE,
    ORCH_STATUS_ELIGIBLE,
    ORCH_STATUS_ESCALATED,
    ORCH_STATUS_STALE_REFRESH_NEEDED,
)
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA, planning_latest_path
from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA, strategy_latest_path

PORTFOLIO_PRIORITIES_SCHEMA = "argus.portfolio_priorities.v1"

# --- Integer weights (documented; higher → more urgent for operator attention) ---
# Base floor so products without artifacts still sort deterministically.
_W_BASE = 12

# Actionable spine: eligible next_action from orchestration evaluation.
_W_NEXT_ACTION = 78

# Headline orchestration_status (mutually exclusive in practice).
_W_STATUS = {
    ORCH_STATUS_ELIGIBLE: 46,
    ORCH_STATUS_ESCALATED: 132,
    ORCH_STATUS_STALE_REFRESH_NEEDED: 54,
    ORCH_STATUS_BLOCKED_WAITING_INPUT: 20,
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL: 36,
    ORCH_STATUS_COMPLETE: 10,
}

# Strategy posture from ``runs/strategy/latest`` (if present).
_W_POSTURE = {
    "pivot": 54,
    "explore": 36,
    "double_down": 44,
    "stabilize": 15,
}

# Planning mode from ``runs/planning/latest`` (if present); aligns with planning snapshot.
_W_PLANNING_MODE = {
    "redirect": 30,
    "validate": 20,
    "expand": 24,
    "consolidate": 11,
}

# Escalation triggers list non-empty (from orchestration evaluation).
_W_ESCALATION_ANY = 46

# Waiting on input without escalation pressure — deprioritize vs active spine.
_PENALTY_WAITING_NO_ESCALATION = -38


def _load_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _strategy_posture(repo_root: Path, product_id: str) -> tuple[str | None, dict[str, Any] | None]:
    p = strategy_latest_path(repo_root, product_id)
    raw = _load_json_dict(p)
    if raw is None or str(raw.get("schema") or "") != STRATEGY_SNAPSHOT_SCHEMA:
        return None, None
    posture = str(raw.get("posture") or raw.get("posture_raw") or "").strip() or None
    return posture, raw


def _planning_mode_and_blob(
    repo_root: Path, product_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    p = planning_latest_path(repo_root, product_id)
    raw = _load_json_dict(p)
    if raw is None or str(raw.get("schema") or "") != PLANNING_SNAPSHOT_SCHEMA:
        return None, None
    mode = str(raw.get("planning_mode") or "").strip() or None
    return mode, raw


def _score_product(
    *,
    product_id: str,
    state: dict[str, Any],
    strategy_posture: str | None,
    planning_mode: str | None,
) -> tuple[int, list[str], str]:
    """Return (priority_score, priority_reasons, evidence_summary)."""
    reasons: list[str] = []
    score = _W_BASE

    na = str(state.get("next_action") or "").strip().lower()
    if na and na != "none":
        score += _W_NEXT_ACTION
        reasons.append(f"next_action is {na}")

    ost = str(state.get("orchestration_status") or "").strip()
    if ost in _W_STATUS:
        score += _W_STATUS[ost]
        reasons.append(f"orchestration_status is {ost}")
    elif ost:
        reasons.append(f"orchestration_status is {ost}")

    triggers = state.get("escalation_triggers") or []
    n_trig = len(triggers) if isinstance(triggers, list) else 0
    if n_trig > 0:
        score += _W_ESCALATION_ANY
        reasons.append("escalation trigger present")

    if ost == ORCH_STATUS_BLOCKED_WAITING_INPUT and n_trig == 0:
        score += _PENALTY_WAITING_NO_ESCALATION
        reasons.append("product is waiting on external input (no escalation trigger)")

    if strategy_posture:
        pw = _W_POSTURE.get(strategy_posture)
        if pw is not None:
            score += pw
            reasons.append(f"strategy posture is {strategy_posture}")

    if planning_mode:
        mw = _W_PLANNING_MODE.get(planning_mode)
        if mw is not None:
            score += mw
            reasons.append(f"planning mode is {planning_mode}")

    ev_parts = [ost or "unknown_status", na or "none"]
    if strategy_posture:
        ev_parts.append(f"posture={strategy_posture}")
    if planning_mode:
        ev_parts.append(f"planning={planning_mode}")
    evidence_summary = "; ".join(ev_parts)
    ef = state.get("eligibility_facts") if isinstance(state.get("eligibility_facts"), dict) else None
    fs = freshness_summary_sentence(ef, orchestration_status=ost)
    if fs:
        evidence_summary = f"{evidence_summary} | {fs}"

    # Deterministic reason ordering for stable diffs
    reasons_sorted = sorted(set(reasons), key=lambda s: s.lower())
    return score, reasons_sorted, evidence_summary


def build_portfolio_priorities(
    repo_root: Path,
    product_ids: list[str],
    *,
    orchestration_states: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """
    Rank products by integer :func:`_score_product` (higher = attend first).

    If ``orchestration_states`` is provided (e.g. from a batch emit), those dicts are used;
    otherwise :func:`evaluate_product_orchestration` is invoked per id.
    """
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).isoformat()
    pids = sorted(set(str(p).strip() for p in product_ids if str(p).strip()))

    rows: list[dict[str, Any]] = []
    for pid in pids:
        if orchestration_states is not None and pid in orchestration_states:
            state = orchestration_states[pid]
        else:
            state = evaluate_product_orchestration(root, pid)

        spost, _st_raw = _strategy_posture(root, pid)
        pmode, _pl_raw = _planning_mode_and_blob(root, pid)

        pri, reasons, ev_sum = _score_product(
            product_id=pid,
            state=state,
            strategy_posture=spost,
            planning_mode=pmode,
        )
        ef_row = state.get("eligibility_facts") if isinstance(state.get("eligibility_facts"), dict) else None
        ost_row = str(state.get("orchestration_status") or "")
        rows.append(
            {
                "product_id": pid,
                "priority_score": pri,
                "orchestration_status": ost_row,
                "next_action": str(state.get("next_action") or "none"),
                "strategy_posture": spost,
                "planning_mode": pmode,
                "evidence_summary": ev_sum,
                "priority_reasons": reasons,
                "freshness_explanation_lines": freshness_explanation_lines(
                    ef_row,
                    orchestration_status=ost_row or None,
                ),
            }
        )

    # Higher score first; tie-break product_id ascending (stable, inspectable).
    rows.sort(key=lambda r: (-int(r["priority_score"]), str(r["product_id"])))
    for i, row in enumerate(rows):
        row["rank"] = i + 1

    top = rows[0] if rows else None
    out: dict[str, Any] = {
        "schema": PORTFOLIO_PRIORITIES_SCHEMA,
        "schema_version": "1",
        "generated_at_utc": ts,
        "repo_root": str(root),
        "products": rows,
    }
    if top:
        out["recommended_product_id"] = top["product_id"]
        out["recommended_next_action"] = top.get("next_action") or "none"
    else:
        out["recommended_product_id"] = None
        out["recommended_next_action"] = None
    return out


def read_portfolio_priorities_json(repo_root: Path) -> tuple[dict[str, Any] | None, str | None]:
    """
    Read ``runs/orchestration/latest/portfolio_priorities.json`` when present.

    Returns ``(payload, None)`` when the file is readable and declares
    ``schema == argus.portfolio_priorities.v1``. Otherwise ``(None, short_error)`` —
    for missing file: ``(None, None)`` (no error; artifact absent).
    """
    root = repo_root.resolve()
    path = portfolio_priorities_path(root)
    if not path.is_file():
        return None, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as e:
        return None, f"not valid JSON ({e})"
    if not isinstance(raw, dict):
        return None, "expected a JSON object"
    if str(raw.get("schema") or "") != PORTFOLIO_PRIORITIES_SCHEMA:
        return None, f"schema is {raw.get('schema')!r} (expected {PORTFOLIO_PRIORITIES_SCHEMA})"
    return raw, None


def write_portfolio_priorities_payload(repo_root: Path, payload: dict[str, Any]) -> Path:
    """
    Persist portfolio priorities JSON under ``runs/orchestration/latest/``,
    mirror a timestamped copy under ``runs/orchestration/generations/``,
    and refresh ``runs/orchestration/latest/portfolio_priority_trends.json``.
    """
    root = repo_root.resolve()
    path = portfolio_priorities_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = dumps_json(payload) + "\n"
    path.write_text(text, encoding="utf-8")
    # Lazy import avoids circular dependency with portfolio_priority_trends (schema constant).
    from argus.orchestrator.portfolio_priority_trends import (
        write_portfolio_priorities_generation_copy,
        write_portfolio_priority_trends_artifact,
    )

    write_portfolio_priorities_generation_copy(root, payload, text)
    write_portfolio_priority_trends_artifact(root)
    return path


def write_portfolio_priorities(
    repo_root: Path,
    product_ids: list[str],
    *,
    orchestration_states: dict[str, dict[str, Any]] | None = None,
) -> Path:
    """Build and write :func:`build_portfolio_priorities`."""
    payload = build_portfolio_priorities(
        repo_root, product_ids, orchestration_states=orchestration_states
    )
    return write_portfolio_priorities_payload(repo_root, payload)


