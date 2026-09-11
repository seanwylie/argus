"""
Durable consecutive-degraded counter for conservative autonomous-runner policy.

Read/write ``runs/portfolio/autonomous_runner/substrate_policy_state.json`` only when
session artifacts are persisted — no hidden recompute.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json

PORTFOLIO_SUBSTRATE_POLICY_STATE_SCHEMA: Final = "argus.portfolio_substrate_policy_state.v1"

DEFAULT_SUPPRESS_PROMOTIONS_AFTER_CONSECUTIVE_DEGRADED: Final = 2
DEFAULT_ABORT_NEW_SESSION_AFTER_CONSECUTIVE_DEGRADED: Final = 4


def substrate_policy_state_path(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "autonomous_runner" / "substrate_policy_state.json"


def load_substrate_policy_state(repo_root: Path) -> dict[str, Any]:
    """Return last persisted state or defaults when missing/invalid."""
    p = substrate_policy_state_path(repo_root)
    if not p.is_file():
        return _defaults()
    try:
        import json

        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return _defaults()
    if str(raw.get("schema") or "") != PORTFOLIO_SUBSTRATE_POLICY_STATE_SCHEMA:
        return _defaults()
    n = raw.get("consecutive_degraded_sessions")
    try:
        streak = max(0, int(n))
    except (TypeError, ValueError):
        streak = 0
    return {
        "schema": PORTFOLIO_SUBSTRATE_POLICY_STATE_SCHEMA,
        "consecutive_degraded_sessions": streak,
        "updated_at_utc": raw.get("updated_at_utc"),
        "last_session_id": raw.get("last_session_id"),
        "last_session_substrate_overall": raw.get("last_session_substrate_overall"),
    }


def _defaults() -> dict[str, Any]:
    return {
        "schema": PORTFOLIO_SUBSTRATE_POLICY_STATE_SCHEMA,
        "consecutive_degraded_sessions": 0,
        "updated_at_utc": None,
        "last_session_id": None,
        "last_session_substrate_overall": None,
    }


def write_substrate_policy_state(repo_root: Path, payload: dict[str, Any]) -> Path:
    root = Path(repo_root).resolve()
    p = substrate_policy_state_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    pl = dict(payload)
    pl["schema"] = PORTFOLIO_SUBSTRATE_POLICY_STATE_SCHEMA
    pl["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
    p.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    return p


def compute_next_degraded_streak(
    *,
    prior_streak: int,
    stop_reason: str,
    session_substrate_overall: str | None,
) -> int:
    """
    Invalid substrate resets the streak (red-light path). Degraded increments; valid/warning resets.
    """
    if stop_reason == "artifact_coherence_invalid":
        return 0
    o = str(session_substrate_overall or "").strip().lower()
    if o == "degraded":
        return max(0, int(prior_streak)) + 1
    return 0


def build_degraded_substrate_policy_block(
    *,
    consecutive_before: int,
    consecutive_after: int,
    session_substrate_overall: str | None,
    stop_reason: str,
    suppress_promotions_after: int,
    abort_new_session_after: int,
    suppress_promotions_applied: bool,
    stopped_for_degraded_persistent: bool,
) -> dict[str, Any]:
    return {
        "schema": "argus.degraded_substrate_policy.v1",
        "consecutive_degraded_sessions_before_session": int(consecutive_before),
        "consecutive_degraded_sessions_after_session": int(consecutive_after),
        "session_substrate_overall_status": session_substrate_overall,
        "stop_reason": stop_reason,
        "thresholds": {
            "suppress_promotions_after_consecutive_degraded_sessions": int(suppress_promotions_after),
            "abort_new_session_after_consecutive_degraded_sessions": int(abort_new_session_after),
        },
        "promotions_suppressed_for_degraded_substrate": bool(suppress_promotions_applied),
        "stopped_for_degraded_persistent_substrate": bool(stopped_for_degraded_persistent),
        "conservative_mode_note": (
            "Substrate coherence was degraded for multiple consecutive persisted sessions; "
            "promotions suppressed until overall returns valid or warning."
            if suppress_promotions_applied
            else None
        ),
    }


__all__ = [
    "DEFAULT_ABORT_NEW_SESSION_AFTER_CONSECUTIVE_DEGRADED",
    "DEFAULT_SUPPRESS_PROMOTIONS_AFTER_CONSECUTIVE_DEGRADED",
    "PORTFOLIO_SUBSTRATE_POLICY_STATE_SCHEMA",
    "build_degraded_substrate_policy_block",
    "compute_next_degraded_streak",
    "load_substrate_policy_state",
    "substrate_policy_state_path",
    "write_substrate_policy_state",
]
