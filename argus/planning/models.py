"""Weekly portfolio plan (analysis only; no execution)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from argus.actions.models import ActionContract


@dataclass
class ProductPlanLine:
    """One product line in the weekly plan."""

    product_id: str
    recommended_next_step: str
    rationale: str


@dataclass
class WeeklyPlan:
    """
    Synthesized operating plan for the next review window.

    All lists are deterministic for a given repo state.
    """

    generated_at_utc: str
    planning_window: str
    schema: str = "argus.weekly_plan.v1"
    repo_root: str = ""

    top_portfolio_priorities: list[str] = field(default_factory=list)
    """Narrative bullets for what matters portfolio-wide."""

    per_product: list[ProductPlanLine] = field(default_factory=list)

    focus_products: list[str] = field(default_factory=list)
    """Up to 3 product ids to prioritize this week."""

    risk_focus: list[str] = field(default_factory=list)
    """Up to 3 product ids with the highest composite risk."""

    watchlist: list[str] = field(default_factory=list)
    """Products to leave alone unless signals change (low churn / low score)."""

    products_to_hold: list[str] = field(default_factory=list)
    """Stable enough to not pull focus this cycle."""

    products_to_deprecate_review: list[str] = field(default_factory=list)
    """Kill candidate or deprecate-class recommendations."""

    products_to_incubate_further: list[str] = field(default_factory=list)
    """Early-stage products needing more signal before big bets."""

    unresolved_escalations: list[dict[str, Any]] = field(default_factory=list)
    """Escalation index rows needing human judgment."""

    cost_risk_notes: list[str] = field(default_factory=list)

    trend_notes: list[str] = field(default_factory=list)
    """Short lines from ``argus trends``-style analysis over history snapshots (if any)."""

    low_effort_experiments: list[str] = field(default_factory=list)

    inventory_valid_count: int = 0
    inventory_invalid_count: int = 0


@dataclass
class PlanningActionsBundle:
    """Action contracts derived from weekly priorities, experiments, and ranked decisions."""

    schema: str = "argus.planning_actions.v1"
    generated_at_utc: str = ""
    repo_root: str = ""
    weekly_plan_at_utc: str = ""
    planning_window: str = ""
    actions: list[ActionContract] = field(default_factory=list)
    priority_action_ids: list[str] = field(default_factory=list)
    experiment_action_ids: list[str] = field(default_factory=list)
    decision_action_ids: list[str] = field(default_factory=list)
