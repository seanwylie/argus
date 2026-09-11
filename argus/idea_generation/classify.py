"""
Classify ideas into exploit | explore | invent using deterministic keyword/feature heuristics.

- **exploit** — tighten, optimize, scale what already works
- **explore** — adjacent bets, variants, channel tests
- **invent** — new category, novel pairings, greenfield
"""

from __future__ import annotations

import re

from argus.idea_generation.models import IdeaSource, IdeaType

# Weighted keyword groups (lowercase)
_EXPLOIT_PAT = re.compile(
    r"\b(optimize|scale|double down|efficiency|reduce cost|margin|conversion|"
    r"retention|improve|accelerate|automate|streamline|tighten|iterate)\b",
    re.I,
)
_EXPLORE_PAT = re.compile(
    r"\b(adjacent|variant|a/b|test|experiment|pilot|channel|segment|expand|"
    r"localization|partnership|niche|vertical)\b",
    re.I,
)
_INVENT_PAT = re.compile(
    r"\b(new category|novel|greenfield|first|unusual|combine|hybrid|invent|"
    r"paradigm|platform|ecosystem|net.new|breakthrough)\b",
    re.I,
)


def classify_type(
    title: str,
    description: str,
    *,
    source: IdeaSource | None = None,
) -> IdeaType:
    """
    Return the dominant strategy type from text heuristics.

    Source nudges: ``synthesis`` / ``mutation`` skew slightly toward explore/invent;
    ``signals`` / ``findings`` can skew exploit when metrics language dominates.
    """
    text = f"{title}\n{description}".lower()
    e = len(_EXPLOIT_PAT.findall(text))
    x = len(_EXPLORE_PAT.findall(text))
    n = len(_INVENT_PAT.findall(text))

    if source == IdeaSource.SYNTHESIS:
        n += 1
        x += 1
    elif source == IdeaSource.MUTATION:
        x += 1
    elif source in (IdeaSource.SIGNALS, IdeaSource.FINDINGS):
        e += 1

    if n >= max(e, x) and n > 0:
        return IdeaType.INVENT
    if x >= max(e, n) and x > 0:
        return IdeaType.EXPLORE
    if e > 0:
        return IdeaType.EXPLOIT
    # Default: explore (bounded bet) when no strong signal
    return IdeaType.EXPLORE
