"""
Portfolio-level diversity: channel, monetization, product-type mix; saturation and lineage penalties.

``diversity_impact_score`` measures how much an idea *adds* to underrepresented dimensions
in the current batch + optional inventory hints. Penalties reduce scores when the portfolio
would over-index on one channel, lineage, or repeated (channel, monetization, type) pattern.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Iterable

from argus.idea_generation.models import Idea


def _normalized_entropy(counter: Counter[str]) -> float:
    """Shannon entropy normalized by max entropy for support size (0–1)."""
    total = sum(counter.values())
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counter.values():
        if c <= 0:
            continue
        p = c / total
        h -= p * math.log2(p + 1e-15)
    k = len([x for x in counter.values() if x > 0])
    if k <= 1:
        return 0.0
    return max(0.0, min(1.0, h / math.log2(k)))


def pattern_key(idea: Idea) -> tuple[str, str, str]:
    return (idea.channel_type.lower(), idea.monetization_type.lower(), idea.type.value)


def count_dimensions(ideas: Iterable[Idea]) -> tuple[Counter[str], Counter[str], Counter[str], Counter[tuple[str, str, str]]]:
    ch: Counter[str] = Counter()
    mo: Counter[str] = Counter()
    ln: Counter[str] = Counter()
    pat: Counter[tuple[str, str, str]] = Counter()
    for i in ideas:
        ch[i.channel_type.lower()] += 1
        mo[i.monetization_type.lower()] += 1
        pid = i.parent_idea_id or "__root__"
        ln[pid] += 1
        pat[pattern_key(i)] += 1
    return ch, mo, ln, pat


def marginal_diversity_impact(
    idea: Idea,
    *,
    prior_channels: Counter[str],
    prior_monetizations: Counter[str],
    prior_patterns: Counter[tuple[str, str, str]],
    inventory_type_counts: dict[str, int] | None = None,
) -> float:
    """
    0–1 score: higher when the idea's channel/monetization/pattern is *rare* in priors
    (helps diversify the portfolio).
    """
    ch = idea.channel_type.lower()
    mo = idea.monetization_type.lower()
    pk = pattern_key(idea)

    n_ch = sum(prior_channels.values()) or 1
    n_mo = sum(prior_monetizations.values()) or 1
    n_pt = sum(prior_patterns.values()) or 1

    rarity_ch = 1.0 - (prior_channels.get(ch, 0) / n_ch)
    rarity_mo = 1.0 - (prior_monetizations.get(mo, 0) / n_mo)
    rarity_pt = 1.0 - (prior_patterns.get(pk, 0) / n_pt)

    inv_boost = 0.0
    if inventory_type_counts:
        # If we have product types from inventory, reward ideas that don't mirror dominant type
        total_t = sum(inventory_type_counts.values()) or 1
        # no per-idea product type on Idea — use channel as weak proxy for "different from dominant product type"
        inv_boost = 0.15 * (1.0 - max(inventory_type_counts.values()) / total_t)

    raw = 0.38 * rarity_ch + 0.38 * rarity_mo + 0.19 * rarity_pt + inv_boost
    return max(0.0, min(1.0, raw))


def saturation_penalty_multiplier(
    idea: Idea,
    *,
    channel_counts: Counter[str],
    monetization_counts: Counter[str],
    lineage_counts: Counter[str],
    pattern_counts: Counter[tuple[str, str, str]],
    channel_cap: int = 6,
    pattern_cap: int = 5,
    lineage_cap: int = 4,
) -> float:
    """
    Returns multiplier in (0, 1] applied to novelty (or combined score) when the portfolio
    is over-saturated on one dimension.
    """
    ch = idea.channel_type.lower()
    mo = idea.monetization_type.lower()
    pk = pattern_key(idea)

    pen = 1.0
    if channel_counts.get(ch, 0) >= channel_cap:
        pen *= 0.72
    if pattern_counts.get(pk, 0) >= pattern_cap:
        pen *= 0.68
    # Same lineage: many mutations from same parent
    parent_key = idea.parent_idea_id or "__root__"
    if idea.parent_idea_id and lineage_counts.get(parent_key, 0) >= lineage_cap:
        pen *= 0.78
    # Many ideas with same monetization in batch
    if monetization_counts.get(mo, 0) >= channel_cap:
        pen *= 0.85
    return pen


def saturation_penalty_for_position(idea: Idea, ideas_before: list[Idea]) -> float:
    """Penalty using only ideas already placed before ``idea`` in the batch."""
    ch, mo, ln, pat = count_dimensions(ideas_before)
    return saturation_penalty_multiplier(
        idea,
        channel_counts=ch,
        monetization_counts=mo,
        lineage_counts=ln,
        pattern_counts=pat,
    )


def compute_batch_diversity_scores(
    ideas: list[Idea],
    *,
    inventory_type_counts: dict[str, int] | None = None,
) -> list[float]:
    """
    For each idea in order, compute diversity_impact_score using only *prior* ideas in the list
    (simulates incremental portfolio construction).
    """
    ch: Counter[str] = Counter()
    mo: Counter[str] = Counter()
    pat: Counter[tuple[str, str, str]] = Counter()
    out: list[float] = []
    for idea in ideas:
        score = marginal_diversity_impact(
            idea,
            prior_channels=ch,
            prior_monetizations=mo,
            prior_patterns=pat,
            inventory_type_counts=inventory_type_counts,
        )
        out.append(score)
        ch[idea.channel_type.lower()] += 1
        mo[idea.monetization_type.lower()] += 1
        pat[pattern_key(idea)] += 1
    return out


def portfolio_diversity_meta(ideas: list[Idea]) -> dict[str, object]:
    """Summary block for bundle ``meta`` (channel / monetization / type diversity)."""
    ch, mo, _ln, _pat = count_dimensions(ideas)
    type_counts = Counter(i.type.value for i in ideas)
    return {
        "channel_diversity": round(_normalized_entropy(ch), 4),
        "monetization_diversity": round(_normalized_entropy(mo), 4),
        "idea_type_diversity": round(_normalized_entropy(type_counts), 4),
        "distinct_channels": len(ch),
        "distinct_monetizations": len(mo),
        "distinct_types": len(type_counts),
    }
