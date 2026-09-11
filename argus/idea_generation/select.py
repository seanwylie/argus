"""
Balanced selection from generated ideas: diversity, novelty, channel caps, cost, strategy.

Policy default: **60% exploit / 30% explore / 10% invent** (slot counts rounded deterministically).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argus.idea_generation.models import Idea, IdeaType
from argus.idea_generation.novelty import idea_fingerprint, jaccard, normalize_tokens
from argus.strategy.modes import StrategyMode, StrategyProfile


def _cost_units(idea: Idea) -> float:
    """Map ``cost_estimate`` string to comparable units (deterministic)."""
    s = (idea.cost_estimate or "").lower().strip()
    if "xlarge" in s or " xl" in s:
        return 8.0
    if "large" in s:
        return 4.0
    if "medium" in s:
        return 2.0
    if "small" in s or "tiny" in s or "minimal" in s:
        return 1.0
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits:
        n = int(digits)
        if n >= 50000:
            return 8.0
        if n >= 10000:
            return 4.0
        if n >= 2000:
            return 2.0
        return 1.0
    return 2.0


def _alignment_weight(idea: Idea, mode: StrategyMode | None) -> float:
    """Strategy multiplier for ranking (deterministic, bounded)."""
    if mode is None:
        return 1.0
    t = idea.type
    ev = idea.expected_value_score
    if mode == StrategyMode.GROWTH:
        return 1.0 + 0.12 * ev + (0.06 if t == IdeaType.EXPLOIT else 0.0)
    if mode == StrategyMode.PROFIT:
        pen = 0.08 if _cost_units(idea) >= 4.0 else 0.0
        return 1.0 + 0.1 * ev + 0.05 * idea.confidence_score - pen
    if mode == StrategyMode.EXPLORATION:
        return 1.0 + (0.14 if t == IdeaType.INVENT else 0.0) + (0.1 if t == IdeaType.EXPLORE else 0.0)
    if mode == StrategyMode.SURVIVAL:
        c = _cost_units(idea)
        return 1.0 - 0.12 * min(1.0, c / 4.0) + (0.05 if t == IdeaType.EXPLOIT else 0.0)
    return 1.0


def _composite_score(idea: Idea, mode: StrategyMode | None) -> float:
    base = (
        idea.expected_value_score * idea.confidence_score
        + 0.08 * idea.novelty_score
        + 0.04 * idea.adjacency_score
    )
    return max(0.0, base * _alignment_weight(idea, mode))


@dataclass(frozen=True)
class SelectionPolicy:
    """Targets and limits for portfolio selection."""

    total_slots: int = 10
    exploit_ratio: float = 0.6
    explore_ratio: float = 0.3
    invent_ratio: float = 0.1
    max_per_channel: int = 3
    channel_max_overrides: dict[str, int] = field(default_factory=dict)
    max_cost_units: float = 24.0
    low_novelty_threshold: float = 0.22
    low_value_threshold: float = 0.28
    max_pairwise_jaccard: float = 0.88
    force_invent_if_available: bool = True


def _slot_allocation(policy: SelectionPolicy) -> tuple[int, int, int]:
    n = max(0, policy.total_slots)
    if n == 0:
        return 0, 0, 0
    r = policy.exploit_ratio + policy.explore_ratio + policy.invent_ratio
    if r <= 0:
        return n, 0, 0
    ne = int(round(n * policy.exploit_ratio / r))
    nx = int(round(n * policy.explore_ratio / r))
    ni = n - ne - nx
    if ni < 0:
        ni = 0
        nx = max(0, n - ne)
    return ne, nx, ni


def _channel_cap(channel: str, policy: SelectionPolicy) -> int:
    ch = channel.lower().strip() or "hybrid"
    for key, cap in policy.channel_max_overrides.items():
        if key.lower() == ch:
            return cap
    return policy.max_per_channel


@dataclass
class SelectionResult:
    """Selected ideas plus audit trail."""

    selected: list[Idea]
    rejected: list[tuple[str, str]] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


def select_ideas(
    ideas: list[Idea],
    policy: SelectionPolicy | None = None,
    *,
    strategy_profile: StrategyProfile | None = None,
) -> SelectionResult:
    """
    Return a balanced subset: quotas per :class:`IdeaType`, channel saturation, cost budget,
    strategy alignment, deduplication, and rejection of low-value + low-novelty pairs.

    Deterministic: tie-break by ``(-composite_score, idea_id)``.
    """
    policy = policy or SelectionPolicy()
    mode = strategy_profile.mode if strategy_profile is not None else None

    rejected: list[tuple[str, str]] = []

    def sort_key(i: Idea) -> tuple[float, str]:
        return (-_composite_score(i, mode), i.idea_id)

    # Per fingerprint keep strongest candidate only (deterministic order)
    best_by_fp: dict[str, Idea] = {}
    for idea in sorted(ideas, key=lambda x: x.idea_id):
        fp = idea_fingerprint(idea)
        prev = best_by_fp.get(fp)
        if prev is None or sort_key(idea) < sort_key(prev):
            if prev is not None:
                rejected.append((prev.idea_id, "duplicate_fingerprint_inferior"))
            best_by_fp[fp] = idea
        else:
            rejected.append((idea.idea_id, "duplicate_fingerprint_inferior"))

    prelim: list[Idea] = []
    for idea in sorted(best_by_fp.values(), key=lambda x: x.idea_id):
        if (
            idea.novelty_score <= policy.low_novelty_threshold
            and idea.expected_value_score <= policy.low_value_threshold
        ):
            rejected.append((idea.idea_id, "low_novelty_and_low_value"))
            continue
        prelim.append(idea)

    by_type: dict[IdeaType, list[Idea]] = {IdeaType.EXPLOIT: [], IdeaType.EXPLORE: [], IdeaType.INVENT: []}
    for idea in prelim:
        by_type[idea.type].append(idea)
    for k in by_type:
        by_type[k].sort(key=sort_key)

    ne, nx, ni = _slot_allocation(policy)
    invent_available = len(by_type[IdeaType.INVENT]) > 0

    selected: list[Idea] = []
    channel_counts: dict[str, int] = {}
    cost_sum = 0.0
    taken_ids: set[str] = set()
    tokens_selected: list[set[str]] = []

    def can_add(idea: Idea) -> bool:
        if idea.idea_id in taken_ids:
            return False
        ch = idea.channel_type.lower().strip() or "hybrid"
        if channel_counts.get(ch, 0) >= _channel_cap(ch, policy):
            return False
        if cost_sum + _cost_units(idea) > policy.max_cost_units + 1e-9:
            return False
        blob = f"{idea.title} {idea.description}"
        toks = normalize_tokens(blob)
        for ts in tokens_selected:
            if toks and ts and jaccard(toks, ts) > policy.max_pairwise_jaccard:
                return False
        return True

    def take(idea: Idea) -> None:
        nonlocal cost_sum
        selected.append(idea)
        taken_ids.add(idea.idea_id)
        ch = idea.channel_type.lower().strip() or "hybrid"
        channel_counts[ch] = channel_counts.get(ch, 0) + 1
        cost_sum += _cost_units(idea)
        tokens_selected.append(normalize_tokens(f"{idea.title} {idea.description}"))

    def try_pick(pool: list[Idea], limit: int) -> int:
        n = 0
        for idea in pool:
            if n >= limit:
                break
            if idea.idea_id in taken_ids:
                continue
            if can_add(idea):
                take(idea)
                n += 1
        return n

    def count_type(t: IdeaType) -> int:
        return sum(1 for s in selected if s.type == t)

    # Fill invent quota first so 10% invent is not crowded out by exploit/explore
    try_pick(by_type[IdeaType.INVENT], ni)
    try_pick([i for i in by_type[IdeaType.EXPLOIT] if i.idea_id not in taken_ids], ne)
    try_pick([i for i in by_type[IdeaType.EXPLORE] if i.idea_id not in taken_ids], nx)

    # Top up toward targets if earlier picks skipped due to caps
    try_pick([i for i in by_type[IdeaType.EXPLOIT] if i.idea_id not in taken_ids], max(0, ne - count_type(IdeaType.EXPLOIT)))
    try_pick([i for i in by_type[IdeaType.EXPLORE] if i.idea_id not in taken_ids], max(0, nx - count_type(IdeaType.EXPLORE)))
    try_pick([i for i in by_type[IdeaType.INVENT] if i.idea_id not in taken_ids], max(0, ni - count_type(IdeaType.INVENT)))

    # Backfill to total_slots
    remaining = policy.total_slots - len(selected)
    if remaining > 0:
        overflow: list[Idea] = []
        for t in (IdeaType.EXPLOIT, IdeaType.EXPLORE, IdeaType.INVENT):
            for idea in by_type[t]:
                if idea.idea_id not in taken_ids:
                    overflow.append(idea)
        overflow.sort(key=sort_key)
        try_pick(overflow, remaining)

    invent_forced = False
    if (
        policy.force_invent_if_available
        and invent_available
        and count_type(IdeaType.INVENT) == 0
        and policy.total_slots > 0
    ):
        candidates = [i for i in by_type[IdeaType.INVENT] if i.idea_id not in taken_ids]
        candidates.sort(key=sort_key)
        placed = False
        for inv in candidates:
            if can_add(inv):
                take(inv)
                invent_forced = True
                placed = True
                break
        if not placed and candidates:
            best_inv = candidates[0]
            scored = sorted(
                [(s, _composite_score(s, mode)) for s in selected if s.type != IdeaType.INVENT],
                key=lambda x: x[1],
            )
            if scored:
                victim = scored[0][0]
                selected.remove(victim)
                taken_ids.discard(victim.idea_id)
                ch = victim.channel_type.lower().strip() or "hybrid"
                channel_counts[ch] = max(0, channel_counts.get(ch, 0) - 1)
                cost_sum -= _cost_units(victim)
                tokens_selected.clear()
                for s in selected:
                    tokens_selected.append(normalize_tokens(f"{s.title} {s.description}"))
                if can_add(best_inv):
                    take(best_inv)
                    invent_forced = True

    selected.sort(key=lambda x: (-_composite_score(x, mode), x.idea_id))

    meta: dict[str, Any] = {
        "slots_allocated": {"exploit": ne, "explore": nx, "invent": ni},
        "selected_by_type": {
            "exploit": count_type(IdeaType.EXPLOIT),
            "explore": count_type(IdeaType.EXPLORE),
            "invent": count_type(IdeaType.INVENT),
        },
        "cost_units_used": round(cost_sum, 4),
        "max_cost_units": policy.max_cost_units,
        "channel_counts": dict(sorted(channel_counts.items())),
        "strategy_mode": mode.value if mode else None,
        "invent_forced": invent_forced,
    }

    return SelectionResult(selected=selected, rejected=rejected, meta=meta)
