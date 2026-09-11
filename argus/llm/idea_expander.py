"""LLM-assisted enrichment of deterministic :class:`~argus.idea_generation.models.Idea` records (additive only)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from argus.advisors.llm import extract_json_object
from argus.idea_generation.models import Idea
from argus.llm.client import (
    LLMCompletionResult,
    LLMCompletionStatus,
    complete,
    is_llm_enabled,
    llm_client_from_env,
)

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"


def _load_template(name: str) -> str:
    p = _PROMPTS_DIR / name
    return p.read_text(encoding="utf-8")


def _idea_block(idea: Idea) -> str:
    return (
        f"idea_id: {idea.idea_id}\n"
        f"title: {idea.title}\n"
        f"description: {idea.description}\n"
        f"type: {idea.type.value}\n"
        f"source: {idea.source.value}\n"
        f"rationale: {idea.rationale}\n"
        f"channel_type: {idea.channel_type}\n"
        f"monetization_type: {idea.monetization_type}\n"
    )


def expand_idea_with_llm(
    idea: Idea,
    *,
    product_id: str | None = None,
    context_prompt_prefix: str = "",
) -> Idea:
    """
    Attach ``idea.llm_expansion`` dict when LLM succeeds.

    Does not modify ``title``, ``description``, or scores — only extends ``llm_expansion``.
    When LLM is off or misconfigured, leaves ``llm_expansion`` unset (callers gate calls).
    """
    if not is_llm_enabled() or llm_client_from_env() is None:
        return idea

    template = _load_template("idea_expansion.txt")
    prompt = template.replace("{{IDEA_BLOCK}}", _idea_block(idea))
    if product_id:
        prompt = f"product_id: {product_id}\n\n" + prompt
    if context_prompt_prefix:
        prompt = context_prompt_prefix + prompt

    result = complete(prompt)
    return _apply_expansion_result(idea, result)


def _apply_expansion_result(idea: Idea, result: LLMCompletionResult) -> Idea:
    if result.status != LLMCompletionStatus.OK or not result.text:
        idea.llm_expansion = {
            "status": result.status.value,
            "advisory": True,
        }
        return idea
    try:
        data = extract_json_object(result.text)
    except (ValueError, RuntimeError) as e:
        logger.warning("Idea expansion JSON parse failed: %s", e)
        idea.llm_expansion = {"status": "parse_error", "advisory": True}
        return idea

    exp = str(data.get("expanded_description", "")).strip()
    imp = data.get("improved_title")
    improved = str(imp).strip() if imp is not None else ""
    ex_raw = data.get("concrete_examples")
    mon_raw = data.get("monetization_suggestions")
    examples: list[str] = []
    if isinstance(ex_raw, list):
        examples = [str(x).strip() for x in ex_raw if str(x).strip()]
    monetization: list[str] = []
    if isinstance(mon_raw, list):
        monetization = [str(x).strip() for x in mon_raw if str(x).strip()]

    idea.llm_expansion = {
        "status": LLMCompletionStatus.OK.value,
        "advisory": True,
        "expanded_description": exp,
        "improved_title": improved or None,
        "concrete_examples": examples[:12],
        "monetization_suggestions": monetization[:12],
    }
    return idea


def expand_ideas_if_enabled(
    ideas: list[Idea],
    *,
    product_id: str | None,
    max_ideas: int = 24,
    repo_root: Path | None = None,
) -> None:
    """
    Mutate ideas in place when LLM is configured; no-op when disabled or no key.

    When ``ARGUS_CONTEXT_PACKETS`` is on and ``repo_root`` + ``product_id`` are set, prepends the
    same purpose-aware bundle as ``idea_generation`` (including ``audit.summary`` when present) so
    expansion prompts are grounded — Step 4 audit integration for optional LLM path.
    """
    if not is_llm_enabled():
        return
    if llm_client_from_env() is None:
        return

    prefix = ""
    if repo_root is not None and product_id:
        from argus.context.assemble import assemble_context_bundle
        from argus.context.env import is_context_packets_enabled
        from argus.context.formatting import bundle_to_refinement_prompt_addon
        from argus.context.purposes import ContextPurpose

        if is_context_packets_enabled():
            bundle = assemble_context_bundle(
                repo_root,
                ContextPurpose.IDEA_GENERATION,
                product_id,
                draft=None,
            )
            prefix = bundle_to_refinement_prompt_addon(bundle) + "\n\n"

    for idea in ideas[: max(0, max_ideas)]:
        expand_idea_with_llm(
            idea,
            product_id=product_id,
            context_prompt_prefix=prefix,
        )


def llm_expansion_for_json(idea: Idea) -> dict[str, Any] | None:
    """Stable shape for serializers."""
    return idea.llm_expansion
