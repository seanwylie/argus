"""Apply doctrine nudges to decision and experiment scores."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.models.validation import validate_decision_candidate
from argus.doctrine.models import ProductDoctrine


def apply_doctrine_to_decision_candidates(
    candidates: list[DecisionCandidate],
    doctrine: ProductDoctrine | None,
) -> list[DecisionCandidate]:
    """
    Scale ``priority_score`` by per-intent multipliers from doctrine (when set).

    Multipliers apply to the final 0–100 score; metadata records the adjustment.
    """
    if doctrine is None or not doctrine.scoring.intent_priority_multiplier:
        return candidates
    mult_map = doctrine.scoring.intent_priority_multiplier
    out: list[DecisionCandidate] = []
    for c in candidates:
        md = dict(c.metadata or {})
        intent_str = md.get("intent")
        if not intent_str or intent_str not in mult_map:
            out.append(c)
            continue
        m = float(mult_map[intent_str])
        if m <= 0:
            out.append(c)
            continue
        new_c = deepcopy(c)
        base = new_c.priority_score
        if base is not None:
            new_c.priority_score = round(min(100.0, max(0.0, base * m)), 2)
        meta = dict(new_c.metadata or {})
        meta["doctrine_intent_multiplier"] = m
        meta["doctrine_priority_score_before"] = base
        new_c.metadata = meta
        validate_decision_candidate(new_c)
        out.append(new_c)
    return out


def doctrine_multiplier_for_intent(doctrine: ProductDoctrine | None, intent_value: str) -> float:
    """Return multiplier for a :class:`DecisionIntent` value string, defaulting to 1.0."""
    if doctrine is None:
        return 1.0
    return float(doctrine.scoring.intent_priority_multiplier.get(intent_value, 1.0))


def apply_doctrine_to_ideas(
    ideas: list[Any],
    doctrine: ProductDoctrine | None,
) -> dict[str, Any]:
    """
    Nudge ``expected_value_score`` for explore/invent ideas when ``experiment_score_boost`` is set.

    Deterministic and bounded; recorded in bundle metadata for inspection.
    """
    from argus.idea_generation.models import IdeaType

    if doctrine is None:
        return {"applied": False, "reason": "no_doctrine"}
    b = float(doctrine.scoring.experiment_score_boost)
    if b <= 0:
        return {"applied": False, "experiment_score_boost": b}

    adjusted = 0
    for idea in ideas:
        if idea.type in (IdeaType.EXPLORE, IdeaType.INVENT):
            idea.expected_value_score = min(1.0, float(idea.expected_value_score) + b * 0.12)
            adjusted += 1
    return {
        "applied": True,
        "experiment_score_boost": b,
        "ideas_adjusted": adjusted,
    }
