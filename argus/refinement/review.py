"""Stakeholder reviews: CURSOR file input, OpenAI JSON, or deterministic stubs (non-CURSOR)."""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.advisors.llm import extract_json_object
from argus.context import is_context_packets_enabled
from argus.council.backends import ReviewBackendId, resolve_active_backend
from argus.council.context_delivery import build_packet_addon_for_member
from argus.council.models import BackendType, CouncilMemberProfile, CouncilMode
from argus.council.routing import member_profiles_for_artifact
from argus.llm.client import LLMCompletionStatus, complete, is_llm_enabled, llm_client_from_env
from argus.refinement.context_cache import CycleReviewContext, load_cycle_review_context
from argus.refinement.models import (
    ArtifactDraft,
    ArtifactType,
    ObjectionCategory,
    ReviewVerdict,
    StakeholderReview,
    StakeholderType,
)
from argus.refinement.queries import reviews_in_round_path
from argus.refinement.routing import refinement_question
from argus.refinement.stakeholders import REVIEW_RUBRIC, ROLE_DESCRIPTION

logger = logging.getLogger(__name__)

_MAX_PARALLEL_REVIEWS = 8

OUTSIDER_PREAMBLE = (
    "IMPORTANT — OUTSIDER REVIEWER: You receive intentionally limited context. "
    "Do not claim repository layout, file contents, or implementation feasibility as facts. "
    "Judge pitch, interest, clarity, and market/creative pressure only.\n\n"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _review_id(session_id: str, round_number: int, stakeholder: StakeholderType) -> str:
    raw = f"{session_id}:{round_number}:{stakeholder.value}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:10]
    return f"rev_{round_number}_{stakeholder.value}_{h}"


_CURSOR_REVIEW_REQUIRED_KEYS = frozenset(
    {
        "verdict",
        "blocking",
        "confidence_score",
        "objection_categories",
        "objections",
        "suggestions",
        "rationale",
    }
)


def load_cursor_review_for_stakeholder(
    repo_root: Path,
    session_id: str,
    round_number: int,
    stakeholder: StakeholderType,
) -> dict[str, Any]:
    """
    Load ``reviews_in/round_<round_number>.json``: a single JSON object mapping
    ``stakeholder.value`` (e.g. ``product``) to a review payload.

    Each payload must include exactly the keys expected from ``build_review_prompt`` /
    OpenAI JSON output. On missing file, invalid JSON, wrong shape, or missing keys:
    raises ``ValueError`` (no stub fallback).
    """
    path = reviews_in_round_path(repo_root, session_id, round_number)
    if not path.is_file():
        raise ValueError(
            "cursor review input missing (expected file): "
            f"{path}. "
            "Create this JSON before `argus refine run`. "
            f"Use `argus refine show {session_id} --json` to read current_round "
            f"(file must be reviews_in/round_{round_number}.json for this cycle)."
        )
    try:
        raw_txt = path.read_text(encoding="utf-8")
        root = json.loads(raw_txt)
    except json.JSONDecodeError as e:
        raise ValueError(f"cursor review file invalid JSON: {path}: {e}") from e
    if not isinstance(root, dict):
        raise ValueError(
            f"cursor review file must be a JSON object mapping stakeholder id to review: {path}"
        )
    key = stakeholder.value
    inner = root.get(key)
    if inner is None:
        raise ValueError(
            f"cursor review file missing entry {key!r} (have {sorted(root.keys())!r}): {path}"
        )
    if not isinstance(inner, dict):
        raise ValueError(f"cursor review for {key!r} must be a JSON object: {path}")
    missing = _CURSOR_REVIEW_REQUIRED_KEYS - inner.keys()
    if missing:
        raise ValueError(
            f"cursor review for {key!r} missing required keys {sorted(missing)!r}: {path}"
        )
    return inner


def _review_payload_to_stakeholder(
    data: dict[str, Any],
    *,
    session_id: str,
    draft: ArtifactDraft,
    stakeholder: StakeholderType,
    council_mode_str: str,
    backend_used: str,
    llm_status: str | None,
) -> StakeholderReview:
    """Build StakeholderReview from OpenAI/Cursor JSON object (shared contract)."""
    v = str(data.get("verdict", "concern")).lower()
    verdict = ReviewVerdict(v) if v in ("pass", "concern", "fail") else ReviewVerdict.CONCERN
    blocking = bool(data.get("blocking", False))
    if council_mode_str == CouncilMode.OUTSIDER.value:
        blocking = False
    try:
        conf = float(data.get("confidence_score", 0.65))
    except (TypeError, ValueError):
        conf = 0.65
    conf = max(0.0, min(1.0, conf))
    cats = _parse_categories(data.get("objection_categories"))
    obj_raw = data.get("objections")
    objections = [str(x).strip() for x in obj_raw if str(x).strip()] if isinstance(obj_raw, list) else []
    sug_raw = data.get("suggestions")
    suggestions = [str(x).strip() for x in sug_raw if str(x).strip()] if isinstance(sug_raw, list) else []
    rationale = str(data.get("rationale", "")).strip() or "(no rationale)"
    return StakeholderReview(
        review_id=_review_id(session_id, draft.round_number, stakeholder),
        session_id=session_id,
        draft_id=draft.draft_id,
        round_number=draft.round_number,
        stakeholder_type=stakeholder,
        verdict=verdict,
        blocking=blocking,
        confidence_score=conf,
        objection_categories=cats,
        objections=objections[:24],
        suggestions=suggestions[:24],
        rationale=rationale,
        created_at_utc=_now(),
        llm_status=llm_status,
        council_mode=council_mode_str,
        backend_used=backend_used,
    )


def _parse_categories(raw: Any) -> list[ObjectionCategory]:
    out: list[ObjectionCategory] = []
    if not isinstance(raw, list):
        return out
    for x in raw:
        try:
            out.append(ObjectionCategory(str(x)))
        except ValueError:
            continue
    return out


def _map_backend(bt: BackendType) -> ReviewBackendId:
    if bt == BackendType.OPENAI:
        return ReviewBackendId.OPENAI
    if bt == BackendType.CURSOR:
        return ReviewBackendId.CURSOR
    return ReviewBackendId.DETERMINISTIC


def build_review_prompt(
    draft: ArtifactDraft,
    stakeholder: StakeholderType,
    artifact_type: ArtifactType,
    *,
    doctrine_excerpt: str,
    strategy_summary: str,
    context_packet_addon: str | None = None,
    council_mode: str | None = None,
) -> str:
    role = ROLE_DESCRIPTION.get(stakeholder, "")
    rubric = REVIEW_RUBRIC.get(stakeholder, "")
    q = refinement_question(artifact_type)
    outsider = (council_mode or "") == CouncilMode.OUTSIDER.value
    header = (
        f"You are an Argus stakeholder reviewer: {stakeholder.value}.\n"
        f"Role: {role}\nRubric: {rubric}\n"
        f"Artifact type: {artifact_type.value}. Guiding question: {q}\n"
    )
    if outsider:
        header = OUTSIDER_PREAMBLE + header
    if context_packet_addon:
        mid = context_packet_addon + "\n\n"
    elif outsider:
        mid = (
            "Limited read-only context (outsider slice):\n"
            f"Strategy (truncated):\n{strategy_summary[:1500]}\n\n"
            f"Doctrine (truncated):\n{doctrine_excerpt[:1500]}\n\n"
        )
    else:
        mid = (
            f"Strategy context (read-only; same for all reviewers this cycle):\n{strategy_summary[:6000]}\n\n"
            f"Doctrine excerpt (may be empty):\n{doctrine_excerpt[:6000]}\n\n"
        )
    return (
        f"{header}"
        f"{mid}"
        f"DRAFT TITLE: {draft.title}\n\n"
        f"DRAFT CONTENT:\n{draft.content[:14000]}\n\n"
        "Return ONE JSON object with keys:\n"
        "verdict: 'pass' | 'concern' | 'fail',\n"
        "blocking: boolean (true only if this stakeholder believes the artifact cannot proceed),\n"
        "confidence_score: number 0..1,\n"
        "objection_categories: array of strings from: "
        "monetization, novelty, distribution, scope, doctrine_alignment, technical_feasibility, "
        "architecture_risk, implementation_risk, user_value, sequencing, cost, ambiguity,\n"
        "objections: array of short strings (explicit, no vague dislike),\n"
        "suggestions: array of concrete suggestions,\n"
        "rationale: short string.\n"
        "Do not invent live metrics.\n"
    )


def collect_stakeholder_review(
    repo_root: Path,
    draft: ArtifactDraft,
    stakeholder: StakeholderType,
    artifact_type: ArtifactType,
    product_id: str | None,
    session_id: str,
    *,
    context: CycleReviewContext | None = None,
) -> StakeholderReview:
    ctx = context or load_cycle_review_context(repo_root, product_id)
    pmap = {p.stakeholder_type: p for p in member_profiles_for_artifact(artifact_type)}
    prof = pmap.get(stakeholder)
    return _collect_stakeholder_review_with_context(
        repo_root,
        draft,
        stakeholder,
        artifact_type,
        product_id,
        session_id,
        ctx,
        profile=prof,
    )


def _collect_stakeholder_review_with_context(
    repo_root: Path,
    draft: ArtifactDraft,
    stakeholder: StakeholderType,
    artifact_type: ArtifactType,
    product_id: str | None,
    session_id: str,
    ctx: CycleReviewContext,
    *,
    context_packet_addon: str | None = None,
    profile: CouncilMemberProfile | None = None,
) -> StakeholderReview:
    mode_val = profile.council_mode.value if profile else "grounded"
    prompt = build_review_prompt(
        draft,
        stakeholder,
        artifact_type,
        doctrine_excerpt=ctx.doctrine_excerpt,
        strategy_summary=ctx.strategy_summary,
        context_packet_addon=context_packet_addon,
        council_mode=mode_val,
    )

    bt = profile.backend_type if profile else BackendType.DETERMINISTIC
    council_mode_str = mode_val

    requested = _map_backend(bt)
    active = resolve_active_backend(requested)
    backend_used = active.value

    # File-based CURSOR: use ``reviews_in`` when present; if missing, fall through to the
    # resolved backend (deterministic stub / OpenAI) so ``refine run`` is not blocked.
    if bt == BackendType.CURSOR:
        rip = reviews_in_round_path(repo_root, session_id, draft.round_number)
        if rip.is_file():
            data = load_cursor_review_for_stakeholder(
                repo_root, session_id, draft.round_number, stakeholder
            )
            return _review_payload_to_stakeholder(
                data,
                session_id=session_id,
                draft=draft,
                stakeholder=stakeholder,
                council_mode_str=council_mode_str,
                backend_used="cursor",
                llm_status="file",
            )

    llm_st: str | None = None
    if active == ReviewBackendId.OPENAI and is_llm_enabled() and llm_client_from_env() is not None:
        res = complete(prompt, temperature=0.2)
        llm_st = res.status.value
        if res.status == LLMCompletionStatus.OK and res.text:
            try:
                data = extract_json_object(res.text)
                return _review_payload_to_stakeholder(
                    data,
                    session_id=session_id,
                    draft=draft,
                    stakeholder=stakeholder,
                    council_mode_str=council_mode_str,
                    backend_used=backend_used,
                    llm_status=llm_st,
                )
            except (ValueError, RuntimeError) as e:
                logger.warning("Review JSON parse failed for %s: %s", stakeholder, e)

    if active == ReviewBackendId.DETERMINISTIC:
        llm_st = llm_st or "stub"

    return _stub_review(
        draft,
        stakeholder,
        artifact_type,
        session_id,
        ctx.doctrine_excerpt,
        llm_status=llm_st or "stub",
        council_mode=council_mode_str,
        backend_used=backend_used,
    )


def _stub_review(
    draft: ArtifactDraft,
    stakeholder: StakeholderType,
    artifact_type: ArtifactType,
    session_id: str,
    doctrine_excerpt: str,
    *,
    llm_status: str,
    council_mode: str = "grounded",
    backend_used: str = "deterministic",
) -> StakeholderReview:
    """Deterministic placeholder when LLM off or parse fails — still structured."""
    h = int(
        hashlib.sha256(
            f"{draft.draft_id}:{stakeholder.value}:{draft.content[:400]}".encode()
        ).hexdigest()[:8],
        16,
    )
    verdict = ReviewVerdict.PASS
    blocking = False
    outsider = council_mode == CouncilMode.OUTSIDER.value
    if (h % 17) == 0:
        verdict = ReviewVerdict.CONCERN
    if not outsider and (h % 47) == 0 and stakeholder in (StakeholderType.FINANCE, StakeholderType.DOCTRINE):
        verdict = ReviewVerdict.FAIL
        blocking = True
    if not outsider and stakeholder == StakeholderType.DOCTRINE and doctrine_excerpt and "violation_stub" in doctrine_excerpt:
        verdict = ReviewVerdict.FAIL
        blocking = True

    objections: list[str] = []
    cats: list[ObjectionCategory] = []
    if verdict != ReviewVerdict.PASS:
        objections.append(f"[{stakeholder.value}] Deterministic review flag h%={h % 17} — tighten evidence.")
        cats.append(ObjectionCategory.AMBIGUITY)
    conf = 0.55 + (h % 40) / 100.0

    return StakeholderReview(
        review_id=_review_id(session_id, draft.round_number, stakeholder),
        session_id=session_id,
        draft_id=draft.draft_id,
        round_number=draft.round_number,
        stakeholder_type=stakeholder,
        verdict=verdict,
        blocking=blocking,
        confidence_score=round(conf, 4),
        objection_categories=cats,
        objections=objections,
        suggestions=["Add measurable acceptance criteria for the next round."],
        rationale="ARGUS-STUB: deterministic stakeholder review (enable LLM for richer critique).",
        created_at_utc=_now(),
        llm_status=llm_status,
        council_mode=council_mode,
        backend_used=backend_used,
    )


def run_council_reviews(
    repo_root: Path,
    session_id: str,
    draft: ArtifactDraft,
    artifact_type: ArtifactType,
    product_id: str | None,
    council: list[tuple[Any, float, bool]],
    *,
    member_profiles: list[CouncilMemberProfile] | None = None,
) -> list[StakeholderReview]:
    """
    Run all stakeholder reviews.

    Loads doctrine/strategy **once** per cycle, then runs reviewers **in parallel**
    (thread pool) when there is more than one stakeholder.
    """
    ctx = load_cycle_review_context(repo_root, product_id)
    profiles = member_profiles or member_profiles_for_artifact(artifact_type)
    pmap = {p.stakeholder_type: p for p in profiles}

    stakeholders: list[StakeholderType] = []
    for row in council:
        st = row[0]
        if isinstance(st, StakeholderType):
            stakeholders.append(st)

    def _addon_for(st: StakeholderType) -> str | None:
        if not is_context_packets_enabled():
            return None
        prof = pmap.get(st)
        if not prof:
            return None
        return build_packet_addon_for_member(
            repo_root,
            product_id,
            draft,
            prof.context_policy,
        )

    if not stakeholders:
        return []

    if len(stakeholders) <= 1:
        st0 = stakeholders[0]
        return [
            _collect_stakeholder_review_with_context(
                repo_root,
                draft,
                st0,
                artifact_type,
                product_id,
                session_id,
                ctx,
                context_packet_addon=_addon_for(st0),
                profile=pmap.get(st0),
            )
        ]

    workers = min(_MAX_PARALLEL_REVIEWS, len(stakeholders))

    def _one(st: StakeholderType) -> StakeholderReview:
        return _collect_stakeholder_review_with_context(
            repo_root,
            draft,
            st,
            artifact_type,
            product_id,
            session_id,
            ctx,
            context_packet_addon=_addon_for(st),
            profile=pmap.get(st),
        )

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(_one, stakeholders))
