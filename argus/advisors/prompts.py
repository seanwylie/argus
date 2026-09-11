"""Structured prompts per advisor archetype."""

from __future__ import annotations

from argus.advisors.context import ConsultationContext
from argus.advisors.models import Advisor
from argus.advisors.temporal import TemporalGrounding
from argus.core.serialize import dumps_json

SYSTEM_BASE = """You are one independent advisor on an internal product review board for software products.

Ground rules (temporal):
- You are NOT the source of current operational truth. You only interpret evidence provided below, with explicit timestamps and freshness.
- INTERPRET the temporal data: say what it suggests given age and completeness.
- RECOMMEND actions based on current evidence only — if evidence is stale or missing, say so and prefer verification or refresh over speculation.
- IDENTIFY uncertainty explicitly: list what you cannot know from the artifacts.
- Do NOT invent metrics, live traffic, or "what is happening now" beyond what the artifacts support.

Output MUST be a single JSON object with keys:
  "recommendation": string (one short imperative sentence),
  "rationale": string (2-5 sentences),
  "risks": array of strings (specific, actionable),
  "confidence": number between 0 and 1 (your confidence in this advice given evidence quality and freshness),
  "stance": number between 0 and 1 (0 = strong preference for hold/cut risk, 1 = strong preference for invest/accelerate),
  "temporal_assumptions": array of strings (what you are assuming about time-bounded evidence; empty if none),
  "freshness_risk": string — one of "low", "moderate", "high" based on staleness/missing sources in the grounding section,
  "confidence_adjustment": number between -1 and 0 (optional negative adjustment you apply due to stale/missing data; 0 if none)

No markdown fences, only raw JSON."""


def _format_temporal_block(tg: TemporalGrounding | None) -> str:
    if tg is None:
        return (
            "## Temporal grounding\n"
            "(Not loaded — treat all product evidence as potentially stale or incomplete. "
            "Do not assume current operational state.)"
        )
    lines: list[str] = [
        "## Temporal grounding (explicit evidence bounds)",
        tg.current_vs_stale_note,
        "",
        f"**Overall freshness risk (0=best, 1=worst):** {tg.overall_freshness_risk:.3f}",
    ]
    if tg.missing_sources:
        lines.append(f"**Missing artifact sources:** {', '.join(tg.missing_sources)}")
    lines.append("")
    lines.append("### Source timestamps")
    for s in tg.sources:
        age = f"{s.age_days:.1f}d" if s.age_days is not None else "unknown"
        obs = s.observed_at_utc or "(none)"
        lines.append(
            f"- **{s.key}** [{s.status}]: observed={obs}, age={age}, "
            f"window≤{s.stale_after_days:.0f}d — {s.detail}"
        )
    if tg.collection_recency:
        lines.append("")
        lines.append(
            "### Collection recency (runs/temporal/latest — deterministic age vs reference; not advisor opinion)"
        )
        lines.append(dumps_json(tg.collection_recency, indent=None))
    lines.append("")
    lines.append("### Recent signal records (most recent first; not live telemetry)")
    lines.append(tg.recent_signals_excerpt[:12000])
    return "\n".join(lines)


def build_user_prompt(advisor: Advisor, ctx: ConsultationContext) -> str:
    """Full user message: archetype instructions + temporal grounding + structured context sections."""
    temporal_block = _format_temporal_block(ctx.temporal)
    return "\n\n".join(
        [
            f"## Advisor role\nID: {advisor.id}\nArchetype: {advisor.archetype.value}\n"
            f"Focus: {advisor.description}\n"
            f"Guidance: {advisor.prompt_template}",
            temporal_block,
            "## Product summary (from product.yaml)\n" + _json_block(ctx.product_summary),
            "## Findings\n" + ctx.findings_section,
            "## Trends (from local trend analysis if available)\n" + ctx.trends_section,
            "## Latest decisions snapshot\n" + ctx.decisions_section,
            "## Experiments\n" + ctx.experiments_section,
            "## Declared doctrine (machine-readable policy)\n" + ctx.doctrine_section,
            "## Argus platform self-improvement (optional; planning-only)\n"
            + (
                ctx.self_improvement_section
                if ctx.self_improvement_section.strip()
                else "(No runs/self_improvement/plan_latest.json — run `argus self improve plan`.)"
            ),
        ]
    )


def build_chat_messages(advisor: Advisor, ctx: ConsultationContext) -> list[dict[str, str]]:
    """OpenAI-style chat messages: system + user."""
    return [
        {"role": "system", "content": SYSTEM_BASE},
        {"role": "user", "content": build_user_prompt(advisor, ctx)},
    ]


def _json_block(obj: object) -> str:
    from argus.core.serialize import dumps_json

    try:
        return dumps_json(obj, indent=None)
    except TypeError:
        return str(obj)
