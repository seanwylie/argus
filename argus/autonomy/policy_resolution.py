"""Deterministic precedence when doctrine, safety, autonomy, strategy, and decisions disagree."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PolicyResolutionResult:
    """Outcome of layering operator policy sources."""

    precedence: tuple[str, ...]
    winning_layer: str
    rationale: str
    conflicts: list[str]


# Highest precedence first (hard safety and human policy beat automation defaults).
PRECEDENCE_ORDER: tuple[str, ...] = (
    "doctrine",
    "safety_invariants",
    "autonomy_policy",
    "strategy_mode",
    "decision_candidate",
)
# Names match ``winning_layer`` strings returned by :func:`resolve_policy_layers`.


def resolve_policy_layers(
    repo_root: Path,
    product_id: str,
    *,
    doctrine_errors: list[str] | None = None,
    safety_invariants: list[str] | None = None,
    autonomy_reasons: list[str] | None = None,
    strategy_notes: str | None = None,
    top_decision_summary: str | None = None,
) -> PolicyResolutionResult:
    """
    Produce a single deterministic narrative for “what wins” when artifacts conflict.

    Precedence (highest first): **doctrine** → **safety_invariants** → **autonomy_policy** →
    **strategy_mode** → **decision_candidate**.

    This does not mutate files; it is for operator visibility (dashboard, CLI, escalation text).
    """
    conflicts: list[str] = []
    if doctrine_errors:
        conflicts.extend(f"doctrine: {e}" for e in doctrine_errors)
    if safety_invariants:
        conflicts.extend(f"safety: {e}" for e in safety_invariants)
    if autonomy_reasons:
        conflicts.extend(f"autonomy: {r}" for r in autonomy_reasons)

    # Winning layer: first layer with a blocking signal in precedence order.
    if doctrine_errors:
        return PolicyResolutionResult(
            precedence=PRECEDENCE_ORDER,
            winning_layer="doctrine",
            rationale="Doctrine YAML is invalid or blocks scoring — fix `products/<id>/doctrine.yaml` before automation.",
            conflicts=conflicts,
        )

    if safety_invariants:
        return PolicyResolutionResult(
            precedence=PRECEDENCE_ORDER,
            winning_layer="safety_invariants",
            rationale="Hard safety invariants block automation — resolve listed issues before strategy or decisions apply.",
            conflicts=conflicts,
        )

    if autonomy_reasons and any("OFF" in r or "forbidden" in r for r in autonomy_reasons):
        return PolicyResolutionResult(
            precedence=PRECEDENCE_ORDER,
            winning_layer="autonomy_policy",
            rationale="Autonomy policy hard-denies this path — adjust mode/tier or approvals.",
            conflicts=conflicts,
        )

    if strategy_notes and "exploration" in strategy_notes.lower():
        return PolicyResolutionResult(
            precedence=PRECEDENCE_ORDER,
            winning_layer="strategy_mode",
            rationale="Strategy mode adjusts weights and uncertainty posture — decisions remain subordinate to doctrine and autonomy.",
            conflicts=conflicts,
        )

    if top_decision_summary:
        return PolicyResolutionResult(
            precedence=PRECEDENCE_ORDER,
            winning_layer="decision_candidate",
            rationale="No higher-priority blockers — follow latest decision artifacts under `runs/decisions/latest/`.",
            conflicts=conflicts,
        )

    return PolicyResolutionResult(
        precedence=PRECEDENCE_ORDER,
        winning_layer="safety_invariants",
        rationale="Default safe path: collect signals/findings and review autonomy gates before execution.",
        conflicts=conflicts,
    )


def resolution_to_jsonable(r: PolicyResolutionResult) -> dict[str, Any]:
    return {
        "schema": "argus.policy_resolution.v1",
        "precedence": list(r.precedence),
        "winning_layer": r.winning_layer,
        "rationale": r.rationale,
        "conflicts": list(r.conflicts),
    }
