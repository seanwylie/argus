"""Deterministic idea selection for orchestration ``refinement_start_idea`` (explicit policy; no hidden picks)."""

from __future__ import annotations

from pathlib import Path

from argus.idea_generation.pipeline import load_latest_bundle
from argus.idea_generation.score import rank_key
from argus.refinement.models import ArtifactType, SessionStatus
from argus.refinement.persistence import list_session_entries

# Documented in orchestration execution_detail when no explicit idea_id is passed.
ORCHESTRATION_IDEA_SELECTION_RULE = "rank_key_desc_then_idea_id_asc"

_TERMINAL_SESSION_STATUSES = frozenset(
    {
        SessionStatus.APPROVED.value,
        SessionStatus.APPROVED_WITH_RISKS.value,
        SessionStatus.REJECTED.value,
    }
)


def deterministic_idea_id_for_refinement_orchestration(repo_root: Path, product_id: str) -> str | None:
    """
    Pick exactly one ``idea_id`` when orchestration does not supply explicit metadata:

    - Require ``runs/ideas/latest.json`` with ``product_id`` matching ``product_id``.
    - Include ideas whose ``product_id`` is ``None`` or matches this product.
    - Sort by ``rank_key`` descending, then ``idea_id`` ascending (stable tie-break).
    - Return the first ``idea_id``, or ``None`` if no candidate.
    """
    bundle = load_latest_bundle(repo_root)
    if bundle is None:
        return None
    if str(bundle.product_id or "").strip() != product_id:
        return None
    candidates: list = []
    for i in bundle.ideas:
        ip = i.product_id
        if ip is not None and str(ip) != product_id:
            continue
        candidates.append(i)
    if not candidates:
        return None
    candidates.sort(key=lambda x: (-rank_key(x), x.idea_id))
    return candidates[0].idea_id


def has_non_terminal_idea_refinement_for_source(
    repo_root: Path, product_id: str, source_id: str
) -> bool:
    """True when an idea refinement session exists for this product+source and is not in a terminal status."""
    for row in list_session_entries(repo_root):
        if str(row.get("product_id") or "").strip() != product_id:
            continue
        if str(row.get("artifact_type") or "").strip() != ArtifactType.IDEA.value:
            continue
        if str(row.get("source_id") or "").strip() != source_id:
            continue
        st = str(row.get("status") or "").strip()
        if st in _TERMINAL_SESSION_STATUSES:
            continue
        return True
    return False
