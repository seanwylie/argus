"""
Deterministic scores for ideas: novelty, adjacency, expected value, confidence.

All outputs are clipped to ``[0, 1]``. Uses stable hashing for reproducibility
when a ``seed`` string is provided (mutation / synthesis).

Rich **novelty** and **diversity_impact_score** are applied in :func:`finalize_batch_scores`
after a full idea list exists (see ``novelty.py`` / ``diversity.py``).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from argus.idea_generation.models import Idea, IdeaSource, IdeaType


def _stable01(key: str, salt: str) -> float:
    h = hashlib.sha256(f"{salt}:{key}".encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def lexical_type_novelty(idea: Idea, *, seed: str = "") -> float:
    """Higher when invent-type or unusual wording; lower for pure exploit language (lexical baseline)."""
    base = _stable01(idea.idea_id + idea.title, seed + "novelty")
    if idea.type == IdeaType.INVENT:
        base = 0.45 + 0.55 * base
    elif idea.type == IdeaType.EXPLORE:
        base = 0.25 + 0.45 * base
    else:
        base = 0.1 + 0.35 * base
    if re.search(r"\b(new|novel|first|hybrid)\b", idea.description, re.I):
        base = min(1.0, base + 0.12)
    return max(0.0, min(1.0, base))


def score_novelty(idea: Idea, *, seed: str = "") -> float:
    """Alias for :func:`lexical_type_novelty` (backward compatible)."""
    return lexical_type_novelty(idea, seed=seed)


def score_adjacency(idea: Idea, *, seed: str = "") -> float:
    """
    Proximity to familiar successful *patterns* (exploit = closest to known winners;
    explore = adjacent bets; invent = farther from the core — more novel territory).
    """
    base = _stable01(idea.description[:200], seed + "adj")
    if idea.type == IdeaType.EXPLORE:
        return max(0.0, min(1.0, 0.5 + 0.45 * base))
    if idea.type == IdeaType.INVENT:
        return max(0.0, min(1.0, 0.35 + 0.5 * base))
    return max(0.0, min(1.0, 0.15 + 0.45 * base))


def score_expected_value(idea: Idea, *, seed: str = "") -> float:
    """Proxy EV from type, source, and text strength (deterministic)."""
    base = _stable01(idea.rationale + idea.title, seed + "ev")
    boost = 0.0
    if idea.source == IdeaSource.FINDINGS:
        boost += 0.08
    if idea.source == IdeaSource.SIGNALS:
        boost += 0.05
    if idea.type == IdeaType.EXPLOIT:
        boost += 0.1
    if "revenue" in idea.description.lower() or "mrr" in idea.description.lower():
        boost += 0.07
    v = 0.35 * base + 0.25 + boost
    return max(0.0, min(1.0, v))


def score_confidence(idea: Idea, *, seed: str = "") -> float:
    """Higher when grounded in signals/findings; lower for pure mutation/advisors stub."""
    base = _stable01(idea.idea_id, seed + "conf")
    if idea.source == IdeaSource.SIGNALS:
        base = 0.55 + 0.4 * base
    elif idea.source == IdeaSource.FINDINGS:
        base = 0.5 + 0.42 * base
    elif idea.source == IdeaSource.SYNTHESIS:
        base = 0.4 + 0.38 * base
    elif idea.source == IdeaSource.MUTATION:
        base = 0.2 + 0.45 * base
    else:  # advisors
        base = 0.35 + 0.4 * base
    return max(0.0, min(1.0, base))


def apply_scores(idea: Idea, *, seed: str = "") -> Idea:
    """Mutate idea in place with computed scores (returns same object)."""
    idea.novelty_score = lexical_type_novelty(idea, seed=seed)
    idea.adjacency_score = score_adjacency(idea, seed=seed)
    idea.expected_value_score = score_expected_value(idea, seed=seed)
    idea.confidence_score = score_confidence(idea, seed=seed)
    return idea


def finalize_batch_scores(ideas: list[Idea], repo_root: Path, *, seed: str = "") -> None:
    """
    Recompute novelty from product/recent/batch context and set diversity_impact_score.

    Call once per bundle after all ideas are collected (see :func:`run_pipeline`).
    """
    from argus.idea_generation.diversity import (
        compute_batch_diversity_scores,
        saturation_penalty_for_position,
    )
    from argus.idea_generation.novelty import compute_novelty_score, idea_fingerprint
    from argus.idea_generation.pipeline import load_latest_bundle
    from argus.products.inventory import build_inventory

    root = repo_root.resolve()
    inv = build_inventory(root)
    product_summaries: list[str] = []
    inventory_type_counts: dict[str, int] = {}
    for rec in inv.valid.values():
        n = rec.node
        parts = [n.name, n.id]
        if n.type_info:
            parts.append(str(n.type_info.type or ""))
            parts.append(str(n.type_info.state or ""))
            t = str(n.type_info.type or "").strip()
            if t:
                inventory_type_counts[t] = inventory_type_counts.get(t, 0) + 1
        parts.extend(n.tags)
        product_summaries.append(" ".join(parts).lower())

    prior = load_latest_bundle(root)
    recent_texts: list[str] = []
    recent_fps: set[str] = set()
    if prior and prior.ideas:
        recent_texts = [f"{x.title} {x.description}" for x in prior.ideas[:120]]
        recent_fps = {idea_fingerprint(x) for x in prior.ideas}

    div_scores = compute_batch_diversity_scores(ideas, inventory_type_counts=inventory_type_counts or None)

    for i, idea in enumerate(ideas):
        before = ideas[:i]
        lex = lexical_type_novelty(idea, seed=seed)
        nov = compute_novelty_score(
            idea,
            product_summaries=product_summaries,
            recent_idea_texts=recent_texts,
            recent_fingerprints=recent_fps,
            batch_siblings=before,
            lexical_novelty=lex,
            seed=seed,
        )
        sat = saturation_penalty_for_position(idea, before)
        idea.novelty_score = max(0.0, min(1.0, nov * sat))
        idea.diversity_impact_score = div_scores[i] if i < len(div_scores) else 0.0


def rank_key(idea: Idea) -> float:
    """
    Final bundle sort key after batch scoring: EV×confidence + novelty + diversity terms.

    Synthesis ideas use :func:`~argus.idea_generation.synthesis_grounding.synthesis_grounding_rank_multiplier`.
    Signal hygiene and tone multipliers apply only in :func:`~argus.idea_generation.mechanical_cleanup.mechanical_rank_score`
    (cleanup selection), not here.
    """
    from argus.idea_generation.synthesis_grounding import synthesis_grounding_rank_multiplier

    base = (
        idea.expected_value_score * idea.confidence_score
        + 0.05 * idea.novelty_score
        + 0.03 * idea.diversity_impact_score
    )
    return max(0.0, base * synthesis_grounding_rank_multiplier(idea))
