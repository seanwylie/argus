"""
Controlled random mutations over structured :class:`Idea` rows.

Dimensions: channel, monetization, audience, format, timing. Each run mixes **sane** adjacencies
with a configurable fraction of **weird** variants to force unconventional thinking without
being fully random noise — randomness is **seeded** for reproducibility.
"""

from __future__ import annotations

import hashlib
import random
from typing import Sequence

from argus.idea_generation.classify import classify_type
from argus.idea_generation.models import Idea, IdeaSource, IdeaType, new_idea_id
from argus.idea_generation.score import apply_scores

# --- Pools: "sane" = plausible pivot; "weird" = intentionally odd (not all mutations are sensible) ---

CHANNEL_SANE = (
    "tiktok",
    "instagram",
    "youtube",
    "discord_bot",
    "slack_app",
    "web_app",
    "email",
    "newsletter",
    "podcast",
    "api",
    "chrome_extension",
    "reddit",
    "linkedin",
)

CHANNEL_WEIRD = (
    "fax_machine_hotline",
    "morse_code_club",
    "pigeon_net",
    "submarine_bulletin_board",
    "interpretive_dance_tour",
    "ar_sidewalk_chalk",
    "elevator_speakers_only",
    "library_whisper_network",
)

MONETIZATION_SANE = (
    "ads",
    "subscription",
    "usage_based",
    "enterprise_license",
    "affiliate",
    "tips",
    "one_time",
    "marketplace_take_rate",
)

MONETIZATION_WEIRD = (
    "barter_spices",
    "karma_points_only",
    "paid_in_compliments",
    "nft_of_silence",
    "sponsor_a_bug",
    "revenue_share_with_cats",
)

AUDIENCE_SANE = (
    "kids",
    "students",
    "creators",
    "smb",
    "enterprise",
    "developers",
    "hobbyists",
    "parents",
)

AUDIENCE_WEIRD = (
    "time_travelers",
    "rival_saas_ceos",
    "competitive_pigeons",
    "sleepwalkers",
    "underwater_city_planners",
)

FORMAT_SANE = (
    "video",
    "article",
    "tool",
    "course",
    "interactive_quiz",
    "api_product",
    "mobile_app",
    "browser_game",
)

FORMAT_WEIRD = (
    "choose_your_own_sms",
    "smell_o_vision_kit",
    "spreadsheet_opera",
    "live_whiteboard_rpg",
)

TIMING_SANE = (
    "real_time",
    "async",
    "daily_batch",
    "weekly_cadence",
    "seasonal",
    "on_demand",
)

TIMING_WEIRD = (
    "only_during_solar_eclipses",
    "leap_seconds_only",
    "when_mercury_retrograde",
    "every_full_moon",
)

MAJOR_DIMENSIONS = frozenset({"channel", "monetization", "audience"})
ALL_DIMENSIONS = ("channel", "monetization", "audience", "format", "timing")


def _rng(seed: str, idea_id: str, index: int) -> random.Random:
    h = hashlib.sha256(f"{seed}:{idea_id}:mut:{index}".encode()).hexdigest()
    return random.Random(int(h[:16], 16))


def _pick_distinct(rng: random.Random, pool: Sequence[str], current: str | None) -> str:
    cur = (current or "").strip().lower()
    choices = [p for p in pool if p.lower() != cur]
    if not choices:
        choices = list(pool)
    return rng.choice(choices)


def _baseline_seed_idea(product_id: str | None) -> Idea:
    """Synthetic parent when no ideas exist (same contract as previous generate_mutation)."""
    return Idea(
        idea_id=new_idea_id("idea"),
        title="Baseline: ship a thin vertical slice",
        description="Ship smallest measurable slice; instrument one funnel metric.",
        type=IdeaType.EXPLORE,
        source=IdeaSource.MUTATION,
        novelty_score=0.0,
        adjacency_score=0.0,
        expected_value_score=0.0,
        confidence_score=0.0,
        cost_estimate="small",
        channel_type="web",
        monetization_type="subscription",
        rationale="Mutation seed when no ideas exist.",
        product_id=product_id,
    )


def _mutate_one(
    base: Idea,
    rng: random.Random,
    *,
    weird_ratio: float,
    index: int,
    seed: str,
    product_id: str | None,
) -> Idea:
    """Apply 2–5 dimension shifts; at least one major dimension (channel/monetization/audience) changes."""
    n_dims = rng.randint(2, len(ALL_DIMENSIONS))
    majors = [d for d in ALL_DIMENSIONS if d in MAJOR_DIMENSIONS]
    first_major = rng.choice(majors)
    rest = [d for d in ALL_DIMENSIONS if d != first_major]
    rng.shuffle(rest)
    chosen = [first_major] + rest[: max(0, n_dims - 1)]

    weird_slots: set[str] = set()
    if rng.random() < float(weird_ratio):
        weird_slots.add(rng.choice(chosen))
    # occasional extra weird dimension
    if rng.random() < float(weird_ratio) * 0.5:
        for d in chosen:
            if d not in weird_slots and rng.random() < 0.4:
                weird_slots.add(d)
                break

    axes: list[str] = []
    new_channel = base.channel_type
    new_mon = base.monetization_type
    aud: str | None = None
    fmt: str | None = None
    tim: str | None = None

    for dim in chosen:
        use_weird = dim in weird_slots or (rng.random() < 0.1 and rng.random() < weird_ratio)
        if dim == "channel":
            pool = CHANNEL_WEIRD if use_weird else CHANNEL_SANE
            new_channel = _pick_distinct(rng, pool, base.channel_type)
            axes.append(f"channel:{base.channel_type}->{new_channel}")
        elif dim == "monetization":
            pool = MONETIZATION_WEIRD if use_weird else MONETIZATION_SANE
            new_mon = _pick_distinct(rng, pool, base.monetization_type)
            axes.append(f"monetization:{base.monetization_type}->{new_mon}")
        elif dim == "audience":
            pool = AUDIENCE_WEIRD if use_weird else AUDIENCE_SANE
            aud = _pick_distinct(rng, pool, None)
            axes.append(f"audience:->{aud}")
        elif dim == "format":
            pool = FORMAT_WEIRD if use_weird else FORMAT_SANE
            fmt = _pick_distinct(rng, pool, None)
            axes.append(f"format:->{fmt}")
        elif dim == "timing":
            pool = TIMING_WEIRD if use_weird else TIMING_SANE
            tim = _pick_distinct(rng, pool, None)
            axes.append(f"timing:->{tim}")

    weird_note = " (includes at least one unconventional pivot)" if weird_slots else ""

    body_lines = [
        f"Perturbed variant of `{base.title}`. Shifts:{weird_note}",
    ]
    if "channel" in chosen:
        body_lines.append(f"- Channel: {base.channel_type} → {new_channel}")
    if "monetization" in chosen:
        body_lines.append(f"- Monetization: {base.monetization_type} → {new_mon}")
    if aud is not None:
        body_lines.append(f"- Audience focus: {aud}")
    if fmt is not None:
        body_lines.append(f"- Format: {fmt}")
    if tim is not None:
        body_lines.append(f"- Timing: {tim}")
    body_lines.append("")
    body_lines.append(f"Parent description (truncated): {base.description[:320]}")

    title = f"Mutation: {base.title[:72]}"
    desc = "\n".join(body_lines)
    it = classify_type(title, desc, source=IdeaSource.MUTATION)
    rationale = (
        f"source=mutation; axes={';'.join(axes)}; "
        f"parent={base.idea_id}; ix={index}. Not all mutations are intended as production bets."
    )
    idea = Idea(
        idea_id=new_idea_id("idea"),
        title=title,
        description=desc,
        type=it,
        source=IdeaSource.MUTATION,
        novelty_score=0.0,
        adjacency_score=0.0,
        expected_value_score=0.0,
        confidence_score=0.0,
        cost_estimate=base.cost_estimate,
        channel_type=new_channel,
        monetization_type=new_mon,
        rationale=rationale,
        product_id=product_id if product_id is not None else base.product_id,
        parent_idea_id=base.idea_id,
    )
    apply_scores(idea, seed=seed + f":m{index}")
    return idea


def mutate_ideas(
    base_ideas: list[Idea],
    *,
    count: int = 5,
    seed: str = "",
    product_id: str | None = None,
    weird_ratio: float = 0.35,
) -> list[Idea]:
    """
    Produce ``count`` mutated ideas from one or more parents.

    Randomness is **deterministic** given ``seed``, parent ``idea_id``, and mutation index.
    ``weird_ratio`` controls how often at least one dimension is drawn from the weird pools.
    """
    if count <= 0:
        return []
    if not base_ideas:
        base_ideas = [_baseline_seed_idea(product_id)]

    out: list[Idea] = []
    for i in range(count):
        base = base_ideas[i % len(base_ideas)]
        rng = _rng(seed, base.idea_id, i)
        out.append(
            _mutate_one(
                base,
                rng,
                weird_ratio=weird_ratio,
                index=i,
                seed=seed,
                product_id=product_id,
            )
        )
    return out
