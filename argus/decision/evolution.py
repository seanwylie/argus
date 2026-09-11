"""Decision bundle lineage: match candidates across generations and classify changes."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.decision.persistence import latest_product_path


def _norm(s: str) -> str:
    return " ".join(str(s).lower().split())


def _fuzzy_match(prev_row: dict[str, Any], cand: DecisionCandidate) -> bool:
    if str(prev_row.get("action_type") or "") != cand.action_type.value:
        return False
    return _norm(str(prev_row.get("summary") or "")) == _norm(cand.summary)


def _float_close(a: float | None, b: float | None) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=1e-9)


def _core_text_equivalent(prev_row: dict[str, Any], cand: DecisionCandidate) -> bool:
    """Match narrative fields only (used to separate content vs ranking-only drift)."""
    if str(prev_row.get("action_type") or "") != cand.action_type.value:
        return False
    if _norm(str(prev_row.get("summary") or "")) != _norm(cand.summary):
        return False
    if _norm(str(prev_row.get("rationale") or "")) != _norm(cand.rationale):
        return False
    if _norm(str(prev_row.get("expected_impact") or "")) != _norm(cand.expected_impact):
        return False
    return True


def _meaningful_fields_equivalent(prev_row: dict[str, Any], cand: DecisionCandidate) -> bool:
    """
    Equivalence for **unchanged** classification: same decision substance, excluding ``priority_score``.

    ``priority_score`` may drift from scoring-weight changes without implying content evolution.
    """
    if not _core_text_equivalent(prev_row, cand):
        return False
    if not _float_close(
        None if prev_row.get("confidence") is None else float(prev_row["confidence"]),
        cand.confidence,
    ):
        return False
    est = prev_row.get("estimated_cost")
    if est is None and cand.estimated_cost is None:
        return True
    if est is None or cand.estimated_cost is None:
        return False
    return _float_close(float(est), cand.estimated_cost)


def _confidence_delta_cat(prev_row: dict[str, Any], cand: DecisionCandidate) -> str | None:
    pc = prev_row.get("confidence")
    nc = cand.confidence
    if pc is None or nc is None:
        return None
    pcf, ncf = float(pc), float(nc)
    if math.isclose(pcf, ncf, rel_tol=0.0, abs_tol=1e-9):
        return "same"
    return "up" if ncf > pcf else "down"


def _match_new_to_previous(
    prev_rows: list[dict[str, Any]],
    new_cands: list[DecisionCandidate],
) -> tuple[list[int | None], list[dict[str, Any]]]:
    """
    Greedy one-to-one matching: id first, then fuzzy summary+action_type.
    Returns (new_index -> prev_index or None, removed prev rows).
    """
    n_prev = len(prev_rows)
    prev_used = [False] * n_prev
    new_to_prev: list[int | None] = [None] * len(new_cands)

    for j, nc in enumerate(new_cands):
        for i, p in enumerate(prev_rows):
            if prev_used[i]:
                continue
            if str(p.get("id") or "") == nc.id:
                new_to_prev[j] = i
                prev_used[i] = True
                break

    for j, nc in enumerate(new_cands):
        if new_to_prev[j] is not None:
            continue
        for i, p in enumerate(prev_rows):
            if prev_used[i]:
                continue
            if _fuzzy_match(p, nc):
                new_to_prev[j] = i
                prev_used[i] = True
                break

    removed = [prev_rows[i] for i in range(n_prev) if not prev_used[i]]
    return new_to_prev, removed


def build_decision_lineage_payload(
    repo_root: Path,
    product_id: str,
    new_candidates: list[DecisionCandidate],
) -> dict[str, Any]:
    """
    Build optional bundle fields + per-candidate augmentations for one save.

    Keys returned:
    - ``bundle_extras``: top-level keys to merge into decisions bundle (excluding candidates)
    - ``candidate_augmentations``: list[dict] aligned with ``new_candidates``
    """
    root = repo_root.resolve()
    lp = latest_product_path(root, product_id)
    bundle_extras: dict[str, Any] = {}
    candidate_augmentations: list[dict[str, Any]] = []

    if not lp.is_file():
        for _ in new_candidates:
            candidate_augmentations.append(
                {
                    "change_type": "new",
                    "confidence_delta": None,
                }
            )
        bundle_extras["decision_evolution"] = {
            "previous_generated_at_utc": None,
            "new_count": len(new_candidates),
            "unchanged_count": 0,
            "modified_count": 0,
            "ranking_modified_count": 0,
            "removed_count": 0,
        }
        bundle_extras["removed_decisions"] = []
        return {"bundle_extras": bundle_extras, "candidate_augmentations": candidate_augmentations}

    try:
        raw_prev = json.loads(lp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raw_prev = None
    if not isinstance(raw_prev, dict):
        for _ in new_candidates:
            candidate_augmentations.append({"change_type": "new", "confidence_delta": None})
        bundle_extras["decision_evolution"] = {
            "previous_generated_at_utc": None,
            "new_count": len(new_candidates),
            "unchanged_count": 0,
            "modified_count": 0,
            "removed_count": 0,
        }
        bundle_extras["removed_decisions"] = []
        return {"bundle_extras": bundle_extras, "candidate_augmentations": candidate_augmentations}

    prev_gen = str(raw_prev.get("generated_at_utc") or "").strip() or None
    try:
        rel_prev = str(lp.relative_to(root))
    except ValueError:
        rel_prev = str(lp)

    prev_candidates = [c for c in (raw_prev.get("candidates") or []) if isinstance(c, dict)]
    new_to_prev, removed_raw = _match_new_to_previous(prev_candidates, new_candidates)

    new_count = 0
    unchanged_count = 0
    modified_count = 0
    ranking_modified_count = 0

    for j, nc in enumerate(new_candidates):
        pi = new_to_prev[j]
        if pi is None:
            new_count += 1
            candidate_augmentations.append(
                {
                    "change_type": "new",
                    "confidence_delta": None,
                }
            )
            continue
        prev_row = prev_candidates[pi]
        mid = str(prev_row.get("id") or "")
        cdelta = _confidence_delta_cat(prev_row, nc)
        if _meaningful_fields_equivalent(prev_row, nc):
            unchanged_count += 1
            candidate_augmentations.append(
                {
                    "change_type": "unchanged",
                    "matched_previous_decision_id": mid,
                    "confidence_delta": cdelta,
                }
            )
        elif _core_text_equivalent(prev_row, nc):
            ranking_modified_count += 1
            candidate_augmentations.append(
                {
                    "change_type": "modified_ranking",
                    "matched_previous_decision_id": mid,
                    "confidence_delta": cdelta,
                }
            )
        else:
            modified_count += 1
            candidate_augmentations.append(
                {
                    "change_type": "modified_content",
                    "matched_previous_decision_id": mid,
                    "confidence_delta": cdelta,
                }
            )

    removed_decisions = [
        {"decision_id": str(r.get("id") or ""), "reason": "not_present_in_new_generation"}
        for r in removed_raw
        if str(r.get("id") or "")
    ]

    bundle_extras["previous_decisions_generated_at_utc"] = prev_gen
    bundle_extras["previous_decisions_path"] = rel_prev
    bundle_extras["decision_evolution"] = {
        "previous_generated_at_utc": prev_gen,
        "new_count": new_count,
        "unchanged_count": unchanged_count,
        "modified_count": modified_count,
        "ranking_modified_count": ranking_modified_count,
        "removed_count": len(removed_decisions),
    }
    bundle_extras["removed_decisions"] = removed_decisions

    return {"bundle_extras": bundle_extras, "candidate_augmentations": candidate_augmentations}


def change_summary_from_bundle_extras(bundle_extras: dict[str, Any]) -> dict[str, Any]:
    """Subset for orchestration ``execution_detail`` / tests."""
    ev = bundle_extras.get("decision_evolution")
    if not isinstance(ev, dict):
        return {}
    return {
        "new_count": ev.get("new_count"),
        "unchanged_count": ev.get("unchanged_count"),
        "modified_count": ev.get("modified_count"),
        "ranking_modified_count": ev.get("ranking_modified_count"),
        "removed_count": ev.get("removed_count"),
    }
