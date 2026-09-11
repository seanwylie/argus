"""
Deterministic idea list reduction: deduplication, hygiene-aware ranking, diversity caps, hard max.

Runs before semantic / batch novelty (``finalize_batch_scores``) to limit explosion and
duplicate titles or near-duplicate bodies.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Any

from argus.idea_generation.models import Idea, IdeaSource
from argus.idea_generation.synthesis_grounding import (
    is_weak_synthesis_filler,
    synthesis_grounding_rank_multiplier,
)

# Near-duplicate: Jaccard on word sets (fast) OR sequence ratio on normalized text (substring-ish).
_DEFAULT_JACCARD_NEAR_DUP = 0.88
_DEFAULT_SEQUENCE_NEAR_DUP = 0.92

# Strong down-rank for manifest / sentinel / placeholder signal rows (multiplies mechanical rank).
_WEAK_SIGNAL_FLAGS_FACTOR = 0.18
_LOW_QUALITY_FACTOR = 0.42
_MEDIUM_QUALITY_FACTOR = 0.86

_DEFAULT_MAX_IDEAS = 12
_DEFAULT_MAX_PER_BUCKET = 3

# Quality gate (after structural filters): do not pad to max_ideas with weak tail rows.
# min_score = max(absolute_floor, batch_best_mechanical * relative_factor).
# Weak synthesis fillers (lattice-only, combinatorial-only, etc.) use a stricter relative factor.
# Stricter multiplier for :func:`is_weak_synthesis_filler` rows (lattice-only, combinatorial-only, etc.).
_QUALITY_BATCH_BEST_RELATIVE = 0.55
_QUALITY_WEAK_SYNTH_RELATIVE = 0.72
_QUALITY_ABSOLUTE_FLOOR = 1e-6


def quality_minimum_mechanical_score(batch_best: float, idea: Idea) -> float:
    """
    Minimum :func:`mechanical_rank_score` required to keep an idea after the first slot.

    First kept idea is never subject to this gate (guarantees at least one idea when input non-empty).
    """
    rel = _QUALITY_WEAK_SYNTH_RELATIVE if is_weak_synthesis_filler(idea) else _QUALITY_BATCH_BEST_RELATIVE
    return max(_QUALITY_ABSOLUTE_FLOOR, float(batch_best) * rel)


def normalize_idea_text(text: str) -> str:
    """Lowercase, strip, collapse whitespace, remove punctuation for dedup comparisons."""
    t = text.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def combined_normalized_idea_text(idea: Idea) -> str:
    """Single string for exact / near-duplicate checks (title + description)."""
    return normalize_idea_text(f"{idea.title}\n{idea.description}")


def _word_set(norm: str) -> set[str]:
    return {w for w in norm.split() if len(w) >= 2}


def jaccard_similarity(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def sequence_similarity(a: str, b: str) -> float:
    """Bounded comparison; truncate very long texts for speed."""
    if a == b:
        return 1.0
    a2, b2 = a[:4000], b[:4000]
    if not a2 or not b2:
        return 0.0
    return SequenceMatcher(None, a2, b2).ratio()


def is_near_duplicate(
    norm: str,
    word_set: set[str],
    kept_norms: list[str],
    kept_word_sets: list[set[str]],
    *,
    jaccard_threshold: float,
    sequence_threshold: float,
) -> bool:
    for prev_norm, prev_ws in zip(kept_norms, kept_word_sets):
        if jaccard_similarity(word_set, prev_ws) >= jaccard_threshold:
            return True
        if sequence_similarity(norm, prev_norm) >= sequence_threshold:
            return True
    return False


def hygiene_mechanical_factor(idea: Idea) -> float:
    """
    Down-rank ideas tied to weak signal rows (manifest gaps, sentinels, placeholders).

    Non-signal ideas use factor 1.0. Signal ideas without hygiene metadata use 1.0.
    """
    if idea.source != IdeaSource.SIGNALS:
        return 1.0
    h = idea.signal_hygiene
    if not isinstance(h, dict):
        return 1.0
    if h.get("is_manifest_declaration") or h.get("is_sentinel_timestamp") or h.get("is_placeholder"):
        return _WEAK_SIGNAL_FLAGS_FACTOR
    q = h.get("signal_quality_score")
    if q == "low":
        return _LOW_QUALITY_FACTOR
    if q == "medium":
        return _MEDIUM_QUALITY_FACTOR
    return 1.0


def mechanical_rank_score(idea: Idea) -> float:
    """
    Deterministic pre-batch rank for cleanup selection.

    ``(expected_value_score * confidence_score + 0.05 * novelty_score)
    * hygiene_mechanical_factor * tone_rank_multiplier * synthesis_grounding_rank_multiplier``

    Optional ``idea.tone_alignment["rank_multiplier"]`` (tone alignment vs decision/findings) applies here.

    ``diversity_impact_score`` is omitted (set later in ``finalize_batch_scores``; final sort uses ``rank_key``).
    """
    base = idea.expected_value_score * idea.confidence_score + 0.05 * idea.novelty_score
    h = hygiene_mechanical_factor(idea)
    tm = 1.0
    ta = getattr(idea, "tone_alignment", None)
    if isinstance(ta, dict) and ta.get("rank_multiplier") is not None:
        try:
            tm = float(ta["rank_multiplier"])
        except (TypeError, ValueError):
            tm = 1.0
    sg = synthesis_grounding_rank_multiplier(idea)
    return max(0.0, base * h * tm * sg)


def diversity_bucket_key(idea: Idea) -> str:
    """
    Simple bucket for capping repetition (no clustering).

    Signal ideas: ``sig:<signal_type>:<adapter_source>`` when provenance is present;
    else ``sig:unknown``. Other sources: coarse bucket by source + channel.
    """
    if idea.source == IdeaSource.SIGNALS:
        sp = idea.signal_provenance
        if isinstance(sp, dict):
            st = str(sp.get("signal_type") or "unknown")
            ad = str(sp.get("adapter_source") or "unknown")
            return f"sig:{st}:{ad}"
        return "sig:unknown"
    if idea.source == IdeaSource.FINDINGS:
        return f"find:{idea.channel_type}"
    if idea.source == IdeaSource.SYNTHESIS:
        return f"synth:{idea.channel_type}:{idea.monetization_type}"
    if idea.source == IdeaSource.MUTATION:
        return "mutation"
    return f"other:{idea.source.value}"


def mechanical_cleanup(
    ideas: list[Idea],
    *,
    max_ideas: int = _DEFAULT_MAX_IDEAS,
    max_per_bucket: int = _DEFAULT_MAX_PER_BUCKET,
    jaccard_threshold: float = _DEFAULT_JACCARD_NEAR_DUP,
    sequence_threshold: float = _DEFAULT_SEQUENCE_NEAR_DUP,
) -> tuple[list[Idea], dict[str, Any]]:
    """
    Sort by mechanical rank, then greedily keep ideas under dedup + per-bucket caps + ``max_ideas``.

    Returns ``(selected, meta)`` where ``meta`` includes counts and rejection rows for auditing.
    """
    n_in = len(ideas)
    max_ideas = max(1, int(max_ideas))
    max_per_bucket = max(1, int(max_per_bucket))

    ordered = sorted(ideas, key=mechanical_rank_score, reverse=True)
    scores_all = [mechanical_rank_score(i) for i in ideas]
    batch_best = max(scores_all) if scores_all else 0.0

    selected: list[Idea] = []
    seen_exact: set[str] = set()
    kept_norms: list[str] = []
    kept_word_sets: list[set[str]] = []
    bucket_counts: dict[str, int] = {}
    rejected: list[dict[str, Any]] = []

    counts: dict[str, int] = {
        "rejected_exact_duplicate": 0,
        "rejected_near_duplicate": 0,
        "rejected_bucket_cap": 0,
        "rejected_ranked_out": 0,
        "rejected_below_quality_threshold": 0,
    }

    for idea in ordered:
        if len(selected) >= max_ideas:
            counts["rejected_ranked_out"] += 1
            rejected.append(
                {
                    "selection": "rejected_ranked_out",
                    "reason": "after_cap",
                    "idea_id": idea.idea_id,
                    "title": idea.title[:200],
                    "source": idea.source.value,
                }
            )
            continue

        norm = combined_normalized_idea_text(idea)
        if not norm:
            counts["rejected_exact_duplicate"] += 1
            rejected.append(
                {
                    "selection": "rejected_duplicate",
                    "reason": "empty_normalized_text",
                    "idea_id": idea.idea_id,
                    "title": idea.title[:200],
                }
            )
            continue

        if norm in seen_exact:
            counts["rejected_exact_duplicate"] += 1
            rejected.append(
                {
                    "selection": "rejected_duplicate",
                    "reason": "exact_normalized_text",
                    "idea_id": idea.idea_id,
                    "title": idea.title[:200],
                }
            )
            continue

        ws = _word_set(norm)
        if is_near_duplicate(norm, ws, kept_norms, kept_word_sets, jaccard_threshold=jaccard_threshold, sequence_threshold=sequence_threshold):
            counts["rejected_near_duplicate"] += 1
            rejected.append(
                {
                    "selection": "rejected_duplicate",
                    "reason": "near_duplicate",
                    "idea_id": idea.idea_id,
                    "title": idea.title[:200],
                }
            )
            continue

        bkey = diversity_bucket_key(idea)
        if bucket_counts.get(bkey, 0) >= max_per_bucket:
            counts["rejected_bucket_cap"] += 1
            rejected.append(
                {
                    "selection": "rejected_bucket_cap",
                    "reason": "max_per_diversity_bucket",
                    "bucket": bkey,
                    "idea_id": idea.idea_id,
                    "title": idea.title[:200],
                }
            )
            continue

        mscore = mechanical_rank_score(idea)
        qmin = quality_minimum_mechanical_score(batch_best, idea)
        if len(selected) >= 1 and mscore < qmin:
            counts["rejected_below_quality_threshold"] += 1
            rejected.append(
                {
                    "selection": "rejected_below_quality_threshold",
                    "reason": "below_mechanical_quality_floor",
                    "idea_id": idea.idea_id,
                    "title": idea.title[:200],
                    "source": idea.source.value,
                    "mechanical_rank_score": round(mscore, 8),
                    "quality_minimum": round(qmin, 8),
                    "batch_best_mechanical": round(batch_best, 8),
                    "weak_synthesis_filler": is_weak_synthesis_filler(idea),
                }
            )
            continue

        selected.append(idea)
        seen_exact.add(norm)
        kept_norms.append(norm)
        kept_word_sets.append(ws)
        bucket_counts[bkey] = bucket_counts.get(bkey, 0) + 1

    stopped_early = (
        counts["rejected_below_quality_threshold"] > 0 and len(selected) < max_ideas
    )
    quality_rule = (
        "min_score=max(absolute_floor, batch_best_mechanical * relative); "
        f"relative={_QUALITY_BATCH_BEST_RELATIVE} for most ideas, "
        f"{_QUALITY_WEAK_SYNTH_RELATIVE} when is_weak_synthesis_filler; "
        f"first_kept_idea_exempt; absolute_floor={_QUALITY_ABSOLUTE_FLOOR}"
    )

    meta: dict[str, Any] = {
        "ideas_input": n_in,
        "ideas_output": len(selected),
        "max_ideas": max_ideas,
        "max_per_diversity_bucket": max_per_bucket,
        "dedup": {
            "normalize": "lowercase_strip_punctuation_collapse_whitespace",
            "near_duplicate": {
                "jaccard_word_threshold": jaccard_threshold,
                "sequence_ratio_threshold": sequence_threshold,
            },
        },
        "ranking": {
            "mechanical_rank_score": "expected_value * confidence * hygiene_factor * tone_multiplier * synthesis_grounding_multiplier + 0.05 * novelty",
            "hygiene_factors": {
                "weak_signal_manifest_sentinel_placeholder": _WEAK_SIGNAL_FLAGS_FACTOR,
                "quality_low": _LOW_QUALITY_FACTOR,
                "quality_medium": _MEDIUM_QUALITY_FACTOR,
            },
        },
        "quality_threshold": {
            "stopped_early_for_quality": stopped_early,
            "quality_threshold_rule": quality_rule,
            "batch_best_mechanical": round(batch_best, 8),
        },
        "rejection_counts": counts,
        "rejected_ideas": rejected,
        "diversity_bucket_counts_selected": dict(sorted(bucket_counts.items())),
    }
    return selected, meta


__all__ = [
    "combined_normalized_idea_text",
    "diversity_bucket_key",
    "hygiene_mechanical_factor",
    "mechanical_cleanup",
    "mechanical_rank_score",
    "normalize_idea_text",
    "quality_minimum_mechanical_score",
]
