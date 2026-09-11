"""Single source of truth for refinement artifact paths and lightweight session queries (read-only).

All filesystem layout for ``runs/refinement/<session_id>/`` (drafts, reviews, reviews_in, rounds)
should go through this module so orchestration and refinement stay aligned.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.refinement.models import RefinementSessionSnap, SessionStatus
from argus.refinement.persistence import read_json, session_dir


def session_json_path(repo_root: Path, session_id: str) -> Path:
    """``<session_id>/session.json`` (session record)."""
    return session_dir(repo_root, session_id) / "session.json"


def draft_path(repo_root: Path, session_id: str, round_number: int) -> Path:
    """Per-round draft artifact: ``drafts/round_<n>.json``."""
    return session_dir(repo_root, session_id) / "drafts" / f"round_{round_number}.json"


def reviews_path(repo_root: Path, session_id: str, round_number: int) -> Path:
    """Emitted council reviews: ``reviews/round_<n>.json``."""
    return session_dir(repo_root, session_id) / "reviews" / f"round_{round_number}.json"


def reviews_in_round_path(repo_root: Path, session_id: str, round_number: int) -> Path:
    """File-based CURSOR reviews input: ``reviews_in/round_<n>.json``."""
    return session_dir(repo_root, session_id) / "reviews_in" / f"round_{round_number}.json"


# Backward-compatible alias (orchestration historically used this name in ``artifact_snapshot``).
reviews_in_path = reviews_in_round_path


def synthesis_round_path(repo_root: Path, session_id: str, round_number: int) -> Path:
    """``synthesis/round_<n>.json``."""
    return session_dir(repo_root, session_id) / "synthesis" / f"round_{round_number}.json"


def convergence_round_path(repo_root: Path, session_id: str, round_number: int) -> Path:
    """``convergence/round_<n>.json``."""
    return session_dir(repo_root, session_id) / "convergence" / f"round_{round_number}.json"


def uncertainty_round_outcome_path(repo_root: Path, session_id: str, round_number: int) -> Path:
    """Per-round uncertainty sidecar under ``outcomes/`` (not ``outcomes/latest.json``)."""
    return session_dir(repo_root, session_id) / "outcomes" / f"uncertainty_round_{round_number}.json"


def load_reviews_round(repo_root: Path, session_id: str, round_number: int) -> dict[str, Any] | None:
    p = reviews_path(repo_root, session_id, round_number)
    return read_json(p)


def load_convergence_round(repo_root: Path, session_id: str, round_number: int) -> dict[str, Any] | None:
    p = convergence_round_path(repo_root, session_id, round_number)
    return read_json(p)


def reviews_round_has_blocking_grounded(repo_root: Path, session_id: str, round_number: int) -> bool:
    """
    True if any review in ``reviews/round_<n>.json`` has ``blocking`` and is not an outsider seat.

    Matches convergence gate semantics: outsider reviews do not satisfy a grounded blocking objection.
    """
    raw = load_reviews_round(repo_root, session_id, round_number)
    if not raw:
        return False
    revs = raw.get("reviews")
    if not isinstance(revs, list):
        return False
    for rev in revs:
        if not isinstance(rev, dict):
            continue
        if not bool(rev.get("blocking")):
            continue
        cm = str(rev.get("council_mode") or "").strip()
        if cm == "outsider":
            continue
        return True
    return False


def refinement_not_converged_stuck(
    repo_root: Path,
    sess: RefinementSessionSnap,
) -> bool:
    """
    True when session is still ``refining`` at or past max rounds and last round's convergence
    did not converge (durable artifact says iteration exhausted without approval).
    """
    if sess.status != SessionStatus.REFINING.value:
        return False
    if sess.current_round < sess.max_rounds:
        return False
    last_round = max(0, sess.max_rounds - 1)
    raw = load_convergence_round(repo_root, sess.session_id, last_round)
    if not raw:
        return False
    return raw.get("converged") is False


def refinement_cycle_incomplete(
    repo_root: Path,
    sess: RefinementSessionSnap,
) -> bool:
    """
    True when a draft exists for current_round but reviews for that round are not written —
    typically interrupted run or awaiting operator to place ``reviews_in`` before ``refine run``.

    When ``reviews_in`` for the round is already present, the cycle is not considered incomplete:
    ``refinement_run`` can consume ``reviews_in`` and emit ``reviews/`` in one step.
    """
    r = sess.current_round
    dp = draft_path(repo_root, sess.session_id, r)
    rp = reviews_path(repo_root, sess.session_id, r)
    rip = reviews_in_round_path(repo_root, sess.session_id, r)
    if not dp.is_file():
        return False
    if rip.is_file():
        return False
    return not rp.is_file()


def refinement_reviews_in_missing(
    repo_root: Path,
    sess: RefinementSessionSnap,
) -> bool:
    """
    True when draft exists, session is in_review, reviews not written, and ``reviews_in`` absent.

    Typical CURSOR workflow: operator adds ``reviews_in`` before ``refine run`` can emit ``reviews/``.
    """
    r = sess.current_round
    if sess.status != SessionStatus.IN_REVIEW.value:
        return False
    dp = draft_path(repo_root, sess.session_id, r)
    rp = reviews_path(repo_root, sess.session_id, r)
    rip = reviews_in_round_path(repo_root, sess.session_id, r)
    return dp.is_file() and not rp.is_file() and not rip.is_file()
