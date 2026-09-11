"""
Advisor-assisted **expansion** of existing structured ideas (not net-new generation).

Advisors critique, suggest wording improvements, variations, risks, and monetization
angles while keeping the system-generated :class:`~argus.idea_generation.models.Idea`
record unchanged — outputs attach under bundle metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.advisors.consensus import build_consensus
from argus.advisors.models import Advisor, AdvisorArchetype, AdvisorResponse, ConsensusResult
from argus.advisors.registry import resolve_advisors
from argus.advisors.runner import run_advisors
from argus.advisors.temporal import TemporalGrounding
from argus.idea_generation.models import Idea

EXPANSION_SCHEMA = "argus.advisor_idea_expansion.v1"


def build_idea_expansion_prompt(idea: Idea) -> str:
    """
    Strict operator prompt: advisors must react to this idea only, not invent a replacement.

    Used as ``context_override`` for :func:`run_advisors` (findings-shaped section).
    """
    return (
        "ARGUS IDEA EXPANSION (read-only anchor — do not replace or invent a new primary idea).\n\n"
        f"IDEA_ID: {idea.idea_id}\n"
        f"SOURCE: {idea.source.value}\n"
        f"TITLE (canonical, do not rename): {idea.title}\n\n"
        f"DESCRIPTION (canonical):\n{idea.description}\n\n"
        f"RATIONALE (from generator): {idea.rationale}\n\n"
        f"CHANNEL: {idea.channel_type}  MONETIZATION: {idea.monetization_type}  COST: {idea.cost_estimate}\n\n"
        "TASK FOR YOUR PERSONA:\n"
        "- Suggest clearer description phrasing (optional bullets; do not assert live metrics).\n"
        "- Propose 1–2 *variations* of the same idea (adjacent scope), not a different product.\n"
        "- List concrete risks.\n"
        "- Suggest monetization or packaging tweaks aligned with this idea.\n"
        "FORBIDDEN: proposing an unrelated net-new idea; replacing TITLE; claiming live user data.\n"
    )


def _monetization_note(adv_id: str, arch: AdvisorArchetype, idea: Idea) -> str:
    return (
        f"[{adv_id}/{arch.value}] Revisit `{idea.monetization_type}` packaging and proof points "
        f"before scaling (idea `{idea.idea_id}`)."
    )


def _variation_line(idea: Idea, resp: AdvisorResponse, arch: str) -> str:
    return (
        f"[{arch}] Variation: keep core `{idea.title[:48]}` — emphasize "
        f"{resp.recommendation[:120].strip()}"
    )


def _description_tweak(resp: AdvisorResponse, arch: str) -> str:
    return f"[{arch}] Description polish: tie narrative to `{resp.recommendation[:180].strip()}`"


def _stub_expansion_payload(
    idea: Idea,
    advisors: list[Advisor],
    responses: list[AdvisorResponse],
) -> dict[str, Any]:
    improved: list[str] = []
    variations: list[str] = []
    risks: list[str] = []
    monetization: list[str] = []

    for adv, resp in zip(advisors, responses, strict=True):
        arch = adv.archetype.value
        improved.append(_description_tweak(resp, arch))
        variations.append(_variation_line(idea, resp, arch))
        for r in resp.risks or [resp.risk_assessment]:
            if r:
                risks.append(f"[{adv.id}] {r}")
        if adv.archetype in (
            AdvisorArchetype.FINANCE,
            AdvisorArchetype.INVESTOR,
            AdvisorArchetype.MARKETING,
        ):
            monetization.append(_monetization_note(adv.id, adv.archetype, idea))

    return {
        "improved_description_suggestions": improved[:12],
        "suggested_variations": variations[:12],
        "risks_highlighted": risks[:20],
        "monetization_tweaks": monetization[:12],
    }


def uncertainty_from_advisor_consensus(consensus: ConsensusResult) -> float:
    """
    Deterministic 0–1: higher when advisors disagree or consensus confidence is low.

    Does **not** replace idea confidence scores; this is an expansion-side uncertainty signal.
    """
    div = min(1.0, len(consensus.disagreement_signals) * 0.14)
    conf_gap = 1.0 - max(0.0, min(1.0, consensus.confidence_score))
    u = min(1.0, 0.45 * conf_gap + 0.55 * div)
    return round(u, 4)


@dataclass
class IdeaExpansionRecord:
    """Advisor expansion output for one idea (sidecar to the canonical Idea)."""

    idea_id: str
    improved_description_suggestions: list[str] = field(default_factory=list)
    suggested_variations: list[str] = field(default_factory=list)
    risks_highlighted: list[str] = field(default_factory=list)
    monetization_tweaks: list[str] = field(default_factory=list)
    uncertainty_from_disagreement: float = 0.0
    consensus_summary: str = ""
    consensus_confidence: float = 0.0
    disagreement_signal_count: int = 0


def expand_idea_with_advisors(
    repo_root: Path | str,
    product_id: str,
    idea: Idea,
    *,
    use_llm: bool | None = False,
) -> IdeaExpansionRecord:
    """
    Run the advisor board against a **single anchored idea** (expansion / critique only).

    ``use_llm=False`` by default so results stay deterministic in CI; enable LLM explicitly when configured.
    """
    root = Path(repo_root).resolve()
    prompt = build_idea_expansion_prompt(idea)
    advisors = resolve_advisors(root, product_id)
    run = run_advisors(
        root,
        product_id,
        advisors=advisors,
        context_override=prompt,
        use_llm=use_llm,
        log_consultation=False,
    )
    tg = run.temporal_grounding
    cons = build_consensus(
        root,
        product_id,
        advisors,
        run.responses,
        temporal_grounding=tg if isinstance(tg, TemporalGrounding) else None,
    )
    stub_payload = _stub_expansion_payload(idea, advisors, run.responses)
    u = uncertainty_from_advisor_consensus(cons)

    return IdeaExpansionRecord(
        idea_id=idea.idea_id,
        improved_description_suggestions=stub_payload["improved_description_suggestions"],
        suggested_variations=stub_payload["suggested_variations"],
        risks_highlighted=stub_payload["risks_highlighted"],
        monetization_tweaks=stub_payload["monetization_tweaks"],
        uncertainty_from_disagreement=u,
        consensus_summary=cons.disagreement_summary,
        consensus_confidence=cons.confidence_score,
        disagreement_signal_count=len(cons.disagreement_signals),
    )


def expansion_record_to_jsonable(rec: IdeaExpansionRecord) -> dict[str, Any]:
    return {
        "idea_id": rec.idea_id,
        "improved_description_suggestions": rec.improved_description_suggestions,
        "suggested_variations": rec.suggested_variations,
        "risks_highlighted": rec.risks_highlighted,
        "monetization_tweaks": rec.monetization_tweaks,
        "uncertainty_from_disagreement": rec.uncertainty_from_disagreement,
        "consensus_summary": rec.consensus_summary,
        "consensus_confidence": rec.consensus_confidence,
        "disagreement_signal_count": rec.disagreement_signal_count,
    }


def attach_advisor_expansions(
    repo_root: Path | str,
    product_id: str,
    ideas: list[Idea],
    *,
    max_ideas: int = 8,
    use_llm: bool | None = False,
) -> dict[str, Any]:
    """
    Build metadata block for :class:`~argus.idea_generation.models.IdeasBundle`.

    System-generated ideas stay primary; this only adds ``per_idea`` expansion records.
    """
    root = Path(repo_root).resolve()
    per_idea: dict[str, Any] = {}
    for idea in ideas[:max(0, max_ideas)]:
        rec = expand_idea_with_advisors(root, product_id, idea, use_llm=use_llm)
        per_idea[idea.idea_id] = expansion_record_to_jsonable(rec)

    return {
        "schema": EXPANSION_SCHEMA,
        "role": (
            "Advisors expand and critique existing ideas only; they do not replace system-generated ideas."
        ),
        "per_idea": per_idea,
    }
