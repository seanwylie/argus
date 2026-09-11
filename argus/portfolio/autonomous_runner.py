"""
Bounded autonomous operator session — refresh → cycle → lifecycle → operator summary → narrative per iteration,
with scheduler-equivalent guardrails and a single session artifact.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.dashboard.narrative import run_operator_narrative
from argus.dashboard.operator_summary import run_operator_summary
from argus.portfolio.cycle import run_portfolio_cycle
from argus.portfolio.lifecycle import (
    PORTFOLIO_LIFECYCLE_SCHEMA,
    collect_promotion_opportunities,
    run_portfolio_lifecycle,
)
from argus.portfolio.lifecycle_session_influence import build_lifecycle_session_influence
from argus.portfolio.refresh import run_portfolio_refresh
from argus.portfolio.scheduler import (
    CYCLE_OVERALL_STOP,
    DEFAULT_INTERVENTION_FLAGGED_THRESHOLD,
    DEFAULT_INTERVENTION_HEAVY_STREAK,
    DEFAULT_MAX_CYCLES,
    DEFAULT_NO_MATERIAL_CHANGE_STREAK,
    QUIESCENCE_STOP_RECOMMENDATIONS,
    _flagged_count,
    _material_change_count,
    _overall_rec,
    _per_cycle_record,
    _quiescence_rec,
)
from argus.portfolio.substrate_policy_state import (
    DEFAULT_ABORT_NEW_SESSION_AFTER_CONSECUTIVE_DEGRADED,
    DEFAULT_SUPPRESS_PROMOTIONS_AFTER_CONSECUTIVE_DEGRADED,
    build_degraded_substrate_policy_block,
    compute_next_degraded_streak,
    load_substrate_policy_state,
    write_substrate_policy_state,
)
from argus.products.promotion import (
    run_promote_bootstrap,
    run_promote_creation,
    run_promote_deprecation,
)
from argus.products.signal_instrumentation import (
    load_latest_signal_instrumentation_by_product,
    product_ids_under_instrumentation_pressure,
)

PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA = "argus.portfolio_autonomous_runner.v1"

# Relative paths (posix) used when corresponding stages persist outputs
_ARTIFACT_REFS = (
    "runs/portfolio/latest/summary.txt",
    "runs/portfolio/latest/refresh.json",
    "runs/portfolio/cycle/latest.json",
    "runs/portfolio/lifecycle/latest.json",
    "runs/dashboard/operator_summary/latest.json",
    "runs/dashboard/narrative/latest.json",
)


def portfolio_autonomous_runner_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "autonomous_runner"


def _annotate_per_cycle_confidence_adjustment(
    per_cycle_outcomes: list[dict[str, Any]],
    *,
    kind: str,
    adjustment: dict[str, Any],
) -> None:
    if not per_cycle_outcomes:
        return
    row = per_cycle_outcomes[-1]
    row["confidence_adjustment"] = {
        "action": "continue_once_then_reassess",
        "kind": kind,
        "adjustment_schema": adjustment.get("schema"),
        "patterns_matched": list(adjustment.get("patterns_matched") or []),
    }


def _dashboard_status(
    summary_pl: dict[str, Any] | None,
    narrative_pl: dict[str, Any] | None,
    *,
    skipped: bool,
) -> dict[str, Any]:
    if skipped:
        return {
            "operator_summary": {"status": "skipped", "reason": "dry_run_or_stage_writes_disabled"},
            "operator_narrative": {"status": "skipped", "reason": "dry_run_or_stage_writes_disabled"},
        }
    ss = summary_pl or {}
    nn = narrative_pl or {}
    return {
        "operator_summary": {
            "status": "ok" if ss else "missing",
            "run_id": ss.get("run_id"),
            "headline_status": ss.get("headline_status"),
            "evaluated_at_utc": ss.get("evaluated_at_utc"),
        },
        "operator_narrative": {
            "status": "ok" if nn else "missing",
            "run_id": nn.get("run_id"),
            "evaluated_at_utc": nn.get("evaluated_at_utc"),
        },
    }


def _execute_bounded_autonomous_promotions(
    repo_root: Path,
    opportunities: dict[str, Any],
    *,
    dry_run: bool,
    stages_persist: bool,
    allow_promotion: bool,
    promotion_include_bootstrap: bool,
    products_dir: Path | None,
    write_promotion_artifacts: bool,
) -> dict[str, Any]:
    """
    At most one safe deprecation plan, one creation scaffold (optional chained bootstrap),
    and one standalone bootstrap when no creation ran — all via :mod:`argus.products.promotion`.
    """
    root = repo_root.resolve()
    effective_dry = bool(dry_run) or not stages_persist
    write_stages = stages_persist and not effective_dry

    base: dict[str, Any] = {
        "allow_promotion": allow_promotion,
        "promotion_include_bootstrap": promotion_include_bootstrap,
        "effective_dry_run": effective_dry,
        "write_stage_artifacts": write_stages,
        "steps": [],
    }
    if not allow_promotion:
        base["skipped_reason"] = "allow_promotion is false (detection-only)"
        return base

    acts = [a for a in (opportunities.get("promotable_actions") or []) if isinstance(a, dict)]
    dep = next((a for a in acts if a.get("kind") == "deprecation_proposal_to_plan" and a.get("safe_for_auto")), None)
    cre = next((a for a in acts if a.get("kind") == "creation_proposal_to_scaffold" and a.get("safe_for_auto")), None)
    boot_candidates = [a for a in acts if a.get("kind") == "scaffolded_product_to_bootstrap" and a.get("safe_for_auto")]

    def _step(
        kind: str,
        attempted: bool,
        *,
        result_status: str,
        detail: str,
        promotion: dict[str, Any] | None = None,
    ) -> None:
        (base["steps"]).append(
            {
                "kind": kind,
                "attempted": attempted,
                "result_status": result_status,
                "detail": detail,
                "promotion": promotion,
            }
        )

    if dep:
        pr_id = str(dep.get("proposal_id") or "")
        pl = run_promote_deprecation(
            root,
            proposal_id=pr_id,
            dry_run=effective_dry,
            write_promotion_artifact=write_promotion_artifacts,
            write_stage_artifacts=write_stages,
            actor="autonomous_runner",
            products_dir=products_dir,
        )
        st = str(pl.get("result_status") or "")
        nested_dep = (pl.get("nested_results") or {}).get("deprecation_plan") or {}
        ok = st == "success" or (effective_dry and bool(nested_dep.get("ok")))
        _step(
            "deprecation_proposal_to_plan",
            True,
            result_status="success" if ok else "failed",
            detail="deprecation plan promotion" + (" (preview)" if effective_dry else ""),
            promotion=pl,
        )
    creation_product_for_bootstrap: str | None = None
    if cre:
        pr_id = str(cre.get("proposal_id") or "")
        chain_bootstrap = bool(promotion_include_bootstrap) and not effective_dry
        pl = run_promote_creation(
            root,
            proposal_id=pr_id,
            bootstrap=chain_bootstrap,
            dry_run=effective_dry,
            write_promotion_artifact=write_promotion_artifacts,
            write_stage_artifacts=write_stages,
            actor="autonomous_runner",
            products_dir=products_dir,
        )
        nested_sc = (pl.get("nested_results") or {}).get("creation_scaffold") or {}
        creation_product_for_bootstrap = str(nested_sc.get("product_id") or "").strip() or None
        st = str(pl.get("result_status") or "")
        ok = st == "success" or (effective_dry and bool(nested_sc.get("ok")))
        _step(
            "creation_proposal_to_scaffold",
            True,
            result_status="success" if ok else "failed",
            detail="creation scaffold promotion" + (" (preview)" if effective_dry else ""),
            promotion=pl,
        )
        if effective_dry and promotion_include_bootstrap and creation_product_for_bootstrap and nested_sc.get("ok"):
            bpl = run_promote_bootstrap(
                root,
                product_id=creation_product_for_bootstrap,
                dry_run=True,
                write_promotion_artifact=write_promotion_artifacts,
                write_stage_artifacts=False,
                actor="autonomous_runner",
                products_dir=products_dir,
            )
            b_ok = str(bpl.get("result_status") or "") == "success" or bool(
                (bpl.get("nested_results") or {}).get("creation_bootstrap", {}).get("ok")
            )
            _step(
                "scaffolded_product_to_bootstrap",
                True,
                result_status="success" if b_ok else "failed",
                detail="chained bootstrap preview (dry-run) after scaffold preview",
                promotion=bpl,
            )
    elif promotion_include_bootstrap and boot_candidates:
        b0 = boot_candidates[0]
        pid = str(b0.get("product_id") or "")
        pl = run_promote_bootstrap(
            root,
            product_id=pid,
            dry_run=effective_dry,
            write_promotion_artifact=write_promotion_artifacts,
            write_stage_artifacts=write_stages,
            actor="autonomous_runner",
            products_dir=products_dir,
        )
        st = str(pl.get("result_status") or "")
        nested_b = (pl.get("nested_results") or {}).get("creation_bootstrap") or {}
        ok = st == "success" or (effective_dry and bool(nested_b.get("ok")))
        _step(
            "scaffolded_product_to_bootstrap",
            True,
            result_status="success" if ok else "failed",
            detail="standalone bootstrap promotion" + (" (preview)" if effective_dry else ""),
            promotion=pl,
        )

    return base


def _promotion_session_notes(
    opportunities: dict[str, Any],
    execution: dict[str, Any],
) -> str:
    n_promo = len(opportunities.get("promotable_actions") or [])
    n_block = len(opportunities.get("blocked_promotions") or [])
    lines = [
        "",
        "Promotion scan (advisory):",
        f"- Promotable actions detected: {n_promo}",
        f"- Blocked or unsafe (with reasons recorded): {n_block}",
    ]
    if not execution.get("allow_promotion"):
        lines.append("- Execution: detection only (`allow_promotion` false).")
        return "\n".join(lines)
    if execution.get("skipped_reason"):
        lines.append(f"- Execution: {execution.get('skipped_reason')}")
        return "\n".join(lines)
    eff = execution.get("effective_dry_run")
    lines.append(f"- Execution: allowed (effective dry-run: {eff}).")
    for step in execution.get("steps") or []:
        if not isinstance(step, dict):
            continue
        lines.append(
            f"  - {step.get('kind')}: {step.get('result_status')} — {step.get('detail')}"
        )
    return "\n".join(lines)


def _session_summary_lines(
    *,
    cycles_run: int,
    stop_reason: str,
    artifacts_refreshed: list[str],
    per_cycle: list[dict[str, Any]],
    lifecycle_primary_signal: str | None = None,
) -> str:
    lines = [
        f"Autonomous session completed {cycles_run} iteration(s).",
        f"Stop reason: {stop_reason}.",
        "",
        "Canonical artifacts touched (when stages persisted):",
    ]
    if artifacts_refreshed:
        for p in artifacts_refreshed:
            lines.append(f"- `{p}`")
    else:
        lines.append("- _(none — dry run or no successful writes)_")
    lines.append("")
    lines.append("Per iteration: portfolio refresh → portfolio cycle → portfolio lifecycle → "
                  "operator summary → operator narrative; then guardrails are evaluated on the cycle outcome.")
    if lifecycle_primary_signal:
        lines.append("")
        lines.append(f"Lifecycle-aware primary signal (advisory): `{lifecycle_primary_signal}`.")
    if per_cycle:
        lines.append("")
        lines.append("Last iteration snapshot:")
        last = per_cycle[-1]
        pc = last.get("portfolio_cycle") or {}
        lines.append(
            f"- Cycle run id: `{pc.get('cycle_run_id')}` · quiescence: `{pc.get('quiescence_recommendation')}` · "
            f"overall: `{pc.get('overall_operator_recommendation')}`"
        )
    return "\n".join(lines)


def run_portfolio_autonomous_session(
    repo_root: Path,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    limit_per_cycle: int = 5,
    limit_history: int = 30,
    dry_run: bool = False,
    skip_import_failed: bool = False,
    skip_waiting: bool = False,
    products_dir: Path | None = None,
    write_session_artifacts: bool = True,
    write_stage_artifacts: bool = True,
    check_stop_sentinel: bool = True,
    no_material_change_streak_limit: int = DEFAULT_NO_MATERIAL_CHANGE_STREAK,
    intervention_flagged_threshold: int = DEFAULT_INTERVENTION_FLAGGED_THRESHOLD,
    intervention_heavy_streak: int = DEFAULT_INTERVENTION_HEAVY_STREAK,
    allow_promotion: bool = False,
    promotion_include_bootstrap: bool = False,
    degraded_suppress_promotions_after: int = DEFAULT_SUPPRESS_PROMOTIONS_AFTER_CONSECUTIVE_DEGRADED,
    degraded_abort_session_after: int = DEFAULT_ABORT_NEW_SESSION_AFTER_CONSECUTIVE_DEGRADED,
) -> dict[str, Any]:
    """
    Run up to ``max_cycles`` iterations. Each iteration executes, in order:

    1. ``run_portfolio_refresh``
    2. ``run_portfolio_cycle``
    3. ``run_portfolio_lifecycle``
    4. ``run_operator_summary``
    5. ``run_operator_narrative``

    Guardrails (same semantics as ``run_portfolio_scheduler_session``) apply **after** each full
    iteration, using the portfolio cycle payload from step 2.

    ``write_session_artifacts`` controls ``runs/portfolio/autonomous_runner/*`` only.

    ``write_stage_artifacts`` gates persistence for refresh, cycle, lifecycle, operator summary, and
    narrative. When ``False`` (or when ``dry_run`` is ``True``), those stages run in a no-write /
    dry-run mode: nothing is written under ``runs/portfolio/``, ``runs/dashboard/``, etc.

    Lifecycle promotion records under ``runs/products/promotion_actions/`` are written only when
    ``write_session_artifacts`` is true **and** pipeline stages persist (``write_stage_artifacts``
    and not ``dry_run``). That matches effective dry-run promotion execution inside
    ``_execute_bounded_autonomous_promotions``.

    After iterations complete, the runner evaluates **promotion opportunities** (creation scaffold,
    bootstrap, deprecation plan) and records them on the session payload. With ``allow_promotion``,
    it may execute **bounded** safe promotions via :mod:`argus.products.promotion` (see
    ``promotion_execution`` in the payload). ``promotion_include_bootstrap`` chains bootstrap after
    scaffold when not in effective dry-run mode, or adds a standalone bootstrap step when no
    creation promotion runs.

    ``degraded_suppress_promotions_after`` / ``degraded_abort_session_after`` bound the yellow-light
    policy: consecutive persisted sessions with substrate coherence ``degraded`` suppress promotions
    (when ``allow_promotion``) and eventually refuse to start a new session (inspect-only stop).
    """
    from argus.portfolio.autonomy_memory import evaluate_autonomous_confidence_adjustment

    root = repo_root.resolve()
    session_started = datetime.now(timezone.utc).isoformat()
    session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    max_c = max(1, int(max_cycles))
    lim = max(0, int(limit_per_cycle))
    hist = max(1, int(limit_history))
    streak_limit = max(1, int(no_material_change_streak_limit))
    int_thresh = max(1, int(intervention_flagged_threshold))
    int_streak_need = max(1, int(intervention_heavy_streak))

    conf_adj_initial = evaluate_autonomous_confidence_adjustment(root, limit_history=hist)
    eligible_initial = bool(conf_adj_initial.get("eligible"))
    eff_extra = (
        1
        if eligible_initial and int(conf_adj_initial.get("effective_max_cycles_delta") or 0) > 0
        else 0
    )
    eff_max_cycles = max_c + eff_extra
    conf_adj = conf_adj_initial
    conf_evaluations_per_iteration: list[dict[str, Any]] = []
    continuation_used = False
    _eval_conf_fields = (
        "eligible",
        "deferral_available",
        "block_reason",
        "patterns_matched",
        "autonomy_memory_run_id",
        "substrate_coherence_context",
    )

    stages_persist = bool(write_stage_artifacts) and not bool(dry_run)
    refresh_no_save = not stages_persist
    cycle_dry = bool(dry_run) or not stages_persist

    persist_policy = bool(write_session_artifacts) and stages_persist
    suppress_thr = max(1, int(degraded_suppress_promotions_after))
    abort_thr = max(1, int(degraded_abort_session_after))
    sp_state_before = load_substrate_policy_state(root) if persist_policy else {"consecutive_degraded_sessions": 0}
    prior_degraded_streak = int(sp_state_before.get("consecutive_degraded_sessions") or 0)

    if persist_policy and prior_degraded_streak >= abort_thr:
        finished_early = datetime.now(timezone.utc).isoformat()
        dsb = build_degraded_substrate_policy_block(
            consecutive_before=prior_degraded_streak,
            consecutive_after=prior_degraded_streak,
            session_substrate_overall=None,
            stop_reason="artifact_coherence_degraded_persistent",
            suppress_promotions_after=suppress_thr,
            abort_new_session_after=abort_thr,
            suppress_promotions_applied=False,
            stopped_for_degraded_persistent=True,
        )
        out_abort: dict[str, Any] = {
            "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
            "session_id": session_id,
            "started_at_utc": session_started,
            "finished_at_utc": finished_early,
            "cycles_run": 0,
            "stop_reason": "artifact_coherence_degraded_persistent",
            "stop_reason_codes": ["autonomous_runner.stop.artifact_coherence_degraded_persistent"],
            "artifact_coherence_policy": {
                "soft_brake_on_invalid_substrate": True,
                "stopped_for_invalid_substrate": False,
                "promotions_suppressed_for_invalid_substrate": False,
                "degraded_substrate_policy": dsb,
            },
            "artifacts_refreshed": [],
            "session_summary": (
                f"Autonomous session did not run pipeline iterations: substrate coherence was degraded for "
                f"{prior_degraded_streak} consecutive persisted session(s) (threshold {abort_thr}). "
                "Inspect `runs/debug/artifact_coherence/latest.json` and repair inputs; "
                "a valid or warning session resets the counter."
            ),
            "per_cycle_outcomes": [],
            "dashboard_refresh_status": _dashboard_status(None, None, skipped=True),
            "lifecycle_session_influence": build_lifecycle_session_influence(None),
            "promotable_actions": [],
            "promotion_recommendations": [],
            "blocked_promotions": [],
            "promotion_opportunities": {"schema": "argus.promotion_opportunities.v1"},
            "promotion_execution": {
                "allow_promotion": bool(allow_promotion),
                "promotion_include_bootstrap": bool(promotion_include_bootstrap),
                "effective_dry_run": bool(dry_run) or not stages_persist,
                "skipped_reason": "degraded_substrate_persistent_abort",
                "steps": [],
                "write_stage_artifacts": stages_persist,
            },
            "confidence_adjustment": evaluate_autonomous_confidence_adjustment(root, limit_history=hist),
            "inputs": {
                "max_cycles": max_c,
                "limit_per_cycle": lim,
                "limit_history": hist,
                "dry_run": dry_run,
                "skip_import_failed": skip_import_failed,
                "skip_waiting": skip_waiting,
                "write_session_artifacts": write_session_artifacts,
                "write_stage_artifacts": write_stage_artifacts,
                "check_stop_sentinel": check_stop_sentinel,
                "no_material_change_streak_limit": streak_limit,
                "intervention_flagged_threshold": int_thresh,
                "intervention_heavy_streak_cycles": int_streak_need,
                "allow_promotion": allow_promotion,
                "promotion_include_bootstrap": promotion_include_bootstrap,
                "degraded_suppress_promotions_after": suppress_thr,
                "degraded_abort_session_after": abort_thr,
            },
            "cycle_run_ids": [],
            "guardrails_note": (
                "Substrate policy refused to start: too many consecutive degraded coherence sessions. "
                "See `substrate_policy_state.json` and degraded_substrate_policy in this payload."
            ),
            "signal_instrumentation_advisory": {"products_under_instrumentation_pressure": [], "notes": []},
        }
        if write_session_artifacts:
            write_portfolio_autonomous_session_artifacts(root, out_abort)
        return out_abort

    cycles_run = 0
    cycle_run_ids: list[str] = []
    per_cycle_outcomes: list[dict[str, Any]] = []
    stop_reason = "unknown"
    stop_reason_codes: list[str] = []
    artifacts_refreshed: list[str] = []

    no_mat_streak = 0
    int_heavy_streak = 0

    ar_dir = portfolio_autonomous_runner_dir(root)
    stop_sentinel = ar_dir / "STOP"

    last_dashboard_status: dict[str, Any] = {}
    last_lifecycle_session_influence: dict[str, Any] = build_lifecycle_session_influence(None)
    last_substrate_overall: str | None = None

    for _ in range(eff_max_cycles):
        if check_stop_sentinel and cycles_run > 0 and stop_sentinel.is_file():
            stop_reason = "explicit_stop_sentinel"
            stop_reason_codes.append("autonomous_runner.stop.sentinel_file")
            break

        cycle_index = cycles_run + 1
        refresh_payload: dict[str, Any] | None = None
        cycle_payload: dict[str, Any] | None = None
        lifecycle_payload: dict[str, Any] | None = None
        summary_payload: dict[str, Any] | None = None
        narrative_payload: dict[str, Any] | None = None

        ref_exit, refresh_payload = run_portfolio_refresh(
            root,
            products_dir=products_dir,
            per_product_reports=False,
            no_save=refresh_no_save,
        )
        refresh_ok = bool(refresh_payload.get("ok")) and ref_exit == 0

        if refresh_ok and refresh_payload.get("portfolio_state") == "empty_portfolio":
            stop_reason = "empty_portfolio"
            stop_reason_codes.append("autonomous_runner.stop.empty_portfolio")
            per_cycle_outcomes.append(
                {
                    "cycle_index": cycle_index,
                    "portfolio_refresh": {
                        "exit_code": ref_exit,
                        "ok": True,
                        "portfolio_state": "empty_portfolio",
                        "zero_state": True,
                        "schema": refresh_payload.get("schema"),
                    },
                    "portfolio_cycle": None,
                    "portfolio_lifecycle": None,
                    "operator_summary": None,
                    "operator_narrative": None,
                }
            )
            break

        if not refresh_ok:
            stop_reason = "portfolio_refresh_failed"
            stop_reason_codes.append(
                f"autonomous_runner.stop.refresh_exit={ref_exit}_error={refresh_payload.get('error')!r}"
            )
            per_cycle_outcomes.append(
                {
                    "cycle_index": cycle_index,
                    "portfolio_refresh": {
                        "exit_code": ref_exit,
                        "ok": refresh_ok,
                        "error": refresh_payload.get("error"),
                        "schema": refresh_payload.get("schema"),
                    },
                    "portfolio_cycle": None,
                    "portfolio_lifecycle": None,
                    "operator_summary": None,
                    "operator_narrative": None,
                }
            )
            break

        if stages_persist:
            for p in _ARTIFACT_REFS[:2]:
                if p not in artifacts_refreshed:
                    artifacts_refreshed.append(p)

        cycle_payload = run_portfolio_cycle(
            root,
            limit=lim,
            limit_history=hist,
            products_dir=products_dir,
            dry_run=cycle_dry,
            execute=True,
            skip_import_failed=bool(skip_import_failed),
            skip_waiting=bool(skip_waiting),
            write_cycle_artifacts=stages_persist,
            write_stage_artifacts=stages_persist,
            write_artifact_coherence=stages_persist,
        )
        if not cycle_payload.get("ok", True):
            stop_reason = "portfolio_cycle_failed"
            stop_reason_codes.append("autonomous_runner.stop.cycle_ok_false")
            per_cycle_outcomes.append(
                _build_per_cycle_outcome(
                    cycle_index=cycle_index,
                    ref_exit=ref_exit,
                    refresh_payload=refresh_payload,
                    cycle_payload=cycle_payload,
                    lifecycle_payload=None,
                    summary_payload=None,
                    narrative_payload=None,
                    stages_persist=stages_persist,
                )
            )
            break

        ac_block = cycle_payload.get("artifact_coherence") or {}
        if (
            stages_persist
            and ac_block.get("status") == "ok"
            and str(ac_block.get("overall_status") or "") == "invalid"
        ):
            stop_reason = "artifact_coherence_invalid"
            stop_reason_codes.append(
                f"autonomous_runner.stop.artifact_coherence_invalid:run_id={ac_block.get('run_id')}"
            )
            gate_row = _build_per_cycle_outcome(
                cycle_index=cycle_index,
                ref_exit=ref_exit,
                refresh_payload=refresh_payload,
                cycle_payload=cycle_payload,
                lifecycle_payload=None,
                summary_payload=None,
                narrative_payload=None,
                stages_persist=stages_persist,
            )
            gate_row["artifact_coherence_gate"] = {
                "policy": "soft_brake_invalid_substrate",
                "overall_status": "invalid",
                "inspect_only": True,
                "paths": ac_block.get("paths"),
            }
            per_cycle_outcomes.append(gate_row)
            last_substrate_overall = str(ac_block.get("overall_status") or "").strip() or None
            break

        try:
            lifecycle_payload = run_portfolio_lifecycle(
                root,
                products_dir=products_dir,
                write_artifacts=stages_persist,
            )
        except Exception as e:
            stop_reason = "portfolio_lifecycle_failed"
            stop_reason_codes.append(f"autonomous_runner.stop.lifecycle:{type(e).__name__}")
            per_cycle_outcomes.append(
                _build_per_cycle_outcome(
                    cycle_index=cycle_index,
                    ref_exit=ref_exit,
                    refresh_payload=refresh_payload,
                    cycle_payload=cycle_payload,
                    lifecycle_payload={"error": str(e), "error_type": type(e).__name__},
                    summary_payload=None,
                    narrative_payload=None,
                    stages_persist=stages_persist,
                )
            )
            break

        try:
            summary_payload = run_operator_summary(
                root,
                limit_history=hist,
                products_dir=products_dir,
                write_artifacts=stages_persist,
            )
        except Exception as e:
            stop_reason = "operator_summary_failed"
            stop_reason_codes.append(f"autonomous_runner.stop.operator_summary:{type(e).__name__}")
            per_cycle_outcomes.append(
                _build_per_cycle_outcome(
                    cycle_index=cycle_index,
                    ref_exit=ref_exit,
                    refresh_payload=refresh_payload,
                    cycle_payload=cycle_payload,
                    lifecycle_payload=lifecycle_payload,
                    summary_payload={"error": str(e), "error_type": type(e).__name__},
                    narrative_payload=None,
                    stages_persist=stages_persist,
                )
            )
            break

        try:
            narrative_payload = run_operator_narrative(
                root,
                limit_history=hist,
                products_dir=products_dir,
                write_artifacts=stages_persist,
            )
        except Exception as e:
            stop_reason = "operator_narrative_failed"
            stop_reason_codes.append(f"autonomous_runner.stop.operator_narrative:{type(e).__name__}")
            per_cycle_outcomes.append(
                _build_per_cycle_outcome(
                    cycle_index=cycle_index,
                    ref_exit=ref_exit,
                    refresh_payload=refresh_payload,
                    cycle_payload=cycle_payload,
                    lifecycle_payload=lifecycle_payload,
                    summary_payload=summary_payload,
                    narrative_payload={"error": str(e), "error_type": type(e).__name__},
                    stages_persist=stages_persist,
                )
            )
            break

        if stages_persist:
            for p in _ARTIFACT_REFS[2:]:
                if p not in artifacts_refreshed:
                    artifacts_refreshed.append(p)
            for p in (
                "runs/portfolio/outcomes/latest.json",
                "runs/portfolio/strategy/latest.json",
                "runs/portfolio/intervention_inbox/latest.json",
                "runs/portfolio/escalation_inbox/latest.json",
            ):
                if (root / p).is_file() and p not in artifacts_refreshed:
                    artifacts_refreshed.append(p)

        cycles_run += 1
        rid = str(cycle_payload.get("run_id") or "")
        if rid:
            cycle_run_ids.append(rid)

        _ac0 = cycle_payload.get("artifact_coherence") or {}
        last_substrate_overall = str(_ac0.get("overall_status") or "").strip() or None

        last_dashboard_status = _dashboard_status(
            summary_payload,
            narrative_payload,
            skipped=not stages_persist,
        )

        _pco = _build_per_cycle_outcome(
            cycle_index=cycle_index,
            ref_exit=ref_exit,
            refresh_payload=refresh_payload,
            cycle_payload=cycle_payload,
            lifecycle_payload=lifecycle_payload,
            summary_payload=summary_payload,
            narrative_payload=narrative_payload,
            stages_persist=stages_persist,
        )
        last_lifecycle_session_influence = _pco.get("lifecycle_session_influence") or last_lifecycle_session_influence
        per_cycle_outcomes.append(_pco)

        conf_adj = evaluate_autonomous_confidence_adjustment(root, limit_history=hist)
        conf_evaluations_per_iteration.append(
            {k: conf_adj.get(k) for k in _eval_conf_fields}
        )
        continuation_available = bool(conf_adj.get("eligible") and conf_adj.get("deferral_available"))

        ov = _overall_rec(cycle_payload)
        if ov and ov in CYCLE_OVERALL_STOP:
            if (
                continuation_available
                and not continuation_used
                and ov == "inspect_specific_products"
            ):
                continuation_used = True
                stop_reason_codes.append(
                    "autonomous_runner.confidence_adjustment.defer_cycle_overall_inspect_specific_products"
                )
                _annotate_per_cycle_confidence_adjustment(
                    per_cycle_outcomes, kind="cycle_overall_inspect_specific_products", adjustment=conf_adj
                )
                continue
            stop_reason = "cycle_overall_recommendation"
            stop_reason_codes.append(f"autonomous_runner.stop.cycle_overall.{ov}")
            break

        qrec = _quiescence_rec(cycle_payload)
        if qrec and qrec in QUIESCENCE_STOP_RECOMMENDATIONS:
            if continuation_available and not continuation_used and qrec == "inspect":
                continuation_used = True
                stop_reason_codes.append(
                    "autonomous_runner.confidence_adjustment.defer_quiescence_inspect"
                )
                _annotate_per_cycle_confidence_adjustment(
                    per_cycle_outcomes, kind="quiescence_inspect", adjustment=conf_adj
                )
                continue
            stop_reason = "quiescence_recommendation"
            stop_reason_codes.append(f"autonomous_runner.stop.quiescence.{qrec}")
            break

        mat = _material_change_count(cycle_payload)
        if mat == 0:
            no_mat_streak += 1
        else:
            no_mat_streak = 0
        if no_mat_streak >= streak_limit:
            if continuation_available and not continuation_used:
                continuation_used = True
                no_mat_streak = max(0, streak_limit - 1)
                stop_reason_codes.append(
                    "autonomous_runner.confidence_adjustment.defer_no_material_change_streak_once"
                )
                _annotate_per_cycle_confidence_adjustment(
                    per_cycle_outcomes, kind="no_material_change_streak", adjustment=conf_adj
                )
                continue
            stop_reason = "no_material_change_streak"
            stop_reason_codes.append(
                f"autonomous_runner.stop.no_material_change_streak_n={streak_limit}"
            )
            break

        fc = _flagged_count(cycle_payload)
        if fc >= int_thresh:
            int_heavy_streak += 1
        else:
            int_heavy_streak = 0
        if int_heavy_streak >= int_streak_need:
            stop_reason = "intervention_heavy_streak"
            stop_reason_codes.append(
                f"autonomous_runner.stop.intervention_flagged>={int_thresh}_for_{int_streak_need}_cycles"
            )
            break
    else:
        stop_reason = "max_cycles_reached"
        stop_reason_codes.append(f"autonomous_runner.stop.max_cycles={max_c}")
        if eff_extra > 0:
            stop_reason_codes.append("autonomous_runner.confidence_adjustment.effective_iteration_budget_includes_plus_one")

    finished = datetime.now(timezone.utc).isoformat()

    next_degraded_streak = compute_next_degraded_streak(
        prior_streak=prior_degraded_streak,
        stop_reason=stop_reason,
        session_substrate_overall=last_substrate_overall,
    )
    suppress_promotions_degraded = (
        bool(allow_promotion)
        and stop_reason != "artifact_coherence_invalid"
        and str(last_substrate_overall or "").lower() == "degraded"
        and next_degraded_streak >= suppress_thr
    )
    degraded_policy_block = build_degraded_substrate_policy_block(
        consecutive_before=prior_degraded_streak,
        consecutive_after=next_degraded_streak,
        session_substrate_overall=last_substrate_overall,
        stop_reason=stop_reason,
        suppress_promotions_after=suppress_thr,
        abort_new_session_after=abort_thr,
        suppress_promotions_applied=suppress_promotions_degraded,
        stopped_for_degraded_persistent=False,
    )
    if persist_policy:
        write_substrate_policy_state(
            root,
            {
                "consecutive_degraded_sessions": next_degraded_streak,
                "last_session_id": session_id,
                "last_session_substrate_overall": last_substrate_overall,
                "thresholds": {
                    "suppress_promotions_after_consecutive_degraded_sessions": suppress_thr,
                    "abort_new_session_after_consecutive_degraded_sessions": abort_thr,
                },
            },
        )

    promotion_opp = collect_promotion_opportunities(root, products_dir=products_dir)
    allow_promotion_effective = (
        bool(allow_promotion)
        and stop_reason != "artifact_coherence_invalid"
        and not suppress_promotions_degraded
    )
    promotion_exec = _execute_bounded_autonomous_promotions(
        root,
        promotion_opp,
        dry_run=dry_run,
        stages_persist=stages_persist,
        allow_promotion=allow_promotion_effective,
        promotion_include_bootstrap=promotion_include_bootstrap,
        products_dir=products_dir,
        write_promotion_artifacts=bool(write_session_artifacts) and stages_persist,
    )
    if suppress_promotions_degraded:
        promotion_exec = dict(promotion_exec)
        promotion_exec["promotions_suppressed_for_degraded_substrate"] = True
        promotion_exec["skipped_reason"] = (
            "degraded substrate: consecutive persisted sessions with coherence overall_status=degraded "
            f"(threshold {suppress_thr}); inspect runs/debug/artifact_coherence/latest.json"
        )

    session_summary = _session_summary_lines(
        cycles_run=cycles_run,
        stop_reason=stop_reason,
        artifacts_refreshed=artifacts_refreshed,
        per_cycle=per_cycle_outcomes,
        lifecycle_primary_signal=str(last_lifecycle_session_influence.get("primary_signal") or "")
        or None,
    )
    session_summary = session_summary + _promotion_session_notes(promotion_opp, promotion_exec)
    if (
        str(last_substrate_overall or "").lower() == "degraded"
        and next_degraded_streak < suppress_thr
        and stop_reason != "artifact_coherence_invalid"
    ):
        session_summary += (
            "\n\nSubstrate coherence note: latest cycle reported **degraded** "
            f"(consecutive degraded sessions: {next_degraded_streak}; promotion suppress threshold: {suppress_thr}). "
            "Pipeline continued; inspect `runs/debug/artifact_coherence/latest.json` if this persists."
        )
    if suppress_promotions_degraded:
        session_summary += (
            "\n\nDegraded substrate policy: promotions were **suppressed** due to consecutive degraded sessions "
            f"(≥{suppress_thr})."
        )

    inst_by = load_latest_signal_instrumentation_by_product(root)
    inst_pressure = product_ids_under_instrumentation_pressure(inst_by)
    sig_adv_notes: list[str] = []
    if inst_pressure:
        if stop_reason == "cycle_overall_recommendation" and any(
            "inspect_specific_products" in str(c) for c in stop_reason_codes
        ):
            sig_adv_notes.append(
                f"Session stopped with cycle-overall inspect_specific_products while {len(inst_pressure)} "
                "product(s) remain instrumentation-weak — improve observability (`argus products instrument-signals`) "
                "before treating outcomes as optimization shortfalls."
            )
        if stop_reason == "no_material_change_streak":
            sig_adv_notes.append(
                "No material change streak with instrumentation-weak products on disk — stalled loops may be "
                "explained by thin signal coverage; instrument signals before expecting material portfolio movement."
            )
        if stop_reason == "quiescence_recommendation" and any("inspect" in str(c) for c in stop_reason_codes):
            sig_adv_notes.append(
                "Quiescence inspect-class stop while products lack adequate signal instrumentation — "
                "confirm observability before deeper inspect cycles."
            )

    out: dict[str, Any] = {
        "schema": PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
        "session_id": session_id,
        "started_at_utc": session_started,
        "finished_at_utc": finished,
        "cycles_run": cycles_run,
        "stop_reason": stop_reason,
        "stop_reason_codes": stop_reason_codes,
        "artifact_coherence_policy": {
            "soft_brake_on_invalid_substrate": True,
            "stopped_for_invalid_substrate": stop_reason == "artifact_coherence_invalid",
            "promotions_suppressed_for_invalid_substrate": bool(allow_promotion)
            and stop_reason == "artifact_coherence_invalid",
            "degraded_substrate_policy": degraded_policy_block,
        },
        "artifacts_refreshed": artifacts_refreshed,
        "session_summary": session_summary,
        "per_cycle_outcomes": per_cycle_outcomes,
        "dashboard_refresh_status": last_dashboard_status,
        "lifecycle_session_influence": last_lifecycle_session_influence,
        "promotable_actions": promotion_opp.get("promotable_actions") or [],
        "promotion_recommendations": promotion_opp.get("promotion_recommendations") or [],
        "blocked_promotions": promotion_opp.get("blocked_promotions") or [],
        "promotion_opportunities": promotion_opp,
        "promotion_execution": promotion_exec,
        "confidence_adjustment": {
            **conf_adj,
            "continuation_consumed": continuation_used,
            "configured_max_cycles": max_c,
            "effective_max_cycles": eff_max_cycles,
            "evaluations_per_iteration": conf_evaluations_per_iteration,
            "note": (
                "After each full pipeline iteration (refresh → cycle → lifecycle → operator summary → narrative), "
                "confidence adjustment is re-evaluated from autonomy memory, escalation inbox, instrumentation "
                "context, and the durable artifact_coherence snapshot (no hidden coherence recompute)."
            ),
        },
        "inputs": {
            "max_cycles": max_c,
            "limit_per_cycle": lim,
            "limit_history": hist,
            "dry_run": dry_run,
            "skip_import_failed": skip_import_failed,
            "skip_waiting": skip_waiting,
            "write_session_artifacts": write_session_artifacts,
            "write_stage_artifacts": write_stage_artifacts,
            "check_stop_sentinel": check_stop_sentinel,
            "no_material_change_streak_limit": streak_limit,
            "intervention_flagged_threshold": int_thresh,
            "intervention_heavy_streak_cycles": int_streak_need,
            "allow_promotion": allow_promotion,
            "promotion_include_bootstrap": promotion_include_bootstrap,
            "degraded_suppress_promotions_after": suppress_thr,
            "degraded_abort_session_after": abort_thr,
        },
        "cycle_run_ids": cycle_run_ids,
        "guardrails_note": (
            "Autonomous runner caps iterations at max_cycles; after each full pipeline iteration, "
            "quiescence / cycle overall / material-change streak / intervention-heavy rules may stop early. "
            "Touch `runs/portfolio/autonomous_runner/STOP` after cycle 1+ to exit cleanly."
        ),
        "signal_instrumentation_advisory": {
            "products_under_instrumentation_pressure": inst_pressure,
            "notes": sig_adv_notes,
        },
    }

    if write_session_artifacts:
        write_portfolio_autonomous_session_artifacts(root, out)
    return out


def _build_per_cycle_outcome(
    *,
    cycle_index: int,
    ref_exit: int,
    refresh_payload: dict[str, Any] | None,
    cycle_payload: dict[str, Any] | None,
    lifecycle_payload: dict[str, Any] | None,
    summary_payload: dict[str, Any] | None,
    narrative_payload: dict[str, Any] | None,
    stages_persist: bool,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "cycle_index": cycle_index,
        "portfolio_refresh": {
            "exit_code": ref_exit,
            "ok": bool(refresh_payload.get("ok")) if refresh_payload else False,
            "schema": (refresh_payload or {}).get("schema"),
        },
    }
    if cycle_payload:
        pc = _per_cycle_record(cycle_payload)
        pc["cycle_run_id"] = cycle_payload.get("run_id")
        base["portfolio_cycle"] = pc
        ac = cycle_payload.get("artifact_coherence") or {}
        if isinstance(ac, dict) and ac:
            base["artifact_coherence"] = {
                "status": ac.get("status"),
                "overall_status": ac.get("overall_status"),
                "run_id": ac.get("run_id"),
            }
    else:
        base["portfolio_cycle"] = None

    if lifecycle_payload is not None:
        if "error" in lifecycle_payload and "schema" not in lifecycle_payload:
            base["portfolio_lifecycle"] = lifecycle_payload
        else:
            base["portfolio_lifecycle"] = {
                "status": "ok",
                "run_id": lifecycle_payload.get("run_id"),
                "schema": lifecycle_payload.get("schema"),
            }
    else:
        base["portfolio_lifecycle"] = None

    if summary_payload is not None:
        if "error" in summary_payload and "headline_status" not in summary_payload:
            base["operator_summary"] = summary_payload
        else:
            base["operator_summary"] = {
                "status": "ok",
                "run_id": summary_payload.get("run_id"),
                "headline_status": summary_payload.get("headline_status"),
            }
    else:
        base["operator_summary"] = None

    if narrative_payload is not None:
        if "error" in narrative_payload and "sections" not in narrative_payload:
            base["operator_narrative"] = narrative_payload
        else:
            base["operator_narrative"] = {
                "status": "ok",
                "run_id": narrative_payload.get("run_id"),
            }
    else:
        base["operator_narrative"] = None

    base["stages_persisted"] = stages_persist

    lc_src = (
        lifecycle_payload
        if (
            lifecycle_payload
            and str(lifecycle_payload.get("schema")) == PORTFOLIO_LIFECYCLE_SCHEMA
            and "error" not in lifecycle_payload
        )
        else None
    )
    base["lifecycle_session_influence"] = build_lifecycle_session_influence(lc_src)
    return base


def render_portfolio_autonomous_session_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio autonomous runner session",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Session id:** `{payload.get('session_id')}`",
        f"**Started (UTC):** {payload.get('started_at_utc')}",
        f"**Finished (UTC):** {payload.get('finished_at_utc')}",
        "",
        "## Result",
        "",
        f"- **Cycles run:** {payload.get('cycles_run')}",
        f"- **Stop reason:** `{payload.get('stop_reason')}`",
        f"- **Codes:** {', '.join(f'`{c}`' for c in (payload.get('stop_reason_codes') or []))}",
        "",
    ]
    acp = payload.get("artifact_coherence_policy") or {}
    if isinstance(acp, dict) and acp.get("soft_brake_on_invalid_substrate"):
        lines.extend(
            [
                "## Artifact coherence (policy)",
                "",
                f"- **Stopped for invalid substrate:** {acp.get('stopped_for_invalid_substrate')}",
                f"- **Promotions suppressed (invalid substrate, allow_promotion was true):** "
                f"{acp.get('promotions_suppressed_for_invalid_substrate')}",
                "",
            ]
        )
        dsb = acp.get("degraded_substrate_policy") or {}
        if isinstance(dsb, dict) and dsb.get("schema") == "argus.degraded_substrate_policy.v1":
            lines.extend(
                [
                    "### Degraded substrate (yellow-light)",
                    "",
                    f"- **Consecutive degraded sessions (before → after):** "
                    f"{dsb.get('consecutive_degraded_sessions_before_session')} → "
                    f"{dsb.get('consecutive_degraded_sessions_after_session')}",
                    f"- **Session substrate overall:** `{dsb.get('session_substrate_overall_status')}`",
                    f"- **Promotions suppressed (degraded streak):** {dsb.get('promotions_suppressed_for_degraded_substrate')}",
                    f"- **Stopped for persistent degraded substrate:** {dsb.get('stopped_for_degraded_persistent_substrate')}",
                    "",
                ]
            )
            note = dsb.get("conservative_mode_note")
            if note:
                lines.append(f"- {note}")
                lines.append("")
    sia = payload.get("signal_instrumentation_advisory") or {}
    if isinstance(sia, dict) and (sia.get("notes") or sia.get("products_under_instrumentation_pressure")):
        lines.extend(
            [
                "## Signal instrumentation (advisory)",
                "",
            ]
        )
        pids = sia.get("products_under_instrumentation_pressure") or []
        if pids:
            lines.append(
                f"- **Products under instrumentation pressure:** {', '.join(f'`{p}`' for p in pids[:24])}"
            )
        for n in sia.get("notes") or []:
            lines.append(f"- {n}")
        lines.append("")
    ca = payload.get("confidence_adjustment") or {}
    if ca:
        lines.extend(
            [
                "## Confidence adjustment (autonomy memory)",
                "",
                f"- **Memory consulted:** {ca.get('autonomy_memory_consulted')}",
                f"- **Eligible:** {ca.get('eligible')}",
                f"- **Block reason:** `{ca.get('block_reason')}`",
                f"- **Patterns matched:** {ca.get('patterns_matched')}",
                f"- **Continuation consumed this session:** {ca.get('continuation_consumed')}",
                f"- **Configured max cycles:** {ca.get('configured_max_cycles')}",
                f"- **Effective max cycles (budget):** {ca.get('effective_max_cycles')}",
                "",
            ]
        )
        notes = ca.get("safe_to_adjust_notes") or []
        if notes:
            lines.append("### Why adjustment was or was not considered safe")
            lines.append("")
            for n in notes:
                lines.append(f"- {n}")
            lines.append("")
    lines.extend(
        [
            "## Dashboard refresh status",
            "",
        ]
    )
    ds = payload.get("dashboard_refresh_status") or {}
    oss = ds.get("operator_summary") if isinstance(ds.get("operator_summary"), dict) else {}
    onv = ds.get("operator_narrative") if isinstance(ds.get("operator_narrative"), dict) else {}
    lines.append(
        f"- **Operator summary:** status `{oss.get('status')}` · run_id `{oss.get('run_id')}` · "
        f"headline `{oss.get('headline_status')}`"
    )
    lines.append(
        f"- **Operator narrative:** status `{onv.get('status')}` · run_id `{onv.get('run_id')}`"
    )
    lsi = payload.get("lifecycle_session_influence") or {}
    lines.extend(
        [
            "",
            "## Lifecycle-aware context",
            "",
            f"- **Primary signal:** `{lsi.get('primary_signal')}`",
        ]
    )
    for note in lsi.get("session_notes") or []:
        lines.append(f"- {note}")
    lines.append("")
    lines.append("**Priority hints (soft, non-blocking):**")
    for h in lsi.get("priority_hints") or []:
        lines.append(f"- {h}")
    sc = lsi.get("stop_continue_context") or {}
    if sc:
        lines.append("")
        lines.append(f"**Stop / continue bias:** `{sc.get('bias')}` — {sc.get('note')}")
    lines.extend(["", "## Lifecycle promotions", ""])
    pa = payload.get("promotable_actions") or []
    lines.append(f"- **Promotable actions:** {len(pa)}")
    for a in pa[:12]:
        if not isinstance(a, dict):
            continue
        kind = a.get("kind")
        if kind == "creation_proposal_to_scaffold":
            lines.append(
                f"  - `{kind}` · proposal `{a.get('proposal_id')}` → product `{a.get('derived_product_id')}` "
                f"(auto-safe: {a.get('safe_for_auto')})"
            )
        elif kind == "scaffolded_product_to_bootstrap":
            lines.append(f"  - `{kind}` · `{a.get('product_id')}` (auto-safe: {a.get('safe_for_auto')})")
        elif kind == "deprecation_proposal_to_plan":
            lines.append(
                f"  - `{kind}` · proposal `{a.get('proposal_id')}` · product `{a.get('product_id')}` "
                f"(auto-safe: {a.get('safe_for_auto')})"
            )
        else:
            lines.append(f"  - `{kind}` — {a}")
    if len(pa) > 12:
        lines.append(f"  - _(… {len(pa) - 12} more)_")
    br = payload.get("blocked_promotions") or []
    lines.append(f"- **Blocked promotions:** {len(br)}")
    for b in br[:8]:
        if isinstance(b, dict):
            lines.append(
                f"  - `{b.get('kind')}` · {b.get('proposal_id') or b.get('product_id') or '—'} — {b.get('reason')}"
            )
    if len(br) > 8:
        lines.append(f"  - _(… {len(br) - 8} more)_")
    for r in (payload.get("promotion_recommendations") or [])[:6]:
        lines.append(f"- **Recommendation:** {r}")
    pex = payload.get("promotion_execution") or {}
    inp = payload.get("inputs") or {}
    lines.append("")
    lines.append("### Promotion execution")
    if not inp.get("allow_promotion"):
        lines.append("- Mode: detection only (`--allow-promotion` not set).")
    elif pex.get("skipped_reason"):
        lines.append(f"- Skipped: {pex.get('skipped_reason')}")
    else:
        lines.append(
            f"- **Allowed:** yes · **effective dry-run:** {pex.get('effective_dry_run')} · "
            f"**include bootstrap:** {pex.get('promotion_include_bootstrap')}"
        )
        for step in pex.get("steps") or []:
            if not isinstance(step, dict):
                continue
            st = step.get("result_status")
            lines.append(
                f"  - `{step.get('kind')}` → **{st}** — {step.get('detail')}"
            )
        if not pex.get("steps"):
            lines.append("  - _(no promotion steps ran — nothing matched or all prerequisites missing)_")
    lines.extend(
        [
            "",
            "## Session summary",
            "",
            str(payload.get("session_summary") or ""),
            "",
            "## Artifacts refreshed",
            "",
        ]
    )
    for p in payload.get("artifacts_refreshed") or []:
        lines.append(f"- `{p}`")
    if not (payload.get("artifacts_refreshed") or []):
        lines.append("- _(none)_")
    lines.extend(["", "## Per cycle", ""])
    for row in payload.get("per_cycle_outcomes") or []:
        if not isinstance(row, dict):
            continue
        pc = row.get("portfolio_cycle") or {}
        lines.append(
            f"- **#{row.get('cycle_index')}** · cycle `{pc.get('cycle_run_id')}` · "
            f"quiescence `{pc.get('quiescence_recommendation')}` · overall `{pc.get('overall_operator_recommendation')}`"
        )
    lines.extend(["", payload.get("guardrails_note", ""), ""])
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_autonomous_session_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    session_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    sid = session_id or str(payload.get("session_id") or "")
    if not sid:
        sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["session_id"] = sid
    d = portfolio_autonomous_runner_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{sid}.json"
    stamped_md = d / f"{sid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_autonomous_session_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


__all__ = [
    "PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA",
    "portfolio_autonomous_runner_dir",
    "render_portfolio_autonomous_session_markdown",
    "run_portfolio_autonomous_session",
    "write_portfolio_autonomous_session_artifacts",
]
