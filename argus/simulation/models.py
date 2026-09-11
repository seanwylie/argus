"""Scenario simulation outputs (deterministic heuristics; no ML)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ScenarioKind(StrEnum):
    BEST = "best_case"
    EXPECTED = "expected_case"
    WORST = "worst_case"


@dataclass
class ScenarioOutcome:
    """One branch: metric deltas, cost deltas, qualitative risks."""

    kind: ScenarioKind
    label: str
    metric_changes: dict[str, str]
    cost_changes: dict[str, str]
    risk_notes: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    """Full preview for one product (optional experiment context)."""

    product_id: str
    generated_at_utc: str
    repo_root: str
    baseline_metrics: dict[str, Any]
    product_stage: str
    monthly_cost_usd: float | None
    monthly_cap_usd: float | None
    experiment_id: str | None
    experiment_hypothesis: str | None
    experiment_type: str | None
    scenarios: list[ScenarioOutcome] = field(default_factory=list)
    methodology: str = "argus.simulation.heuristic.v1"
    schema: str = "argus.simulation_result.v1"
