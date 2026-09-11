"""
Soft planning-aware priority for orchestration ``next_action`` selection.

Only reorders priority among **already eligible** action ids using small integer boosts
in ``score = boost - index`` (higher wins); canonical index still dominates across large gaps.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.orchestrator.state_models import (
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
)
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA, planning_latest_path

# Small integer boosts combined with index as ``boost - index`` (higher wins).
# Keeps canonical order dominant across large index gaps; planning nudges nearby rivals only.
_BOOST = 2
_DEEMPH = 1

_VALIDATE_EXPERIMENT: frozenset[str] = frozenset(
    {
        ACTION_EXPERIMENTS_PROPOSE,
        ACTION_EXPERIMENTS_PRIORITIZE,
        ACTION_EXPERIMENTS_CREATE,
        ACTION_EXPERIMENTS_ACTIVATE,
        ACTION_EXPERIMENTS_EVALUATE,
        ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    }
)

_EXPAND_IMPL: frozenset[str] = frozenset(
    {
        ACTION_IMPLEMENTATION_PLAN_GENERATE,
        ACTION_REFINEMENT_START_IDEA,
        ACTION_REFINEMENT_RUN,
        ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    }
)

_REDIRECT: frozenset[str] = frozenset(
    {
        ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
        ACTION_EXPERIMENTS_SURFACE_FINDINGS,
        ACTION_EXPERIMENTS_PROPOSE,
        ACTION_ESCALATION_PACKET_GENERATE,
    }
)

_CONSOLIDATE_EXPERIMENT: frozenset[str] = frozenset(
    {
        ACTION_EXPERIMENTS_PROPOSE,
        ACTION_EXPERIMENTS_PRIORITIZE,
        ACTION_EXPERIMENTS_CREATE,
        ACTION_EXPERIMENTS_ACTIVATE,
        ACTION_EXPERIMENTS_EVALUATE,
        ACTION_EXPERIMENTS_CLOSE_STALE,
        ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    }
)

_VALID_MODES = frozenset({"validate", "expand", "redirect", "consolidate"})


def load_planning_snapshot_for_priority(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    """
    Load ``runs/planning/latest/<product_id>.json`` if present and valid for soft priority.

    Requires ``schema`` == :data:`PLANNING_SNAPSHOT_SCHEMA`, matching ``product_id``, and
    parseable ``planning_mode``.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    p = planning_latest_path(root, pid)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if str(raw.get("schema") or "") != PLANNING_SNAPSHOT_SCHEMA:
        return None
    if str(raw.get("product_id") or "") != pid:
        return None
    mode = str(raw.get("planning_mode") or "").strip().lower()
    if mode not in _VALID_MODES:
        return None
    return {"planning_mode": mode, "posture": str(raw.get("posture") or "").strip() or None}


def _boost_for_mode(action_id: str, mode: str) -> int:
    if mode == "validate":
        return _BOOST if action_id in _VALIDATE_EXPERIMENT else 0
    if mode == "expand":
        return _BOOST if action_id in _EXPAND_IMPL else 0
    if mode == "redirect":
        return _BOOST if action_id in _REDIRECT else 0
    if mode == "consolidate":
        if action_id in _EXPAND_IMPL:
            return _BOOST
        if action_id in _CONSOLIDATE_EXPERIMENT:
            return -_DEEMPH
        return 0
    return 0


def pick_next_action_soft_planning(
    ordered_action_ids: list[str],
    *,
    repo_root: Path,
    product_id: str,
) -> tuple[str, dict[str, Any]]:
    """
    Pick ``next_action`` from ``ordered_action_ids`` (canonical order, first = default head).

    Returns ``(action_id, eligibility_facts_subset)`` for merging into orchestration state.
    """
    meta: dict[str, Any] = {
        "planning_mode_considered": None,
        "planning_priority_adjustment_applied": False,
        "next_action_planning_note": None,
    }
    if not ordered_action_ids:
        meta["next_action_planning_note"] = "no eligible actions"
        return "none", meta

    snap = load_planning_snapshot_for_priority(repo_root, product_id)
    if snap is None:
        meta["next_action_planning_note"] = "planning snapshot absent or invalid — canonical order"
        return ordered_action_ids[0], meta

    mode = str(snap["planning_mode"])
    meta["planning_mode_considered"] = mode

    canonical_head = ordered_action_ids[0]
    best_id = canonical_head
    best_score = _score_action(canonical_head, 0, mode)

    for idx, aid in enumerate(ordered_action_ids[1:], start=1):
        sc = _score_action(aid, idx, mode)
        if sc > best_score:
            best_score = sc
            best_id = aid

    meta["planning_priority_adjustment_applied"] = best_id != canonical_head
    if best_id != canonical_head:
        meta["next_action_planning_note"] = (
            f"planning soft priority (mode={mode}): selected {best_id!r} over canonical head {canonical_head!r}"
        )
    else:
        meta["next_action_planning_note"] = (
            f"planning mode={mode}: canonical head unchanged (tie-break or no boost advantage)"
        )

    return best_id, meta


def _score_action(action_id: str, index: int, mode: str) -> int:
    """Higher wins; ``boost - index`` so a boost shifts an action at most ~``_BOOST`` slots vs unboosted peers."""
    b = _boost_for_mode(action_id, mode)
    return b - index
