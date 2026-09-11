"""
Derive ActionContracts from the weekly plan: priorities, experiments, and decisions.

Commands are Argus CLI entrypoints (``argus …``) with repo-root working directory ``.``.
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path

from argus.actions.models import ActionContract
from argus.core.models.enums import ActionType
from argus.decision.intents import DecisionIntent
from argus.experiments.models import ExperimentStatus
from argus.experiments.store import list_experiments
from argus.planning.models import PlanningActionsBundle
from argus.planning.weekly import _ranked_rows, build_weekly_plan


def _new_id(kind: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"pla_{kind}_{ts}_{secrets.token_hex(3)}"


def _contract(
    *,
    action_id: str,
    product_id: str,
    action_type: str,
    command: str,
    expected_outcome: str,
    rollback_notes: str,
) -> ActionContract:
    return ActionContract(
        action_id=action_id,
        product_id=product_id,
        action_type=action_type,
        command=command,
        working_directory=".",
        expected_outcome=expected_outcome,
        rollback_notes=rollback_notes,
        requires_approval=True,
        safe_to_auto_execute=False,
        estimated_duration="5–15m",
        created_at=datetime.now(timezone.utc),
    )


def _map_intent_to_command(intent_raw: str, pid: str) -> tuple[str, str, str]:
    """Return ``(command, expected_outcome, action_type)`` for a decision intent, or None."""
    try:
        intent = DecisionIntent(str(intent_raw).strip())
    except ValueError:
        return (
            f"argus decisions show {pid}",
            f"Review persisted decision bundle for {pid} and pick a human next step.",
            ActionType.ANALYZE.value,
        )

    if intent in (DecisionIntent.KILL_PRODUCT, DecisionIntent.DEPRECATE_PRODUCT):
        return (
            f"argus lifecycle report {pid}",
            "Markdown + JSON justification from local signals; use to confirm sunset or continue.",
            ActionType.INVESTIGATE.value,
        )
    if intent == DecisionIntent.ESCALATE_TO_HUMAN:
        return (
            f"argus escalation generate {pid}",
            "Escalation packet emitted if rules require halt-and-handoff; review before acting.",
            ActionType.INVESTIGATE.value,
        )
    if intent == DecisionIntent.REDUCE_COST:
        return (
            "argus economics analyze",
            "Refreshed per-product cost/revenue snapshot under runs/economics/latest.json.",
            ActionType.ANALYZE.value,
        )
    if intent == DecisionIntent.GATHER_MORE_DATA:
        return (
            f"argus signals collect {pid}",
            f"New normalized signals for {pid}; follow with findings/decisions if needed.",
            ActionType.ANALYZE.value,
        )
    if intent == DecisionIntent.LAUNCH_EXPERIMENT:
        return (
            f"argus experiments evaluate {pid}",
            f"Deterministic experiment verdicts for open work on {pid}.",
            ActionType.ANALYZE.value,
        )
    if intent == DecisionIntent.IMPROVE_PRODUCT:
        return (
            f"argus findings generate {pid}",
            f"Regenerated findings from latest signals for {pid}.",
            ActionType.ANALYZE.value,
        )
    if intent == DecisionIntent.HOLD_STEADY:
        return (
            f"argus decisions show {pid}",
            f"Confirm top candidates still match operator intent for {pid}; no automated change.",
            ActionType.ANALYZE.value,
        )
    return (
        f"argus decisions show {pid}",
        f"Inspect ranked candidates for {pid} and choose a concrete operator step.",
        ActionType.ANALYZE.value,
    )


def _dedup_key(command: str, product_id: str) -> tuple[str, str]:
    return (command.strip(), product_id.strip())


def build_planning_actions(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> PlanningActionsBundle:
    """
    Build ActionContracts from the current weekly plan plus experiments and ranked decisions.

    Sources:
    - **priority**: portfolio refresh + per focus/risk product pipeline (signals → findings → decisions)
    - **experiments**: evaluate open work per product (active/proposed experiments)
    - **decisions**: top ranked portfolio rows with intent-specific Argus follow-ups
    """
    root = repo_root.resolve()
    plan = build_weekly_plan(root, products_dir=products_dir)
    now = datetime.now(timezone.utc).isoformat()

    seen: set[tuple[str, str]] = set()
    priority_ids: list[str] = []
    experiment_ids: list[str] = []
    decision_ids: list[str] = []
    actions: list[ActionContract] = []

    def add(
        *,
        kind: str,
        source_list: list[str],
        product_id: str,
        action_type: str,
        command: str,
        expected_outcome: str,
        rollback_notes: str,
    ) -> None:
        k = _dedup_key(command, product_id)
        if k in seen:
            return
        seen.add(k)
        aid = _new_id(kind)
        source_list.append(aid)
        actions.append(
            _contract(
                action_id=aid,
                product_id=product_id,
                action_type=action_type,
                command=command,
                expected_outcome=expected_outcome,
                rollback_notes=rollback_notes,
            )
        )

    # --- Priorities ---
    add(
        kind="pri",
        source_list=priority_ids,
        product_id="portfolio",
        action_type=ActionType.ANALYZE.value,
        command="argus portfolio refresh",
        expected_outcome=(
            "Validated inventory; fresh signals, findings, decisions, and portfolio ranking under runs/."
        ),
        rollback_notes=(
            "Artifacts are append-only under runs/; delete the latest generation folders only if you "
            "must revert, then restore from VCS or backups."
        ),
    )

    hot_pids = sorted(set(plan.focus_products) | set(plan.risk_focus))
    for pid in hot_pids:
        add(
            kind="pri",
            source_list=priority_ids,
            product_id=pid,
            action_type=ActionType.ANALYZE.value,
            command=f"argus signals collect {pid} && argus findings generate {pid} && argus decisions generate {pid}",
            expected_outcome=(
                f"Up-to-date signal bundle, findings, and decision candidates for {pid} aligned with this plan."
            ),
            rollback_notes=(
                "Re-run after restoring prior runs/signals/latest or runs/findings/latest from backup; "
                "or use `argus history snapshot` before risky changes."
            ),
        )

    # --- Experiments (active / proposed) ---
    eval_products: set[str] = set()
    for exp in list_experiments(root):
        if exp.status not in (ExperimentStatus.ACTIVE, ExperimentStatus.PROPOSED):
            continue
        if exp.product_id in eval_products:
            continue
        eval_products.add(exp.product_id)
        add(
            kind="exp",
            source_list=experiment_ids,
            product_id=exp.product_id,
            action_type=ActionType.ANALYZE.value,
            command=f"argus experiments evaluate {exp.product_id}",
            expected_outcome=(
                f"Structured verdicts for open experiments on {exp.product_id}; may update experiment status."
            ),
            rollback_notes=(
                "Re-open or revert experiment JSON under runs/experiments/ from VCS; "
                "use `argus experiments update-status` if you must undo a status flip."
            ),
        )

    # --- Decisions (ranked portfolio → intent-specific commands) ---
    ranked = _ranked_rows(root)
    ranked_sorted = sorted(
        [r for r in ranked if isinstance(r, dict) and r.get("product_id")],
        key=lambda r: int(r.get("rank") or 999),
    )
    priority_pid_set = set(hot_pids)
    pipeline_cmd_for = {
        pid: f"argus signals collect {pid} && argus findings generate {pid} && argus decisions generate {pid}"
        for pid in priority_pid_set
    }

    for row in ranked_sorted[:8]:
        pid = str(row.get("product_id", "")).strip()
        if not pid:
            continue
        intent = str(row.get("top_intent", "") or "")
        cmd, outcome, atype = _map_intent_to_command(intent, pid)

        # Skip duplicates already covered by the focus/risk pipeline (same shell chain).
        if pid in priority_pid_set and cmd in (
            f"argus findings generate {pid}",
            f"argus signals collect {pid}",
            f"argus experiments evaluate {pid}",
        ):
            continue
        if pid in priority_pid_set and cmd == pipeline_cmd_for.get(pid):
            continue

        # Global economics runs once.
        if cmd == "argus economics analyze":
            add(
                kind="dec",
                source_list=decision_ids,
                product_id="portfolio",
                action_type=atype,
                command=cmd,
                expected_outcome=outcome,
                rollback_notes=(
                    "Economics JSON is overwritten in runs/economics/; keep prior copy from git or backup if needed."
                ),
            )
            continue

        add(
            kind="dec",
            source_list=decision_ids,
            product_id=pid,
            action_type=atype,
            command=cmd,
            expected_outcome=outcome,
            rollback_notes=(
                "Most commands only regenerate local JSON under runs/; restore from backup or re-run "
                "`argus portfolio refresh` after fixing inputs."
            ),
        )

    return PlanningActionsBundle(
        schema="argus.planning_actions.v1",
        generated_at_utc=now,
        repo_root=str(root),
        weekly_plan_at_utc=plan.generated_at_utc,
        planning_window=plan.planning_window,
        actions=actions,
        priority_action_ids=priority_ids,
        experiment_action_ids=experiment_ids,
        decision_action_ids=decision_ids,
    )
