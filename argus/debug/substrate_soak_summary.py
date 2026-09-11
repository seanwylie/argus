"""
Summarize recent persisted autonomous-runner sessions and optional coherence stamps (soak triage).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from argus.portfolio.artifact_coherence import artifact_coherence_dir
from argus.portfolio.autonomous_runner import portfolio_autonomous_runner_dir
from argus.portfolio.substrate_policy_state import load_substrate_policy_state

SUBSTRATE_SOAK_SUMMARY_SCHEMA = "argus.substrate_soak_summary.v1"

_SESSION_JSON = re.compile(r"^\d{8}T\d{6}Z\.json$")


def _read_json(p: Path) -> dict[str, Any] | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _session_substrate_from_payload(pl: dict[str, Any]) -> str | None:
    """Best-effort overall_status from last per-cycle row or policy block."""
    acp = pl.get("artifact_coherence_policy") or {}
    dsb = acp.get("degraded_substrate_policy") or {}
    s = dsb.get("session_substrate_overall_status")
    if isinstance(s, str) and s.strip():
        return s.strip()
    rows = pl.get("per_cycle_outcomes") or []
    if not rows:
        return None
    last = rows[-1]
    if not isinstance(last, dict):
        return None
    ac = last.get("artifact_coherence") or {}
    if isinstance(ac, dict):
        os = ac.get("overall_status")
        if isinstance(os, str) and os.strip():
            return os.strip()
    return None


def summarize_substrate_soak(
    repo_root: Path,
    *,
    limit_sessions: int = 5,
    include_coherence_stamps: int = 5,
) -> dict[str, Any]:
    """
    Inspect the last ``limit_sessions`` stamped autonomous session JSON files (by filename sort)
    and optional recent ``runs/debug/artifact_coherence/*.json`` stamps.
    """
    root = Path(repo_root).resolve()
    ar_dir = portfolio_autonomous_runner_dir(root)
    counts: dict[str, int] = {"valid": 0, "warning": 0, "degraded": 0, "invalid": 0, "unknown": 0}
    sessions: list[dict[str, Any]] = []

    stamped = sorted(
        [p for p in ar_dir.glob("*.json") if p.name != "latest.json" and _SESSION_JSON.match(p.name)],
        key=lambda p: p.name,
        reverse=True,
    )[: max(0, int(limit_sessions))]

    for p in stamped:
        pl = _read_json(p) or {}
        if str(pl.get("schema") or "") != "argus.portfolio_autonomous_runner.v1":
            continue
        sub = _session_substrate_from_payload(pl)
        if sub:
            key = sub.lower()
            if key in counts:
                counts[key] += 1
            else:
                counts["unknown"] += 1
        else:
            counts["unknown"] += 1
        try:
            rel = str(p.relative_to(root))
        except ValueError:
            rel = str(p)
        sessions.append(
            {
                "session_id": pl.get("session_id"),
                "file": rel,
                "stop_reason": pl.get("stop_reason"),
                "substrate_overall_status": sub,
            }
        )

    coh_dir = artifact_coherence_dir(root)
    coherence_recent: list[dict[str, Any]] = []
    coh_jsons = sorted(
        [p for p in coh_dir.glob("*.json") if p.name != "latest.json"],
        key=lambda p: p.name,
        reverse=True,
    )[: max(0, int(include_coherence_stamps))]
    for p in coh_jsons:
        cj = _read_json(p) or {}
        if str(cj.get("schema") or "") != "argus.artifact_coherence_report.v1":
            continue
        os = str(cj.get("overall_status") or "")
        try:
            crel = str(p.relative_to(root))
        except ValueError:
            crel = str(p)
        coherence_recent.append(
            {
                "file": crel,
                "run_id": cj.get("run_id"),
                "overall_status": os or None,
            }
        )

    st = load_substrate_policy_state(root)
    return {
        "schema": SUBSTRATE_SOAK_SUMMARY_SCHEMA,
        "repo_root": str(root),
        "sessions_inspected": len(sessions),
        "substrate_status_counts_from_sessions": counts,
        "recent_sessions_newest_first": sessions,
        "coherence_stamps_newest_first": coherence_recent,
        "substrate_policy_state": {
            "consecutive_degraded_sessions": st.get("consecutive_degraded_sessions"),
            "updated_at_utc": st.get("updated_at_utc"),
            "last_session_id": st.get("last_session_id"),
        },
    }


__all__ = [
    "SUBSTRATE_SOAK_SUMMARY_SCHEMA",
    "summarize_substrate_soak",
]
