"""
Deterministic grounding metadata and rank factors for synthesis (:class:`IdeaSource.SYNTHESIS`) ideas.

Inspectable JSON on each idea; used only for ranking and operator clarity — no LLMs.

``grounding_kind`` is descriptive: ``finding``, ``signal_cluster``, ``file_context``, ``mixed``, or ``weak``
(portfolio/combinatorial-only rows). ``grounding_strength`` drives rank multipliers.
"""

from __future__ import annotations

from typing import Any

from argus.idea_generation.models import Idea, IdeaSource

# Base multipliers (also applied in :func:`argus.idea_generation.score.rank_key`).
_STRENGTH_HIGH = 1.06
_STRENGTH_MEDIUM = 1.0
_STRENGTH_LOW = 0.42
_STRENGTH_MISSING = 0.52

# Extra nudge when finding id appears with repo path and/or runs/ artifact (observed pipeline evidence).
_FINDING_PLUS_ARTIFACT_BOOST = 1.03

# Signal-family lattice only (no per-record id, no runs/ path in sources) — extra down-rank.
_SIGNAL_TYPE_LATTICE_ONLY_FACTOR = 0.88


def build_grounding(
    *,
    grounding_kind: str,
    grounding_strength: str,
    grounding_sources: list[dict[str, Any]],
) -> dict[str, Any]:
    """Canonical ``idea.grounding`` payload for synthesis rows."""
    return {
        "grounding_kind": grounding_kind,
        "grounding_strength": grounding_strength,
        "grounding_sources": list(grounding_sources),
    }


def _grounding_sources(idea: Idea) -> list[dict[str, Any]]:
    g = getattr(idea, "grounding", None)
    if not isinstance(g, dict):
        return []
    raw = g.get("grounding_sources")
    if not isinstance(raw, list):
        return []
    return [x for x in raw if isinstance(x, dict)]


def _finding_plus_repo_or_artifact(sources: list[dict[str, Any]]) -> bool:
    has_finding = any(str(s.get("type")) == "finding" and s.get("id") for s in sources)
    has_runs = any(
        str(s.get("type")) in ("artifact", "artifact_hint") and "runs/" in str(s.get("path") or "")
        for s in sources
    )
    has_repo = any(str(s.get("type")) == "repo_path" and str(s.get("path") or "").strip() for s in sources)
    return bool(has_finding and (has_runs or has_repo))


def _signal_type_lattice_only(sources: list[dict[str, Any]]) -> bool:
    """Portfolio signal lattice: sole source is ``signal_type_only`` (no bundle row id)."""
    return len(sources) == 1 and str(sources[0].get("type")) == "signal_type_only"


def _has_repo_path_evidence(sources: list[dict[str, Any]]) -> bool:
    return any(str(s.get("type")) == "repo_path" and str(s.get("path") or "").strip() for s in sources)


def is_weak_synthesis_filler(idea: Idea) -> bool:
    """
    True for synthesis rows that are likely quota filler: lattice-only, combinatorial-only,
    missing grounding, or explicitly weak/low strength. Used by mechanical quality gate — not a ban.
    """
    if idea.source != IdeaSource.SYNTHESIS:
        return False
    g = getattr(idea, "grounding", None)
    if not isinstance(g, dict):
        return True
    strength = str(g.get("grounding_strength") or "").lower()
    kind = str(g.get("grounding_kind") or "").lower()
    if strength == "low" or kind == "weak":
        return True
    sources = _grounding_sources(idea)
    if _signal_type_lattice_only(sources):
        return True
    has_concrete = _finding_plus_repo_or_artifact(sources) or any(
        str(s.get("type")) == "signal_record" and s.get("id") for s in sources
    )
    if any(str(s.get("type")) == "combinatorial" for s in sources) and not has_concrete:
        if _has_repo_path_evidence(sources):
            return False
        return True
    return False


def synthesis_grounding_rank_multiplier(idea: Idea) -> float:
    """
    Down-rank weakly grounded synthesis; slightly boost high-grounding synthesis.

    Non-synthesis ideas always use 1.0.

    Extra down-rank for signal-type-only lattice rows (no concrete record/path in sources).
    Small boost when a finding id is paired with repo path and/or ``runs/…`` artifact hints.
    """
    if idea.source != IdeaSource.SYNTHESIS:
        return 1.0
    g = getattr(idea, "grounding", None)
    if not isinstance(g, dict):
        return _STRENGTH_MISSING
    s = str(g.get("grounding_strength") or "").lower()
    if s == "high":
        m = _STRENGTH_HIGH
    elif s == "medium":
        m = _STRENGTH_MEDIUM
    elif s == "low":
        m = _STRENGTH_LOW
    else:
        m = _STRENGTH_MISSING

    sources = _grounding_sources(idea)
    if _finding_plus_repo_or_artifact(sources):
        m *= _FINDING_PLUS_ARTIFACT_BOOST
    if _signal_type_lattice_only(sources):
        m *= _SIGNAL_TYPE_LATTICE_ONLY_FACTOR
    return m


__all__ = [
    "build_grounding",
    "is_weak_synthesis_filler",
    "synthesis_grounding_rank_multiplier",
]
