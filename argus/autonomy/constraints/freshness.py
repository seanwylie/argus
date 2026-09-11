"""
Freshness escalation hooks for autonomous execution policy.

When decision metadata marks ``freshness_escalation`` (stale **operational**
signals — metrics/analytics/cost/health — for a freshness-gated finding; see
``argus.decision.freshness``), experiment-linked actions should not run autonomously.
"""

from __future__ import annotations

from typing import Any, Mapping

from argus.actions.models import ActionContract
from argus.decision.intents import DecisionIntent


def freshness_autonomy_blockers(
    decision_metadata: Mapping[str, Any],
    contract: ActionContract,
) -> list[str]:
    """Return extra autonomy blockers derived from decision freshness metadata."""
    if not decision_metadata.get("freshness_escalation"):
        return []
    intent = str(decision_metadata.get("intent") or "")
    if intent != DecisionIntent.LAUNCH_EXPERIMENT.value:
        return []
    ex = (contract.experiment_id or "").strip()
    if not ex:
        return []
    return [
        "freshness_escalation: operational signals for this experiment-class decision are stale; "
        "requires human approval before autonomous execution",
    ]


def should_escalate_for_freshness(metadata: Mapping[str, Any]) -> bool:
    """Whether decision metadata requests human escalation on freshness grounds."""
    return bool(metadata.get("freshness_escalation"))
