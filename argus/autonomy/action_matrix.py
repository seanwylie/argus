"""Rollout action classification: categories vs autonomy tier (matrix + ``explain`` CLI)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from argus.actions.models import ActionContract
from argus.autonomy.tiers import AutonomyTier, effective_execution_tier


@dataclass(frozen=True)
class RolloutActionRow:
    """One row in the bounded-autonomy matrix."""

    key: str
    description: str
    #: Minimum **effective** tier (0–3) required for this class to be permitted.
    min_tier: AutonomyTier
    requires_approval: bool
    requires_simulation: bool
    #: Whether auto-execution is even considered (still subject to policy + safe_execution).
    auto_exec_eligible: bool
    always_forbidden: bool


# Tier 4 follows tier 3 unless ARGUS_ENABLE_TIER4 (see effective_execution_tier).
MATRIX: tuple[RolloutActionRow, ...] = (
    RolloutActionRow(
        "analysis",
        "Read-only analysis: signals, findings, doctor, dashboard, temporal summary",
        AutonomyTier.OBSERVE_ONLY,
        False,
        False,
        True,
        False,
    ),
    RolloutActionRow(
        "observe_collect",
        "Alias: collection of observe-only artifacts (same as analysis for rollout)",
        AutonomyTier.OBSERVE_ONLY,
        False,
        False,
        True,
        False,
    ),
    RolloutActionRow(
        "local_generation",
        "`argus ideas generate`, codegen stubs, local artifact generation (no subprocess)",
        AutonomyTier.SUGGEST_ONLY,
        False,
        False,
        True,
        False,
    ),
    RolloutActionRow(
        "idea_generation",
        "Structured idea bundles (`argus ideas generate`)",
        AutonomyTier.SUGGEST_ONLY,
        False,
        False,
        True,
        False,
    ),
    RolloutActionRow(
        "experiments",
        "Experiment records / propose / rank (non-subprocess)",
        AutonomyTier.SUGGEST_ONLY,
        False,
        False,
        True,
        False,
    ),
    RolloutActionRow(
        "config_change",
        "Edits to product.yaml, doctrine, metrics config (reviewable mutations)",
        AutonomyTier.SAFE_EXECUTION,
        True,
        False,
        False,
        False,
    ),
    RolloutActionRow(
        "product_scaffolding",
        "Product scaffold / autonomy spawn apply",
        AutonomyTier.SAFE_EXECUTION,
        True,
        False,
        False,
        False,
    ),
    RolloutActionRow(
        "local_execution",
        "`argus execution` / subprocess via action contracts (local repo cwd)",
        AutonomyTier.SAFE_EXECUTION,
        True,
        False,
        True,
        False,
    ),
    RolloutActionRow(
        "external_api_calls",
        "Adapters or commands reaching external HTTP/APIs",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        False,
    ),
    RolloutActionRow(
        "account_setup",
        "Creating cloud/vendor accounts or identities (human-driven)",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        True,
    ),
    RolloutActionRow(
        "account_creation",
        "Deprecated label for account_setup — always forbidden autonomously",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        True,
    ),
    RolloutActionRow(
        "monetization_actions",
        "Billing, pricing, payout, or paid feature toggles",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        False,
    ),
    RolloutActionRow(
        "publishing",
        "Publishing user-visible content or releases",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        False,
    ),
    RolloutActionRow(
        "content_publishing",
        "Alias of publishing",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        False,
    ),
    RolloutActionRow(
        "shutdown_cleanup",
        "Shutdown, archive, deprecate, wind-down moves",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        False,
    ),
    RolloutActionRow(
        "destructive_cleanup",
        "Irreversible teardown beyond archive (explicitly human-gated)",
        AutonomyTier.BOUNDED_EXECUTION,
        True,
        True,
        False,
        True,
    ),
)


def _row_by_key(key: str) -> RolloutActionRow | None:
    k = key.strip().lower().replace("-", "_")
    for row in MATRIX:
        if row.key == k:
            return row
    return None


def classify_action_contract(contract: ActionContract) -> str:
    """Map an :class:`ActionContract` to a matrix key (best-effort heuristic)."""
    at = contract.normalized_action_type()
    lt = contract.lifecycle_transition
    if lt is not None:
        ts = str(lt.to_stage).strip().lower()
        if ts in ("decline", "kill", "deprecate"):
            return "shutdown_cleanup"
    if at in ("deprecate", "archive"):
        return "shutdown_cleanup"
    if at in ("analyze", "investigate"):
        return "analysis"
    if at == "generate":
        return "local_generation"
    if at in ("start", "stop", "pause", "scale", "custom"):
        return "local_execution"
    return "local_execution"


def explain_rollout_action(action_key: str, *, current_tier: AutonomyTier) -> dict[str, Any]:
    """Structured explanation for ``argus autonomy explain <key>``."""
    row = _row_by_key(action_key)
    if row is None:
        return {
            "ok": False,
            "error": f"unknown action key {action_key!r}; try one of: {', '.join(sorted({r.key for r in MATRIX}))}",
        }
    eff = effective_execution_tier(current_tier)
    allowed = not row.always_forbidden and eff >= row.min_tier
    return {
        "ok": True,
        "action": row.key,
        "description": row.description,
        "min_tier_required": int(row.min_tier),
        "min_tier_for_unrestricted_class": int(row.min_tier),
        "requires_approval": row.requires_approval,
        "requires_simulation": row.requires_simulation,
        "auto_exec_eligible": row.auto_exec_eligible,
        "always_forbidden": row.always_forbidden,
        "current_effective_tier": int(eff),
        "allowed_under_current_tier": allowed,
        "rationale": _rationale_line(row, eff, allowed),
    }


def explain_action_contract(
    contract: ActionContract,
    *,
    current_tier: AutonomyTier,
    source_path: str | None = None,
) -> dict[str, Any]:
    """Explain matrix row for a loaded contract (includes inferred key)."""
    key = classify_action_contract(contract)
    out = explain_rollout_action(key, current_tier=current_tier)
    out["matrix_key_inferred"] = key
    out["action_id"] = contract.action_id
    out["product_id"] = contract.product_id
    out["action_type"] = contract.normalized_action_type()
    if source_path:
        out["source_path"] = source_path
    return out


def _rationale_line(row: RolloutActionRow, eff: AutonomyTier, allowed: bool) -> str:
    if row.always_forbidden:
        return "Always forbidden for autonomous operation — requires human-driven workflows outside Argus policy."
    if not allowed:
        return (
            f"Effective tier {int(eff)} is below this category's minimum tier {int(row.min_tier)} "
            "— raise tier or run via explicit operator commands with approvals."
        )
    bits = []
    if row.requires_approval:
        bits.append("approval likely required for execution-class work")
    if row.requires_simulation:
        bits.append("simulation/review recommended before external impact")
    if not row.auto_exec_eligible:
        bits.append("auto-exec generally not eligible for this class")
    tail = "; ".join(bits) if bits else "within bounded rollout defaults"
    return f"Permitted class under tier {int(eff)} — {tail}."
