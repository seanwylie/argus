"""
Structured novelty scoring: distance from products and recent ideas, cross-domain signal,
and unusual channel/monetization pairings.

Blended with lexical/type novelty from :mod:`argus.idea_generation.score` (``lexical_type_novelty``).
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

from argus.idea_generation.models import Idea

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)

# Rough domain buckets — more distinct hits → higher cross-domain score
_DOMAIN_BUCKETS: tuple[tuple[str, frozenset[str]], ...] = (
    ("content", frozenset({"content", "seo", "blog", "newsletter", "video", "tiktok", "youtube", "media"})),
    ("commerce", frozenset({"commerce", "shop", "ecommerce", "marketplace", "retail"})),
    ("saas", frozenset({"saas", "b2b", "api", "platform", "subscription", "dashboard"})),
    ("mobile", frozenset({"mobile", "app", "ios", "android"})),
    ("infra", frozenset({"infra", "hosting", "cloud", "devops", "data"})),
    ("social", frozenset({"social", "community", "network", "viral"})),
)

# Common channel × monetization pairs (saturated patterns) — lower pairing novelty when matched
_COMMON_PAIRINGS: frozenset[tuple[str, str]] = frozenset(
    {
        ("tiktok", "ads"),
        ("web", "ads"),
        ("web", "subscription"),
        ("web", "seo"),
        ("tool", "subscription"),
        ("hybrid", "hybrid"),
    }
)


def normalize_tokens(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def max_similarity_to_corpus(text: str, corpus: list[str]) -> float:
    """Max Jaccard similarity between ``text`` and any string in ``corpus``."""
    t = normalize_tokens(text)
    if not t or not corpus:
        return 0.0
    best = 0.0
    for c in corpus:
        sim = jaccard(t, normalize_tokens(c))
        if sim > best:
            best = sim
    return best


def idea_fingerprint(idea: Idea) -> str:
    """Stable fingerprint for near-duplicate detection (title + channel + monetization)."""
    raw = f"{idea.title.lower().strip()[:200]}|{idea.channel_type.lower()}|{idea.monetization_type.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def cross_domain_score(title: str, description: str) -> float:
    """Higher when multiple distinct domain buckets appear (cross-domain combination)."""
    blob = f"{title} {description}".lower()
    hit_buckets = 0
    for _name, words in _DOMAIN_BUCKETS:
        if any(w in blob for w in words):
            hit_buckets += 1
    if hit_buckets <= 1:
        return 0.25 + 0.1 * hit_buckets
    return min(1.0, 0.35 + 0.22 * (hit_buckets - 1))


def pairing_novelty(channel: str, monetization: str) -> float:
    """Higher when (channel, monetization) is not a clichéd pairing."""
    c = channel.lower().strip() or "hybrid"
    m = monetization.lower().strip() or "hybrid"
    if (c, m) in _COMMON_PAIRINGS:
        return 0.22
    if c == "hybrid" and m == "hybrid":
        return 0.35
    return 0.72


def duplicate_penalty(
    idea: Idea,
    *,
    recent_fingerprints: set[str],
    sibling_ideas: Iterable[Idea],
) -> float:
    """
    Multiplier in (0, 1]: strong penalty for fingerprint match or very high text overlap
    with recent ideas or siblings in the same batch.
    """
    fp = idea_fingerprint(idea)
    if fp in recent_fingerprints:
        return 0.12

    idea_text = f"{idea.title} {idea.description}"
    itoks = normalize_tokens(idea_text)
    if len(itoks) < 4:
        return 1.0

    for other in sibling_ideas:
        if other.idea_id == idea.idea_id:
            continue
        otoks = normalize_tokens(f"{other.title} {other.description}")
        sim = jaccard(itoks, otoks)
        if sim >= 0.82:
            return 0.18
        if sim >= 0.55:
            return 0.55
    return 1.0


def compute_novelty_score(
    idea: Idea,
    *,
    product_summaries: list[str],
    recent_idea_texts: list[str],
    recent_fingerprints: set[str],
    batch_siblings: list[Idea],
    lexical_novelty: float,
    seed: str = "",
) -> float:
    """
    Combine lexical novelty with structural signals.

    - **product distance**: higher when idea text differs from all product summaries.
    - **recent ideas**: duplicate fingerprint / overlap lowers score (via penalty applied outside or inside).
    - **cross-domain** and **pairing** boost unusual combinations.
    """
    idea_blob = f"{idea.title}\n{idea.description}\n{idea.rationale}"

    diff_products = 1.0 - max_similarity_to_corpus(idea_blob, product_summaries)
    diff_products = max(0.0, min(1.0, diff_products))

    diff_recent = 1.0
    if recent_idea_texts:
        diff_recent = 1.0 - max_similarity_to_corpus(idea_blob, recent_idea_texts)
        diff_recent = max(0.0, min(1.0, diff_recent))

    cross = cross_domain_score(idea.title, idea.description)
    pair = pairing_novelty(idea.channel_type, idea.monetization_type)

    dup_pen = duplicate_penalty(
        idea,
        recent_fingerprints=recent_fingerprints,
        sibling_ideas=batch_siblings,
    )

    # Weighted blend (deterministic; no RNG)
    raw = (
        0.22 * lexical_novelty
        + 0.28 * diff_products
        + 0.18 * diff_recent
        + 0.16 * cross
        + 0.16 * pair
    )
    raw *= dup_pen
    return max(0.0, min(1.0, raw))
