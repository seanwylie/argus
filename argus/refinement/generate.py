"""Initial draft generation: deterministic + optional LLM augmentation."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.advisors.llm import extract_json_object
from argus.context import (
    bundle_to_refinement_prompt_addon,
    is_context_packets_enabled,
    refinement_grounded_bundle,
)
from argus.idea_generation.pipeline import load_latest_bundle
from argus.llm.client import LLMCompletionStatus, complete, is_llm_enabled, llm_client_from_env
from argus.products.inventory import build_inventory
from argus.products.reporting import format_product_summary
from argus.refinement.models import ArtifactDraft, ArtifactType, GeneratedBy
from argus.refinement.routing import refinement_question
from argus.strategy.apply import load_strategy_record

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _draft_id(session_id: str, round_number: int) -> str:
    return f"{session_id}_r{round_number}_draft"


def load_idea_text(repo_root: Path, idea_id: str) -> tuple[str, str, dict[str, Any]]:
    """Return title, description, structured fields from latest ideas bundle."""
    bundle = load_latest_bundle(repo_root)
    if bundle:
        for i in bundle.ideas:
            if i.idea_id == idea_id:
                sf: dict[str, Any] = {
                    "idea_id": i.idea_id,
                    "type": i.type.value,
                    "source": i.source.value,
                    "novelty_score": i.novelty_score,
                    "expected_value_score": i.expected_value_score,
                }
                if i.llm_expansion:
                    sf["llm_expansion"] = i.llm_expansion
                return i.title, i.description, sf
    return idea_id, "(idea not found in runs/ideas/latest.json — provide content via refinement meta)", {}


def load_product_context(repo_root: Path, product_id: str) -> str:
    inv = build_inventory(repo_root)
    if product_id not in inv.valid:
        return f"Product {product_id!r} not in valid inventory."
    return format_product_summary(inv.valid[product_id].node)


def build_initial_prompt(
    artifact_type: ArtifactType,
    *,
    title: str,
    body: str,
    product_id: str | None,
    repo_root: Path,
) -> str:
    q = refinement_question(artifact_type)
    strat = load_strategy_record(repo_root) or {}
    mode = strat.get("mode", "") if isinstance(strat, dict) else ""
    prod_block: str
    if is_context_packets_enabled() and product_id:
        bundle = refinement_grounded_bundle(repo_root, product_id, draft=None)
        prod_block = bundle_to_refinement_prompt_addon(bundle)
    else:
        prod = load_product_context(repo_root, product_id) if product_id else "(no product context)"
        prod_block = f"PRODUCT CONTEXT:\n{prod[:8000]}\n"
    return (
        f"ARGUS ARTIFACT DRAFT ({artifact_type.value})\n"
        f"Guiding question: {q}\n"
        f"Strategy mode (context only): {mode}\n\n"
        f"{prod_block}\n"
        f"SEED TITLE: {title}\n\n"
        f"SEED BODY:\n{body}\n\n"
        "Return a single JSON object with keys: title (string), content (string, markdown), "
        "structured_fields (object, optional metadata).\n"
        "Do not claim live metrics. Be concrete and scoped.\n"
    )


def generate_initial_draft(
    repo_root: Path,
    session_id: str,
    artifact_type: ArtifactType,
    source_id: str,
    product_id: str | None,
    *,
    round_number: int = 0,
) -> ArtifactDraft:
    root = repo_root.resolve()
    structured: dict[str, Any] = {}
    title = source_id
    body = ""

    if artifact_type == ArtifactType.IDEA:
        title, body, structured = load_idea_text(root, source_id)
    elif artifact_type == ArtifactType.PRODUCT_SPEC:
        if not product_id:
            body = "Missing product_id for product_spec refinement."
        else:
            body = load_product_context(root, product_id)
            structured["product_id"] = product_id
            title = f"Product spec draft: {product_id}"
    elif artifact_type == ArtifactType.IMPLEMENTATION_PLAN:
        body = (
            f"Implementation plan seed for `{source_id}`.\n\n"
            "1. Scope\n2. Milestones\n3. Risks\n4. Verification\n"
        )
        structured["plan_key"] = source_id
        if product_id:
            structured["product_id"] = product_id
            body = load_product_context(root, product_id) + "\n\n" + body
        title = f"Implementation plan: {source_id}"

    gen = GeneratedBy.DETERMINISTIC
    if is_llm_enabled() and llm_client_from_env() is not None:
        prompt = build_initial_prompt(
            artifact_type,
            title=title,
            body=body,
            product_id=product_id,
            repo_root=root,
        )
        res = complete(prompt, temperature=0.25)
        if res.status == LLMCompletionStatus.OK and res.text:
            try:
                data = extract_json_object(res.text)
                title = str(data.get("title", title)).strip() or title
                body = str(data.get("content", body)).strip() or body
                sf = data.get("structured_fields")
                if isinstance(sf, dict):
                    structured = {**structured, **sf}
                gen = GeneratedBy.LLM
            except (ValueError, RuntimeError) as e:
                logger.warning("Initial draft LLM parse failed, using deterministic seed: %s", e)
        else:
            logger.warning("LLM unavailable for initial draft; using deterministic seed.")

    return ArtifactDraft(
        draft_id=_draft_id(session_id, round_number),
        session_id=session_id,
        round_number=round_number,
        artifact_type=artifact_type,
        title=title,
        content=body,
        structured_fields=structured,
        created_at_utc=_now(),
        generated_by=gen,
    )


def generate_regenerated_draft(
    repo_root: Path,
    session_id: str,
    artifact_type: ArtifactType,
    previous: ArtifactDraft,
    synthesis_required: list[str],
    synthesis_blocking: list[str],
    product_id: str | None,
    *,
    round_number: int,
) -> ArtifactDraft:
    """Produce next draft explicitly addressing synthesis feedback (not a blank restart)."""
    root = repo_root.resolve()
    base_title = previous.title
    base_content = previous.content
    gen = GeneratedBy.DETERMINISTIC

    block = "\n".join(f"- {x}" for x in synthesis_blocking[:40])
    req = "\n".join(f"- {x}" for x in synthesis_required[:40])
    carry = json.dumps(previous.structured_fields, sort_keys=True)[:4000]

    manual = (
        f"# {base_title}\n\n"
        f"{base_content}\n\n"
        "## Addressed feedback (tracked)\n"
        f"Blocking items resolved in this revision:\n{block or '(none)'}\n\n"
        f"Required changes applied:\n{req or '(none)'}\n\n"
        f"Preserved structured fields summary:\n{carry}\n"
    )

    if is_llm_enabled() and llm_client_from_env() is not None:
        if is_context_packets_enabled() and product_id:
            bundle = refinement_grounded_bundle(root, product_id, previous)
            prod = bundle_to_refinement_prompt_addon(bundle)
        else:
            prod = load_product_context(root, product_id) if product_id else ""
            prod = f"PRODUCT CONTEXT (facts only):\n{prod[:6000]}\n\n"
        prompt = (
            f"Revise the following artifact for Argus ({artifact_type.value}).\n"
            "You MUST preserve valid parts of the prior draft and explicitly address each blocking "
            "and required-change item. Do not restart from scratch unless feedback demands it.\n\n"
            f"{prod}"
            f"BLOCKING ISSUES:\n{block}\n\n"
            f"REQUIRED CHANGES:\n{req}\n\n"
            f"PRIOR TITLE: {base_title}\n\n"
            f"PRIOR CONTENT:\n{base_content[:12000]}\n\n"
            "Return JSON: {title, content, structured_fields (object)}.\n"
        )
        res = complete(prompt, temperature=0.28)
        if res.status == LLMCompletionStatus.OK and res.text:
            try:
                data = extract_json_object(res.text)
                base_title = str(data.get("title", base_title)).strip() or base_title
                base_content = str(data.get("content", manual)).strip() or manual
                sf = data.get("structured_fields")
                merged = dict(previous.structured_fields)
                if isinstance(sf, dict):
                    merged.update(sf)
                gen = GeneratedBy.LLM
                return ArtifactDraft(
                    draft_id=_draft_id(session_id, round_number),
                    session_id=session_id,
                    round_number=round_number,
                    artifact_type=artifact_type,
                    title=base_title,
                    content=base_content,
                    structured_fields=merged,
                    created_at_utc=_now(),
                    generated_by=gen,
                )
            except (ValueError, RuntimeError) as e:
                logger.warning("Regeneration LLM parse failed; using deterministic merge: %s", e)

    return ArtifactDraft(
        draft_id=_draft_id(session_id, round_number),
        session_id=session_id,
        round_number=round_number,
        artifact_type=artifact_type,
        title=base_title,
        content=manual,
        structured_fields=dict(previous.structured_fields),
        created_at_utc=_now(),
        generated_by=gen,
    )
