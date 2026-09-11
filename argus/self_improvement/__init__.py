"""Self-improvement layer: analysis and planning for evolving Argus (no auto-execution)."""

from argus.self_improvement.escalation import risky_self_improvement_items
from argus.self_improvement.findings import generate_self_findings
from argus.self_improvement.planning import (
    append_self_improvement_weekly_section,
    build_self_improvement_plan,
    load_latest_plan_json,
    prompt_excerpt_for_advisors,
    write_plan_artifacts,
)
from argus.self_improvement.prioritize import rank_proposals
from argus.self_improvement.propose import proposals_from_findings

__all__ = [
    "append_self_improvement_weekly_section",
    "build_self_improvement_plan",
    "generate_self_findings",
    "load_latest_plan_json",
    "prompt_excerpt_for_advisors",
    "proposals_from_findings",
    "rank_proposals",
    "risky_self_improvement_items",
    "write_plan_artifacts",
]
