"""Session lifecycle: create, run rounds, persist, load."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import to_jsonable
from argus.council.models import profile_to_jsonable
from argus.council.routing import member_profiles_for_artifact, profiles_from_session_meta
from argus.refinement.convergence import evaluate_convergence
from argus.refinement.convergence_narrative import build_convergence_narrative
from argus.refinement.generate import generate_initial_draft, generate_regenerated_draft
from argus.refinement.models import (
    ArtifactDraft,
    ArtifactRefinementSession,
    ArtifactType,
    ConvergenceResult,
    GeneratedBy,
    ReviewSynthesis,
    SessionStatus,
    StakeholderType,
)
from argus.refinement.persistence import (
    ensure_session_layout,
    outcome_path,
    read_json,
    update_index,
    write_json,
)
from argus.refinement.queries import (
    convergence_round_path,
    draft_path,
    reviews_path,
    session_json_path,
    synthesis_round_path,
    uncertainty_round_outcome_path,
)
from argus.refinement.review import run_council_reviews
from argus.refinement.routing import council_for
from argus.refinement.synthesize import synthesize_reviews


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_session_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    h = hashlib.sha256(f"{ts}:{datetime.now(timezone.utc).timestamp()}".encode()).hexdigest()[:8]
    return f"ref_{ts}_{h}"


def create_session(
    repo_root: Path,
    artifact_type: ArtifactType,
    source_id: str,
    *,
    product_id: str | None = None,
    max_rounds: int = 4,
) -> ArtifactRefinementSession:
    root = repo_root.resolve()
    sid = new_session_id()
    council = council_for(artifact_type)
    profiles = member_profiles_for_artifact(artifact_type)
    required = [st for st, _w, req in council if req]
    optional = [st for st, _w, req in council if not req]
    ts = _now()
    sess = ArtifactRefinementSession(
        session_id=sid,
        artifact_type=artifact_type,
        source_id=source_id,
        product_id=product_id,
        current_round=0,
        max_rounds=max_rounds,
        status=SessionStatus.DRAFT,
        required_stakeholders=required,
        optional_stakeholders=optional,
        created_at_utc=ts,
        updated_at_utc=ts,
        meta={
            "council": [(st.value, w, req) for st, w, req in council],
            "council_profiles": [profile_to_jsonable(p) for p in profiles],
            "council_schema": "argus.council_profile.v1",
        },
    )
    ensure_session_layout(root, sid)
    write_json(session_json_path(root, sid), to_jsonable(sess))
    update_index(
        root,
        {
            "session_id": sid,
            "artifact_type": artifact_type.value,
            "source_id": source_id,
            "product_id": product_id,
            "status": sess.status.value,
            "current_round": 0,
            "updated_at_utc": ts,
        },
    )
    return sess


def load_session(repo_root: Path, session_id: str) -> ArtifactRefinementSession | None:
    p = session_json_path(repo_root, session_id)
    raw = read_json(p)
    if not raw:
        return None
    return _session_from_dict(raw)


def _session_from_dict(raw: dict[str, Any]) -> ArtifactRefinementSession:
    req = [StakeholderType(str(x)) for x in (raw.get("required_stakeholders") or [])]
    opt = [StakeholderType(str(x)) for x in (raw.get("optional_stakeholders") or [])]
    return ArtifactRefinementSession(
        session_id=str(raw["session_id"]),
        artifact_type=ArtifactType(str(raw["artifact_type"])),
        source_id=str(raw["source_id"]),
        product_id=str(raw["product_id"]) if raw.get("product_id") else None,
        current_round=int(raw.get("current_round", 0)),
        max_rounds=int(raw.get("max_rounds", 4)),
        status=SessionStatus(str(raw.get("status", "draft"))),
        required_stakeholders=req,
        optional_stakeholders=opt,
        created_at_utc=str(raw.get("created_at_utc", "")),
        updated_at_utc=str(raw.get("updated_at_utc", "")),
        meta=raw.get("meta") if isinstance(raw.get("meta"), dict) else {},
    )


def _save_session(repo_root: Path, sess: ArtifactRefinementSession) -> None:
    sess.updated_at_utc = _now()
    ensure_session_layout(repo_root, sess.session_id)
    write_json(session_json_path(repo_root, sess.session_id), to_jsonable(sess))
    update_index(
        repo_root,
        {
            "session_id": sess.session_id,
            "artifact_type": sess.artifact_type.value,
            "source_id": sess.source_id,
            "product_id": sess.product_id,
            "status": sess.status.value,
            "current_round": sess.current_round,
            "updated_at_utc": sess.updated_at_utc,
        },
    )


def _load_draft(path: Path) -> ArtifactDraft | None:
    raw = read_json(path)
    if not raw:
        return None
    return _draft_from_dict(raw)


def _draft_from_dict(raw: dict[str, Any]) -> ArtifactDraft:
    return ArtifactDraft(
        draft_id=str(raw["draft_id"]),
        session_id=str(raw["session_id"]),
        round_number=int(raw["round_number"]),
        artifact_type=ArtifactType(str(raw["artifact_type"])),
        title=str(raw.get("title", "")),
        content=str(raw.get("content", "")),
        structured_fields=raw.get("structured_fields") if isinstance(raw.get("structured_fields"), dict) else {},
        created_at_utc=str(raw.get("created_at_utc", "")),
        generated_by=GeneratedBy(str(raw.get("generated_by", "deterministic"))),
    )


def run_refinement_cycle(repo_root: Path, session_id: str) -> tuple[ArtifactRefinementSession, ConvergenceResult]:
    """
    One full cycle: ensure draft → council reviews → synthesis → convergence.

    If not converged, regenerates next-round draft and increments ``current_round``.
    """
    root = repo_root.resolve()
    sess = load_session(root, session_id)
    if sess is None:
        raise ValueError(f"Unknown session: {session_id!r}")
    if sess.status in (
        SessionStatus.APPROVED,
        SessionStatus.APPROVED_WITH_RISKS,
        SessionStatus.REJECTED,
        SessionStatus.HUMAN_REVIEW_REQUIRED,
    ):
        raise ValueError(f"Session is terminal: {sess.status.value}")

    council = council_for(sess.artifact_type)
    profiles = profiles_from_session_meta(sess.meta) or member_profiles_for_artifact(sess.artifact_type)
    ddir = draft_path(root, session_id, sess.current_round)
    draft: ArtifactDraft | None = _load_draft(ddir)

    if draft is None:
        if sess.current_round == 0:
            draft = generate_initial_draft(
                root,
                session_id,
                sess.artifact_type,
                sess.source_id,
                sess.product_id,
                round_number=0,
            )
        else:
            prev = _load_draft(draft_path(root, session_id, sess.current_round - 1))
            syn_raw = read_json(synthesis_round_path(root, session_id, sess.current_round - 1))
            if prev is None or syn_raw is None:
                raise ValueError("Missing prior draft or synthesis for regeneration")
            syn = _synthesis_from_dict(syn_raw)
            draft = generate_regenerated_draft(
                root,
                session_id,
                sess.artifact_type,
                prev,
                syn.required_changes,
                syn.blocking_issues,
                sess.product_id,
                round_number=sess.current_round,
            )
        write_json(ddir, to_jsonable(draft))

    sess.status = SessionStatus.IN_REVIEW
    _save_session(root, sess)

    reviews = run_council_reviews(
        root,
        session_id,
        draft,
        sess.artifact_type,
        sess.product_id,
        council,
        member_profiles=profiles,
    )
    rp = reviews_path(root, session_id, draft.round_number)
    write_json(rp, {"reviews": [to_jsonable(r) for r in reviews]})

    syn = synthesize_reviews(session_id, draft.draft_id, draft.round_number, reviews)
    sp = synthesis_round_path(root, session_id, draft.round_number)
    write_json(sp, to_jsonable(syn))

    conv = evaluate_convergence(
        session_id=session_id,
        round_number=draft.round_number,
        max_rounds=sess.max_rounds,
        artifact_type=sess.artifact_type,
        reviews=reviews,
        council=council,
    )
    conv = replace(
        conv,
        narrative=build_convergence_narrative(reviews=reviews, convergence=conv, synthesis=syn),
    )
    cp = convergence_round_path(root, session_id, draft.round_number)
    write_json(cp, to_jsonable(conv))

    # Uncertainty signal for downstream (decision layer)
    disagreement = sum(1 for r in reviews if r.verdict.value != "pass")
    write_json(
        uncertainty_round_outcome_path(root, session_id, draft.round_number),
        {
            "schema": "argus.refinement_uncertainty_signal.v1",
            "session_id": session_id,
            "product_id": sess.product_id,
            "round": draft.round_number,
            "stakeholder_disagreement_count": disagreement,
            "weighted_confidence": conv.weighted_confidence,
        },
    )

    if conv.converged:
        sess.status = conv.final_status
        write_json(outcome_path(root, session_id), {**to_jsonable(conv), "session_id": session_id})
    else:
        sess.status = SessionStatus.REFINING
        sess.current_round = sess.current_round + 1

    _save_session(root, sess)
    return sess, conv


def _synthesis_from_dict(raw: dict[str, Any]) -> ReviewSynthesis:
    ti = raw.get("theme_items")
    theme_items = [dict(x) for x in ti] if isinstance(ti, list) else []
    return ReviewSynthesis(
        session_id=str(raw["session_id"]),
        draft_id=str(raw["draft_id"]),
        round_number=int(raw["round_number"]),
        themes=list(raw.get("themes") or []),
        blocking_issues=list(raw.get("blocking_issues") or []),
        non_blocking_issues=list(raw.get("non_blocking_issues") or []),
        required_changes=list(raw.get("required_changes") or []),
        optional_improvements=list(raw.get("optional_improvements") or []),
        overall_signal=str(raw.get("overall_signal", "")),
        created_at_utc=str(raw.get("created_at_utc", "")),
        theme_items=theme_items,
    )


def approve_session(repo_root: Path, session_id: str) -> ArtifactRefinementSession:
    """Operator manual approval (override)."""
    sess = load_session(repo_root, session_id)
    if sess is None:
        raise ValueError(f"Unknown session: {session_id!r}")
    sess.status = SessionStatus.APPROVED
    _save_session(repo_root, sess)
    return sess


def retry_session(repo_root: Path, session_id: str) -> ArtifactRefinementSession:
    """Re-open a terminal session for another refinement round (does not delete history)."""
    sess = load_session(repo_root, session_id)
    if sess is None:
        raise ValueError(f"Unknown session: {session_id!r}")
    if sess.status not in (
        SessionStatus.HUMAN_REVIEW_REQUIRED,
        SessionStatus.REJECTED,
        SessionStatus.APPROVED_WITH_RISKS,
    ):
        raise ValueError("retry only from human_review_required, rejected, or approved_with_risks")
    sess.status = SessionStatus.REFINING
    sess.current_round = sess.current_round + 1
    _save_session(repo_root, sess)
    return sess
