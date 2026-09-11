"""
Deprecated compatibility shim for the old ``argus.planner`` name.

The canonical planning subsystem is :mod:`argus.planning` (weekly synthesis, planning actions,
``runs/planning/``). Import from ``argus.planning`` or use the ``argus planning`` CLI.
"""

from __future__ import annotations

import warnings

warnings.warn(
    "argus.planner is deprecated; use argus.planning (e.g. build_weekly_plan) or `argus planning`.",
    DeprecationWarning,
    stacklevel=2,
)

from argus.planning import (  # noqa: E402
    PlanningActionsBundle,
    WeeklyPlan,
    build_planning_actions,
    build_weekly_plan,
)

__all__ = [
    "PlanningActionsBundle",
    "WeeklyPlan",
    "build_planning_actions",
    "build_weekly_plan",
]
