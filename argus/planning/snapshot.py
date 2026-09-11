"""Product planning snapshots from strategy posture + decisions/surfaced evidence (deterministic, inspectable)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision.persistence import load_latest_product_decisions
from argus.findings.experiment_surfaced import (
    EXPERIMENT_SURFACED_SCHEMA,
    experiment_surfaced_latest_path,
)
from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA, strategy_latest_path

PLANNING_SNAPSHOT_SCHEMA = "argus.planning_snapshot.v1"

# posture (from strategy) → planning_mode for operators
_POSTURE_TO_PLANNING_MODE: dict[str, str] = {
    "double_down": "expand",
    "explore": "validate",
    "pivot": "redirect",
    "stabilize": "consolidate",
}


def planning_latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "planning" / "latest" / f"{product_id}.json"


def planning_generations_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "planning" / "generations"


def _norm_words(s: str) -> str:
    return " ".join(str(s).lower().split())


def _coarse_theme(action_type: str, summary: str) -> str:
    w = _norm_words(summary).split()
    head = " ".join(w[:4]) if w else "general"
    return f"{action_type}: {head}"


def _priority_workstreams(
    raw_strategy: dict[str, Any], candidates: list[dict[str, Any]]
) -> list[str]:
    """Prefer strategy ``theme_signals``; else coarse themes from decision candidates."""
    out: list[str] = []
    for row in raw_strategy.get("theme_signals") or []:
        if isinstance(row, dict) and row.get("theme"):
            out.append(str(row["theme"]))
    if out:
        return out[:8]
    for c in candidates[:8]:
        if not isinstance(c, dict):
            continue
        out.append(_coarse_theme(str(c.get("action_type") or ""), str(c.get("summary") or "")))
    return out[:8]


def _recommended_actions(
    *,
    planning_mode: str,
    workstreams: list[str],
    surfaced_n: int,
) -> list[dict[str, Any]]:
    """
    Compact next-work intent (deterministic).

    Rules (by planning_mode):
    - **expand** (double_down): ship and deepen — implementation_plan + refinement on themes.
    - **validate** (explore): learn — experiments + decision_review.
    - **redirect** (pivot): reset — decision_review + replacement experiments + narrow scope.
    - **consolidate** (stabilize): integrate — implementation_plan + low-churn refinement.
    """
    ws_hint = workstreams[0] if workstreams else "current decision themes"
    actions: list[dict[str, Any]] = []
    p = 1

    def add(
        action_type: str,
        title: str,
        reason: str,
    ) -> None:
        nonlocal p
        actions.append(
            {
                "action_type": action_type,
                "title": title,
                "reason": reason,
                "priority": p,
            }
        )
        p += 1

    if planning_mode == "expand":
        add(
            "implementation_plan",
            f"Advance delivery aligned with {ws_hint}",
            "Strategy posture is double_down — reinforce bets with concrete implementation scope.",
        )
        add(
            "refinement",
            f"Refine specs for strengthening area: {ws_hint}",
            "Tighten product/idea artifacts around concentrated themes.",
        )
        if surfaced_n >= 1:
            add(
                "experiment",
                "Monitor validation experiments on active bets",
                "Keep a thin experiment loop to confirm doubled-down assumptions.",
            )
    elif planning_mode == "validate":
        add(
            "experiment",
            "Run targeted experiments on open hypotheses",
            "Posture is explore — prioritize learning throughput over breadth.",
        )
        add(
            "decision_review",
            "Reconcile decisions with latest surfaced findings",
            "Align decision candidates with experiment evidence before scaling work.",
        )
        add(
            "refinement",
            f"Iterate on {ws_hint}",
            "Stabilize interpretation while evidence accumulates.",
        )
    elif planning_mode == "redirect":
        add(
            "decision_review",
            "Replace or retire decision candidates inconsistent with pivot",
            "Structural churn — explicitly drop or rewrite stale commitments.",
        )
        add(
            "experiment",
            "Design replacement experiments with narrower scope",
            "Pivot posture — new hypotheses, smaller blast radius.",
        )
        add(
            "implementation_plan",
            "Pause broad delivery until direction stabilizes",
            "Avoid expanding execution surface during redirect.",
        )
    else:  # consolidate
        add(
            "implementation_plan",
            "Integrate recent learnings into the next delivery slice",
            "Stabilize posture — ship coherent increments, avoid new broad bets.",
        )
        add(
            "refinement",
            f"Low-churn refinement on {ws_hint}",
            "Polish and converge rather than expand scope.",
        )
        if surfaced_n <= 1:
            add(
                "decision_review",
                "Lightweight decision hygiene (unchanged-heavy bundle)",
                "Confirm no silent drift while the system holds steady.",
            )
        else:
            add(
                "experiment",
                "Defer new experiments unless blocking gaps appear",
                "Consolidate — experiments only if they unblock integration.",
            )

    # Deterministic cap
    return actions[:6]


def build_planning_snapshot(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Build JSON-serializable planning snapshot (no writes)."""
    root = repo_root.resolve()
    pid = product_id
    sp = strategy_latest_path(root, pid)
    if not sp.is_file():
        raise ValueError("missing_strategy_latest")
    try:
        raw_st = json.loads(sp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError) as e:
        raise ValueError("invalid_strategy_latest") from e
    if not isinstance(raw_st, dict) or str(raw_st.get("schema") or "") != STRATEGY_SNAPSHOT_SCHEMA:
        raise ValueError("invalid_strategy_schema")
    if str(raw_st.get("product_id") or "") != pid:
        raise ValueError("strategy_product_mismatch")

    posture = str(raw_st.get("posture") or "stabilize")
    planning_mode = _POSTURE_TO_PLANNING_MODE.get(posture, "consolidate")

    raw_dec = load_latest_product_decisions(root, pid)
    if not isinstance(raw_dec, dict):
        raise ValueError("missing_or_invalid_decisions_latest")
    src_decisions = str(raw_dec.get("generated_at_utc") or "").strip()
    cands = [c for c in (raw_dec.get("candidates") or []) if isinstance(c, dict)]
    devo = raw_dec.get("decision_evolution") if isinstance(raw_dec.get("decision_evolution"), dict) else {}
    evo_counts = {
        "new_decisions": int(devo.get("new_count") or 0),
        "unchanged_decisions": int(devo.get("unchanged_count") or 0),
        "modified_decisions": int(devo.get("modified_count") or 0),
        "removed_decisions": int(devo.get("removed_count") or 0),
    }
    if not devo:
        evo_counts = {
            "new_decisions": len(cands),
            "unchanged_decisions": 0,
            "modified_decisions": 0,
            "removed_decisions": 0,
        }

    src_surfaced: str | None = None
    surfaced_n = 0
    sfp = experiment_surfaced_latest_path(root, pid)
    if sfp.is_file():
        try:
            raw_s = json.loads(sfp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_s = None
        if isinstance(raw_s, dict) and str(raw_s.get("schema") or "") == EXPERIMENT_SURFACED_SCHEMA:
            src_surfaced = str(raw_s.get("generated_at_utc") or "").strip() or None
            findings = raw_s.get("findings") or []
            if isinstance(findings, list):
                surfaced_n = len(findings)

    workstreams = _priority_workstreams(raw_st, cands)
    recommended = _recommended_actions(
        planning_mode=planning_mode,
        workstreams=workstreams,
        surfaced_n=surfaced_n,
    )

    evidence: dict[str, Any] = {
        "strategy_posture": posture,
        "decision_evolution_counts": evo_counts,
        "surfaced_findings_count": surfaced_n,
    }

    ts = datetime.now(timezone.utc).isoformat()
    src_strategy = str(raw_st.get("generated_at_utc") or "").strip()

    snap: dict[str, Any] = {
        "schema": PLANNING_SNAPSHOT_SCHEMA,
        "schema_version": "1",
        "product_id": pid,
        "generated_at_utc": ts,
        "source_strategy_generated_at_utc": src_strategy,
        "source_decisions_generated_at_utc": src_decisions,
        "posture": posture,
        "planning_mode": planning_mode,
        "priority_workstreams": workstreams,
        "recommended_actions": recommended,
        "evidence": evidence,
    }
    if src_surfaced:
        snap["source_experiment_surfaced_generated_at_utc"] = src_surfaced
    return snap


def save_planning_snapshot(repo_root: Path, product_id: str, snapshot: dict[str, Any]) -> Path:
    """Persist timestamped generation + latest file."""
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    gen = planning_generations_dir(root)
    gen.mkdir(parents=True, exist_ok=True)
    out = gen / f"{ts}_{product_id}.json"
    out.write_text(dumps_json(snapshot) + "\n", encoding="utf-8")
    lp = planning_latest_path(root, product_id)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(dumps_json(snapshot) + "\n", encoding="utf-8")
    return out
