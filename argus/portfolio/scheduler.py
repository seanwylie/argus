"""
Bounded autonomous portfolio cycle scheduler — repeated ``run_portfolio_cycle`` with explicit stop guardrails.

Does not spawn subprocesses; calls portfolio cycle in-process. Session artifacts record why the loop stopped.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.cycle import run_portfolio_cycle
from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle
from argus.portfolio.lifecycle_session_influence import build_lifecycle_session_influence
from argus.portfolio.outcomes import evaluate_portfolio_outcomes

PORTFOLIO_SCHEDULER_SESSION_SCHEMA = "argus.portfolio_scheduler_session.v1"

# Explicit stop: quiescence says pause work or fix imports before more blind cycles
QUIESCENCE_STOP_RECOMMENDATIONS = frozenset(
    {
        "wait",
        "inspect",
        "import_refresh",
        "human_review",
    }
)

# Cycle synthesis: stop when human/import/inspect required before more automation
CYCLE_OVERALL_STOP = frozenset(
    {
        "request_human_review",
        "repair_imports",
        "inspect_specific_products",
    }
)

DEFAULT_MAX_CYCLES = 5
DEFAULT_NO_MATERIAL_CHANGE_STREAK = 3
DEFAULT_INTERVENTION_FLAGGED_THRESHOLD = 4
DEFAULT_INTERVENTION_HEAVY_STREAK = 2


def portfolio_scheduler_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "scheduler"


def _quiescence_rec(cycle_payload: dict[str, Any]) -> str | None:
    summ = (cycle_payload.get("summary") or {}).get("quiescence") or {}
    r = summ.get("recommendation")
    return str(r).strip() if r else None


def _material_change_count(cycle_payload: dict[str, Any]) -> int:
    st = cycle_payload.get("stages") or {}
    q = st.get("portfolio_quiescence") or {}
    if isinstance(q, dict) and q.get("status") == "ok":
        return int(q.get("products_with_material_change_count") or 0)
    return 0


def _flagged_count(cycle_payload: dict[str, Any]) -> int:
    summ = (cycle_payload.get("summary") or {}).get("intervention") or {}
    fp = summ.get("flagged_products") or []
    return len(fp) if isinstance(fp, list) else 0


def _overall_rec(cycle_payload: dict[str, Any]) -> str | None:
    summ = cycle_payload.get("summary") or {}
    o = summ.get("overall_operator_recommendation")
    return str(o).strip() if o else None


def _per_cycle_record(cycle_payload: dict[str, Any]) -> dict[str, Any]:
    summ = cycle_payload.get("summary") or {}
    prog = summ.get("progression") or {}
    sc = prog.get("summary_counts") if isinstance(prog, dict) else {}
    return {
        "cycle_run_id": cycle_payload.get("run_id"),
        "ok": cycle_payload.get("ok"),
        "quiescence_recommendation": _quiescence_rec(cycle_payload),
        "portfolio_quiescent": (summ.get("quiescence") or {}).get("portfolio_quiescent"),
        "products_with_material_change_count": _material_change_count(cycle_payload),
        "intervention_flagged_count": _flagged_count(cycle_payload),
        "overall_operator_recommendation": _overall_rec(cycle_payload),
        "progression_summary_counts": sc if isinstance(sc, dict) else {},
    }


def run_portfolio_scheduler_session(
    repo_root: Path,
    *,
    max_cycles: int = DEFAULT_MAX_CYCLES,
    limit_per_cycle: int = 5,
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
) -> dict[str, Any]:
    """
    Run up to ``max_cycles`` portfolio cycles, stopping on guardrails.

    ``write_session_artifacts`` controls ``runs/portfolio/scheduler/*`` only.
    ``write_stage_artifacts`` is passed through to each ``run_portfolio_cycle`` (stage + cycle JSON under portfolio/).
    """
    root = repo_root.resolve()
    session_started = datetime.now(timezone.utc).isoformat()
    session_run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    max_c = max(1, int(max_cycles))
    lim = max(0, int(limit_per_cycle))
    streak_limit = max(1, int(no_material_change_streak_limit))
    int_thresh = max(1, int(intervention_flagged_threshold))
    int_streak_need = max(1, int(intervention_heavy_streak))

    cycles_run = 0
    cycle_run_ids: list[str] = []
    per_cycle: list[dict[str, Any]] = []
    stop_reason = "unknown"
    stop_reason_codes: list[str] = []

    no_mat_streak = 0
    int_heavy_streak = 0

    sched_dir = portfolio_scheduler_dir(root)
    stop_sentinel = sched_dir / "STOP"

    lifecycle_session_influence: dict[str, Any]
    try:
        _lc_payload = evaluate_portfolio_lifecycle(root, products_dir=products_dir)
        lifecycle_session_influence = build_lifecycle_session_influence(_lc_payload)
    except Exception as e:
        lifecycle_session_influence = build_lifecycle_session_influence(None)
        lifecycle_session_influence["session_notes"] = [
            f"Lifecycle synthesis unavailable for this session ({type(e).__name__}: {e}). "
            "Influence defaults to neutral; re-run after inventory is valid."
        ] + list(lifecycle_session_influence.get("session_notes") or [])

    for _ in range(max_c):
        if check_stop_sentinel and cycles_run > 0 and stop_sentinel.is_file():
            stop_reason = "explicit_stop_sentinel"
            stop_reason_codes.append("scheduler.stop.sentinel_file")
            break

        payload = run_portfolio_cycle(
            root,
            limit=lim,
            products_dir=products_dir,
            dry_run=bool(dry_run),
            execute=True,
            skip_import_failed=bool(skip_import_failed),
            skip_waiting=bool(skip_waiting),
            write_cycle_artifacts=write_stage_artifacts,
            write_stage_artifacts=write_stage_artifacts,
        )
        cycles_run += 1
        rid = str(payload.get("run_id") or "")
        if rid:
            cycle_run_ids.append(rid)
        per_cycle.append(_per_cycle_record(payload))

        ov = _overall_rec(payload)
        if ov and ov in CYCLE_OVERALL_STOP:
            stop_reason = "cycle_overall_recommendation"
            stop_reason_codes.append(f"scheduler.stop.cycle_overall.{ov}")
            break

        qrec = _quiescence_rec(payload)
        if qrec and qrec in QUIESCENCE_STOP_RECOMMENDATIONS:
            stop_reason = "quiescence_recommendation"
            stop_reason_codes.append(f"scheduler.stop.quiescence.{qrec}")
            break

        mat = _material_change_count(payload)
        if mat == 0:
            no_mat_streak += 1
        else:
            no_mat_streak = 0
        if no_mat_streak >= streak_limit:
            stop_reason = "no_material_change_streak"
            stop_reason_codes.append(
                f"scheduler.stop.no_material_change_streak_n={streak_limit}"
            )
            break

        fc = _flagged_count(payload)
        if fc >= int_thresh:
            int_heavy_streak += 1
        else:
            int_heavy_streak = 0
        if int_heavy_streak >= int_streak_need:
            stop_reason = "intervention_heavy_streak"
            stop_reason_codes.append(
                f"scheduler.stop.intervention_flagged>={int_thresh}_for_{int_streak_need}_cycles"
            )
            break

    else:
        stop_reason = "max_cycles_reached"
        stop_reason_codes.append(f"scheduler.stop.max_cycles={max_c}")

    progression_totals: dict[str, int] = {}
    intervention_flagged_max = 0
    for pc in per_cycle:
        if not isinstance(pc, dict):
            continue
        fc = pc.get("intervention_flagged_count")
        if isinstance(fc, int):
            intervention_flagged_max = max(intervention_flagged_max, fc)
        sc = pc.get("progression_summary_counts")
        if isinstance(sc, dict):
            for k, v in sc.items():
                try:
                    progression_totals[k] = progression_totals.get(k, 0) + int(v)
                except (TypeError, ValueError):
                    continue
    if intervention_flagged_max:
        progression_totals["intervention_flagged_max"] = intervention_flagged_max

    outcomes_snapshot: dict[str, Any] | None = None
    if cycles_run > 0:
        try:
            outcomes_snapshot = evaluate_portfolio_outcomes(
                root, limit_history=min(30, cycles_run + 10)
            )
        except Exception as e:
            outcomes_snapshot = {"error": str(e), "error_type": type(e).__name__}

    session_payload: dict[str, Any] = {
        "schema": PORTFOLIO_SCHEDULER_SESSION_SCHEMA,
        "run_id": session_run_id,
        "session_started_utc": session_started,
        "inputs": {
            "max_cycles": max_c,
            "limit_per_cycle": lim,
            "dry_run": dry_run,
            "skip_import_failed": skip_import_failed,
            "skip_waiting": skip_waiting,
            "check_stop_sentinel": check_stop_sentinel,
            "no_material_change_streak_limit": streak_limit,
            "intervention_flagged_threshold": int_thresh,
            "intervention_heavy_streak_cycles": int_streak_need,
        },
        "cycles_run": cycles_run,
        "cycle_run_ids": cycle_run_ids,
        "stop_reason": stop_reason,
        "stop_reason_codes": stop_reason_codes,
        "per_cycle": per_cycle,
        "session_summary": {
            "progression_aggregates": progression_totals,
            "outcomes_end_of_session": outcomes_snapshot,
        },
        "lifecycle_session_influence": lifecycle_session_influence,
        "guardrails_note": (
            "Scheduler never runs unbounded loops: max_cycles caps iterations; "
            "quiescence/cycle/intervention/material-change rules stop early."
        ),
    }

    if write_session_artifacts:
        write_portfolio_scheduler_session_artifacts(root, session_payload)

    return session_payload


def render_portfolio_scheduler_session_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio scheduler session",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Session id:** `{payload.get('run_id')}`",
        f"**Started (UTC):** {payload.get('session_started_utc')}",
        "",
        "## Result",
        "",
        f"- **Cycles run:** {payload.get('cycles_run')}",
        f"- **Stop reason:** `{payload.get('stop_reason')}`",
        f"- **Codes:** {', '.join(f'`{c}`' for c in (payload.get('stop_reason_codes') or []))}",
        "",
        "## Lifecycle-aware context",
        "",
    ]
    lsi = payload.get("lifecycle_session_influence") or {}
    lines.append(f"- **Primary signal:** `{lsi.get('primary_signal')}`")
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
    lines.extend(
        [
            "",
            "## Per cycle",
            "",
            "| # | Cycle run id | Quiescence | Material Δ products | Flagged | Overall |",
            "|---|--------------|------------|---------------------|---------|---------|",
        ]
    )
    for i, row in enumerate(payload.get("per_cycle") or [], start=1):
        if not isinstance(row, dict):
            continue
        lines.append(
            f"| {i} | `{row.get('cycle_run_id')}` | `{row.get('quiescence_recommendation')}` | "
            f"{row.get('products_with_material_change_count')} | {row.get('intervention_flagged_count')} | "
            f"`{row.get('overall_operator_recommendation')}` |"
        )
    lines.extend(["", payload.get("guardrails_note", ""), ""])
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_scheduler_session_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = portfolio_scheduler_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_scheduler_session_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md
