"""
Soft, inspectable lifecycle context for bounded autonomy (scheduler / autonomous runner).

Uses portfolio lifecycle synthesis fields only — no orchestration gating, no queue score mutation.
"""

from __future__ import annotations

from typing import Any

from argus.portfolio.lifecycle import PORTFOLIO_LIFECYCLE_SCHEMA
from argus.portfolio.strategy_influence import describe_soft_influence

LIFECYCLE_SESSION_INFLUENCE_SCHEMA = "argus.portfolio_lifecycle_session_influence.v1"

# --- Tunable thresholds (bounded heuristics; conservative when ambiguous) ---
_MIN_TOTAL_FOR_RATIOS = 3
_REPAIR_HEAVY_ABS = 2
_REPAIR_HEAVY_RATIO = 0.22
_RETIREMENT_HEAVY_ABS = 2
_RETIREMENT_HEAVY_RATIO = 0.2
_CREATE_HEAVY_ABS = 2
_MIXED_UNCLEAR_HEAVY_ABS = 2
_MIXED_UNCLEAR_RATIO = 0.35
_SPARSE_TOTAL = 3


def _total_tracked(counts: dict[str, int]) -> int:
    return max(0, sum(int(v) for v in counts.values()))


def _denom_activeish(counts: dict[str, int]) -> int:
    """Rough denominator for ratio heuristics (excludes pure proposed concepts when tiny)."""
    keys = ("active", "incubating", "repairing", "harvesting", "retiring", "mixed_or_unclear", "archived_candidate")
    return max(1, sum(int(counts.get(k, 0)) for k in keys))


def classify_lifecycle_pressure(
    lifecycle_payload: dict[str, Any] | None,
) -> str:
    """
    Return a single primary signal for session UX: ``create_heavy`` | ``repair_heavy`` |
    ``retirement_heavy`` | ``mixed_sparse`` | ``neutral``.
    """
    if not lifecycle_payload or str(lifecycle_payload.get("schema") or "") != PORTFOLIO_LIFECYCLE_SCHEMA:
        return "neutral"

    counts = {k: int(v) for k, v in (lifecycle_payload.get("lifecycle_counts") or {}).items()}
    total = _total_tracked(counts)
    denom = _denom_activeish(counts)

    n_repair = int(counts.get("repairing", 0))
    n_mixed = int(counts.get("mixed_or_unclear", 0))
    n_create = int(counts.get("incubating", 0)) + int(counts.get("proposed", 0))
    n_exit_lane = (
        int(counts.get("retiring", 0))
        + int(counts.get("archived_candidate", 0))
        + int(counts.get("harvesting", 0))
    )

    entering = lifecycle_payload.get("products_entering") or []
    retpress = lifecycle_payload.get("products_under_retirement_pressure") or []
    posture = str(lifecycle_payload.get("portfolio_strategy_posture") or "").strip().lower()

    # 1) Repair-first when lane pressure is clear
    if n_repair >= _REPAIR_HEAVY_ABS or (
        denom >= _MIN_TOTAL_FOR_RATIOS and n_repair / denom >= _REPAIR_HEAVY_RATIO
    ):
        return "repair_heavy"

    # 2) Exit / deprecation cleanup
    if (
        len(retpress) >= _RETIREMENT_HEAVY_ABS
        or n_exit_lane >= _RETIREMENT_HEAVY_ABS
        or (
            denom >= _MIN_TOTAL_FOR_RATIOS
            and n_exit_lane / denom >= _RETIREMENT_HEAVY_RATIO
        )
    ):
        return "retirement_heavy"

    # 3) Creation / entering — before generic sparse (small portfolios can be mostly incubating/proposed)
    if n_create >= _CREATE_HEAVY_ABS or (
        posture == "create"
        and (n_create >= 1 or len(entering) >= 1)
    ):
        return "create_heavy"

    # 4) Sparse or ambiguous — conservative
    if total <= _SPARSE_TOTAL or (
        denom >= _MIN_TOTAL_FOR_RATIOS
        and n_mixed >= _MIXED_UNCLEAR_HEAVY_ABS
        and n_mixed / denom >= _MIXED_UNCLEAR_RATIO
    ):
        return "mixed_sparse"

    if n_mixed >= 1:
        return "mixed_sparse"

    return "neutral"


def build_lifecycle_session_influence(
    lifecycle_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Build a bounded, JSON-serializable influence object for scheduler / autonomous sessions.

    Does not change orchestration, eligibility, or queue math — notes and hints for operators only.
    """
    empty: dict[str, Any] = {
        "schema": LIFECYCLE_SESSION_INFLUENCE_SCHEMA,
        "primary_signal": "neutral",
        "inputs_snapshot": {},
        "session_notes": ["No portfolio lifecycle payload — influence is neutral (backward compatible)."],
        "priority_hints": ["Follow default operator queue ordering until lifecycle synthesis is available."],
        "stop_continue_context": {
            "bias": "conservative",
            "note": "Insufficient lifecycle context; prefer short sessions and manual review.",
        },
        "strategy_alignment": describe_soft_influence(None),
        "bounded": True,
    }

    if not lifecycle_payload or str(lifecycle_payload.get("schema") or "") != PORTFOLIO_LIFECYCLE_SCHEMA:
        return empty

    counts = {k: int(v) for k, v in (lifecycle_payload.get("lifecycle_counts") or {}).items()}
    entering = list(lifecycle_payload.get("products_entering") or [])
    exiting = list(lifecycle_payload.get("products_exiting") or [])
    rrepair = list(lifecycle_payload.get("products_under_repair_pressure") or [])
    rret = list(lifecycle_payload.get("products_under_retirement_pressure") or [])
    posture = lifecycle_payload.get("portfolio_strategy_posture")

    snapshot = {
        "lifecycle_counts": counts,
        "products_entering": entering[:32],
        "products_exiting": exiting[:32],
        "products_under_repair_pressure": rrepair[:32],
        "products_under_retirement_pressure": rret[:32],
        "portfolio_strategy_posture": posture,
        "lifecycle_run_id": lifecycle_payload.get("run_id"),
    }

    primary = classify_lifecycle_pressure(lifecycle_payload)
    strat_desc = describe_soft_influence(str(posture).strip().lower() if posture else None)

    session_notes: list[str] = []
    priority_hints: list[str] = []
    stop_note: str = ""
    bias: str = "neutral"

    if primary == "create_heavy":
        session_notes.append(
            "Lifecycle signal: creation-oriented — incubating and/or proposed work is prominent relative to the portfolio."
        )
        session_notes.append(
            "Keep creation scaffolds, mission fit, and bootstrap completion visible alongside operator queue order."
        )
        priority_hints.append("Favor attention to entering lanes and proposed concepts when triaging alongside queue rank.")
        priority_hints.append("Strategy hints apply: " + strat_desc.get("downstream_hints", {}).get("creation", ""))
        stop_note = (
            "Continue while material change is visible; pause for human review if quiescence or cycle overall "
            "requests inspect/repair — creation context stays in artifacts, not a hard gate."
        )
        bias = "creation_forward"

    elif primary == "repair_heavy":
        session_notes.append(
            "Lifecycle signal: repair pressure — one or more products are in the repairing lane or under repair pressure."
        )
        session_notes.append(
            "Soft bias: de-emphasize net-new expansion appetite; stabilize blockers before exit planning."
        )
        priority_hints.append("When choosing among queue peers, weigh intervention / unblocker context over pure advance-ready rows.")
        priority_hints.append("Align with strategy `repair` hints: " + strat_desc.get("downstream_hints", {}).get("creation", ""))
        stop_note = (
            "Prefer stopping sessions on quiescence 'wait' or intervention-heavy streaks — repair context favors "
            "stability over blind repetition."
        )
        bias = "repair_first"

    elif primary == "retirement_heavy":
        session_notes.append(
            "Lifecycle signal: retirement / exit-oriented — harvesting, retiring, or archive candidates are material."
        )
        session_notes.append(
            "Highlight deprecation planning, trajectory alignment, and lifecycle cleanup — without suppressing legal advances elsewhere."
        )
        priority_hints.append("Review exit-oriented product ids alongside queue: retirement pressure is context, not a block.")
        priority_hints.append(strat_desc.get("downstream_hints", {}).get("lifecycle_cleanup", ""))
        stop_note = "Continue until guardrails fire; use outcomes + deprecation artifacts before irreversible archival."
        bias = "exit_aware"

    elif primary == "mixed_sparse":
        session_notes.append(
            "Lifecycle signal: sparse inventory and/or mixed/unclear lanes — signals are noisy."
        )
        session_notes.append("Stay conservative: short sessions, explicit human review on ambiguous trajectories.")
        priority_hints.append("Do not over-interpret queue rank alone; confirm outcomes and intervention inbox.")
        stop_note = "Favor early stop on no-material-change streak or cycle overall 'inspect' — avoid thrash."
        bias = "conservative"

    else:
        session_notes.append("Lifecycle signal: neutral — no strong lane imbalance detected in current synthesis.")
        priority_hints.append("Use operator queue and cycle summaries as primary ordering; lifecycle is advisory.")
        stop_note = "Standard scheduler / autonomous guardrails apply."
        bias = "neutral"

    # Cross-align posture vs lifecycle (inspectable only)
    align_notes: list[str] = []
    if posture == "expand" and primary == "repair_heavy":
        align_notes.append(
            "Strategy posture `expand` with lifecycle repair pressure — prefer stabilizing before scaling net-new work."
        )
    if posture == "create" and primary in ("retirement_heavy", "repair_heavy"):
        align_notes.append(
            f"Strategy posture `create` with lifecycle `{primary}` — balance new intake with existing lane pressure."
        )
    if posture == "retire" and primary == "create_heavy":
        align_notes.append(
            "Strategy posture `retire` with creation-heavy lifecycle — reconcile exit discipline with incubating/proposed work explicitly."
        )

    strategy_alignment = dict(strat_desc)
    strategy_alignment["lifecycle_primary_signal"] = primary
    strategy_alignment["alignment_notes"] = align_notes

    return {
        "schema": LIFECYCLE_SESSION_INFLUENCE_SCHEMA,
        "primary_signal": primary,
        "inputs_snapshot": snapshot,
        "session_notes": session_notes + align_notes,
        "priority_hints": priority_hints,
        "stop_continue_context": {
            "bias": bias,
            "note": stop_note,
        },
        "strategy_alignment": strategy_alignment,
        "bounded": True,
    }


__all__ = [
    "LIFECYCLE_SESSION_INFLUENCE_SCHEMA",
    "build_lifecycle_session_influence",
    "classify_lifecycle_pressure",
]
