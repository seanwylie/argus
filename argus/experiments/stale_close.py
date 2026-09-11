"""Deterministic stale-close predicates for experiment lifecycle hygiene (in-repo only)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from argus.experiments.models import Experiment, ExperimentStatus
from argus.experiments.registry import can_transition, is_terminal

# Explicit rule id for orchestration execution_detail and docs (no fuzzy policy).
STALE_CLOSE_RULE_ID = "stale_close_age_reference_clock_v1"
# Non-terminal experiments whose reference clock is at least this old vs evaluation time may close as failed.
STALE_CLOSE_MIN_AGE_DAYS = 90


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not str(ts).strip():
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def experiment_reference_clock_utc(e: Experiment) -> datetime | None:
    """
    Reference instant for age: max(created_at, start_at when set).

    ``start_at`` empty uses ``created_at`` only (same as min single reference).
    """
    c = _parse_iso(e.created_at)
    s = _parse_iso(e.start_at) if e.start_at and str(e.start_at).strip() else None
    if c is None and s is None:
        return None
    if c is None:
        return s
    if s is None:
        return c
    return max(c, s)


def experiment_eligible_for_stale_close(e: Experiment, now: datetime) -> bool:
    """True when ``e`` is non-terminal, may move to failed, and age threshold is met."""
    if is_terminal(e.status):
        return False
    if not can_transition(e.status, ExperimentStatus.FAILED):
        return False
    ref = experiment_reference_clock_utc(e)
    if ref is None:
        return False
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    n = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return (n - ref) >= timedelta(days=STALE_CLOSE_MIN_AGE_DAYS)
