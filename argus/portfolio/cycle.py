"""
Bounded portfolio operator cycle — one full pass: queue → progression → quiescence → delta → intervention,
plus a synthesized top-level recommendation.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.policy.operator_policy import (
    load_operator_policy,
    write_operator_policy_effective_artifact,
)
from argus.portfolio.delta_report import run_portfolio_delta_report
from argus.portfolio.intervention import (
    INTERVENTION_CONTINUE_MONITORING,
    INTERVENTION_HUMAN_REVIEW,
    INTERVENTION_IMPORT_REPAIR,
    INTERVENTION_SAFE_TO_IGNORE,
    SEVERITY_HIGH,
    run_portfolio_intervention,
)
from argus.portfolio.operator_queue import build_operator_queue_payload, write_operator_queue
from argus.portfolio.progression import (
    DEFAULT_PROGRESS_LIMIT,
    run_portfolio_progression,
)
from argus.portfolio.quiescence import run_portfolio_quiescence
from argus.portfolio.strategy_influence import cycle_strategy_context, load_latest_strategic_posture

PORTFOLIO_CYCLE_SCHEMA = "argus.portfolio_cycle.v1"

OVERALL_RUN_AGAIN = "run_again"
OVERALL_WAIT = "wait"
OVERALL_INSPECT_PRODUCTS = "inspect_specific_products"
OVERALL_REPAIR_IMPORTS = "repair_imports"
OVERALL_HUMAN_REVIEW = "request_human_review"


def portfolio_cycle_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "cycle"


def _top_queue_product_ids(queue_payload: dict[str, Any], *, limit: int = 8) -> list[str]:
    entries = list(queue_payload.get("entries") or [])
    entries.sort(key=lambda e: (int(e.get("queue_rank") or 9999), str(e.get("product_id") or "")))
    out: list[str] = []
    for e in entries[:limit]:
        if isinstance(e, dict) and e.get("product_id"):
            out.append(str(e.get("product_id")))
    return out


def synthesize_overall_operator_recommendation(
    *,
    quiescence: dict[str, Any] | None,
    delta: dict[str, Any] | None,
    intervention: dict[str, Any] | None,
    repo_root: Path | None = None,
    operator_policy: dict[str, Any] | None = None,
) -> tuple[str, list[str]]:
    """
    Deterministic priority: human review → import repair → inspect → wait → run again.

    Returns ``(overall_operator_recommendation, rationale_codes)``.
    """
    codes: list[str] = []
    q = quiescence or {}
    d = delta or {}
    inv = intervention or {}

    benign: set[str] = {INTERVENTION_SAFE_TO_IGNORE, INTERVENTION_CONTINUE_MONITORING}
    delta_inspect_paths: set[str] = {"review_blockers", "review_regressions", "establish_baseline"}
    if operator_policy is not None:
        cyc = operator_policy["cycle"]
        benign = set(str(x) for x in (cyc.get("benign_intervention_categories") or []))
        delta_inspect_paths = set(str(x) for x in (cyc.get("delta_inspect_paths") or []))
    elif repo_root is not None:
        cyc = load_operator_policy(repo_root)["cycle"]
        benign = set(str(x) for x in (cyc.get("benign_intervention_categories") or []))
        delta_inspect_paths = set(str(x) for x in (cyc.get("delta_inspect_paths") or []))

    qrec = str(q.get("recommendation") or "")
    drec = str(d.get("recommended_next_portfolio_action") or "")
    stuck = q.get("products_stuck_or_repeating") or []
    if stuck:
        stuck = stuck if isinstance(stuck, list) else []
    flagged = [x for x in (inv.get("flagged_products") or []) if isinstance(x, dict)]

    if qrec == "human_review":
        codes.append("cycle.quiescence_recommendation_human_review")
        return OVERALL_HUMAN_REVIEW, codes
    if stuck:
        codes.append("cycle.quiescence_stuck_or_repeating")
        return OVERALL_HUMAN_REVIEW, codes

    for row in flagged:
        if str(row.get("intervention_category") or "") == INTERVENTION_HUMAN_REVIEW:
            codes.append("cycle.intervention_category_human_review")
            return OVERALL_HUMAN_REVIEW, codes
        if str(row.get("severity") or "") == SEVERITY_HIGH:
            codes.append("cycle.intervention_severity_high")
            return OVERALL_HUMAN_REVIEW, codes

    if qrec == "import_refresh":
        codes.append("cycle.quiescence_import_refresh")
        return OVERALL_REPAIR_IMPORTS, codes
    if drec == "inspect_import_health":
        codes.append("cycle.delta_inspect_import_health")
        return OVERALL_REPAIR_IMPORTS, codes
    for row in flagged:
        if str(row.get("intervention_category") or "") == INTERVENTION_IMPORT_REPAIR:
            codes.append("cycle.intervention_import_repair")
            return OVERALL_REPAIR_IMPORTS, codes

    if qrec == "inspect":
        codes.append("cycle.quiescence_inspect")
        return OVERALL_INSPECT_PRODUCTS, codes
    if drec in delta_inspect_paths:
        codes.append(f"cycle.delta_{drec}")
        return OVERALL_INSPECT_PRODUCTS, codes

    serious_flags = [f for f in flagged if str(f.get("intervention_category") or "") not in benign]
    if serious_flags:
        codes.append("cycle.intervention_non_benign_flags")
        return OVERALL_INSPECT_PRODUCTS, codes

    pq = bool(q.get("portfolio_quiescent"))
    if qrec == "wait" and pq:
        if not flagged or all(str(f.get("intervention_category") or "") in benign for f in flagged):
            codes.append("cycle.quiescent_and_clear")
            return OVERALL_WAIT, codes

    if qrec == "run_again":
        codes.append("cycle.quiescence_run_again")
        return OVERALL_RUN_AGAIN, codes
    if drec == "run_progression_or_queue":
        codes.append("cycle.delta_run_progression_or_queue")
        return OVERALL_RUN_AGAIN, codes

    if qrec == "wait":
        codes.append("cycle.quiescence_wait_fallback")
        return OVERALL_WAIT, codes

    codes.append("cycle.default_run_again")
    return OVERALL_RUN_AGAIN, codes


def _stage_error(stage: str, err: BaseException) -> dict[str, Any]:
    return {
        "status": "error",
        "stage": stage,
        "error": str(err),
        "error_type": type(err).__name__,
    }


def run_portfolio_cycle(
    repo_root: Path,
    *,
    limit: int = DEFAULT_PROGRESS_LIMIT,
    limit_history: int = 30,
    products_dir: Path | None = None,
    dry_run: bool = False,
    execute: bool = True,
    skip_import_failed: bool = False,
    skip_waiting: bool = False,
    write_cycle_artifacts: bool = True,
    write_stage_artifacts: bool = True,
    write_artifact_coherence: bool = False,
) -> dict[str, Any]:
    """
    One bounded operator cycle: build queue → progression → quiescence → delta report → intervention.

    Guardrails: ``limit`` caps progression breadth; ``dry_run`` avoids advancing writes inside progression;
    there is no internal loop — exactly one pass per stage.

    ``write_cycle_artifacts`` controls ``runs/portfolio/cycle/*`` only. ``write_stage_artifacts`` controls
    whether sub-stages persist their usual outputs under ``runs/portfolio/{operator_queue,progression,...}``.

    When ``write_artifact_coherence`` is true and ``write_cycle_artifacts`` is true, a read-only coherence
    report is written under ``runs/debug/artifact_coherence/`` (does not mutate portfolio truth artifacts).

    When all stages succeed and ``write_stage_artifacts`` is true, the cycle also refreshes **satellite**
    artifacts so downstream readers (lifecycle, operator summary, strategy, artifact coherence) see coherent
    ``latest.json`` for outcomes, portfolio patterns, intervention inbox, portfolio strategy, and escalation inbox.
    """
    root = repo_root.resolve()
    lim_hist = max(1, int(limit_history))
    if limit < 0:
        raise ValueError("limit must be >= 0")

    run_started = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    stages: dict[str, Any] = {}
    queue_payload: dict[str, Any] | None = None
    progression_payload: dict[str, Any] | None = None
    quiescence_payload: dict[str, Any] | None = None
    delta_payload: dict[str, Any] | None = None
    intervention_payload: dict[str, Any] | None = None

    # 1) Operator queue
    try:
        queue_payload = build_operator_queue_payload(root, products_dir=products_dir)
        if write_stage_artifacts:
            write_operator_queue(root, products_dir=products_dir, payload=queue_payload)
        qc = len(queue_payload.get("entries") or [])
        stages["operator_queue"] = {
            "status": "ok",
            "entry_count": qc,
            "generated_at_utc": queue_payload.get("generated_at_utc"),
            "artifacts_written": bool(write_stage_artifacts),
        }
    except Exception as e:
        stages["operator_queue"] = _stage_error("operator_queue", e)

    # 2) Portfolio progression
    try:
        progression_payload = run_portfolio_progression(
            root,
            limit=limit,
            products_dir=products_dir,
            dry_run=dry_run,
            execute=execute,
            skip_import_failed=skip_import_failed,
            skip_waiting=skip_waiting,
            write_artifacts=write_stage_artifacts,
        )
        sc = progression_payload.get("summary_counts") or {}
        stages["portfolio_progression"] = {
            "status": "ok",
            "run_id": progression_payload.get("run_id"),
            "dry_run": bool(progression_payload.get("dry_run")),
            "limit": progression_payload.get("limit"),
            "summary_counts": sc,
            "artifacts_written": bool(write_stage_artifacts),
        }
    except Exception as e:
        stages["portfolio_progression"] = _stage_error("portfolio_progression", e)

    # 2b) Rebuild operator queue after progression so latest.json matches refreshed orchestration
    if (
        isinstance(stages.get("operator_queue"), dict)
        and stages["operator_queue"].get("status") == "ok"
        and isinstance(stages.get("portfolio_progression"), dict)
        and stages["portfolio_progression"].get("status") == "ok"
        and write_stage_artifacts
        and not dry_run
    ):
        try:
            queue_payload = build_operator_queue_payload(root, products_dir=products_dir)
            write_operator_queue(root, products_dir=products_dir, payload=queue_payload)
            stages["operator_queue"]["post_progression_realign"] = {
                "applied": True,
                "generated_at_utc": queue_payload.get("generated_at_utc"),
                "note": (
                    "Queue rebuilt after portfolio progression so entries align with durable "
                    "runs/orchestration/latest/* and operator snapshots updated during advancement."
                ),
            }
            stages["operator_queue"]["generated_at_utc"] = queue_payload.get("generated_at_utc")
            stages["operator_queue"]["entry_count"] = len(queue_payload.get("entries") or [])
        except Exception as e:
            stages["operator_queue"]["post_progression_realign"] = {
                "applied": False,
                "error": str(e),
                "error_type": type(e).__name__,
            }

    # 3) Quiescence
    try:
        quiescence_payload = run_portfolio_quiescence(root, write_artifacts=write_stage_artifacts)
        pm = quiescence_payload.get("products_with_material_change") or []
        stages["portfolio_quiescence"] = {
            "status": "ok",
            "recommendation": quiescence_payload.get("recommendation"),
            "portfolio_quiescent": quiescence_payload.get("portfolio_quiescent"),
            "products_with_material_change_count": len(pm) if isinstance(pm, list) else 0,
            "artifacts_written": bool(write_stage_artifacts),
        }
    except Exception as e:
        stages["portfolio_quiescence"] = _stage_error("portfolio_quiescence", e)

    # 4) Delta report
    try:
        delta_payload = run_portfolio_delta_report(root, write_artifacts=write_stage_artifacts)
        mat = delta_payload.get("products_with_material_change") or []
        stages["portfolio_delta_report"] = {
            "status": "ok",
            "run_id": delta_payload.get("run_id"),
            "recommended_next_portfolio_action": delta_payload.get("recommended_next_portfolio_action"),
            "products_with_material_change_count": len(mat) if isinstance(mat, list) else 0,
            "artifacts_written": bool(write_stage_artifacts),
        }
    except Exception as e:
        stages["portfolio_delta_report"] = _stage_error("portfolio_delta_report", e)

    # 5) Intervention
    try:
        intervention_payload = run_portfolio_intervention(root, write_artifacts=write_stage_artifacts)
        flagged = intervention_payload.get("flagged_products") or []
        benign = intervention_payload.get("stable_benign_products") or []
        stages["portfolio_intervention"] = {
            "status": "ok",
            "run_id": intervention_payload.get("run_id"),
            "flagged_count": len(flagged) if isinstance(flagged, list) else 0,
            "stable_benign_count": len(benign) if isinstance(benign, list) else 0,
            "artifacts_written": bool(write_stage_artifacts),
        }
    except Exception as e:
        stages["portfolio_intervention"] = _stage_error("portfolio_intervention", e)

    all_ok = all(
        isinstance(stages.get(k), dict) and stages[k].get("status") == "ok"
        for k in (
            "operator_queue",
            "portfolio_progression",
            "portfolio_quiescence",
            "portfolio_delta_report",
            "portfolio_intervention",
        )
    )

    satellite_refresh: dict[str, Any] = {}
    if all_ok and write_stage_artifacts:
        from argus.portfolio.escalation_inbox import run_escalation_inbox
        from argus.portfolio.intervention_inbox import run_intervention_inbox
        from argus.portfolio.outcomes import run_portfolio_outcomes
        from argus.portfolio.patterns import run_portfolio_patterns
        from argus.portfolio.strategy import (
            build_portfolio_strategy_payload,
            write_portfolio_strategy_artifacts,
        )

        op_outcomes: dict[str, Any] | None = None
        inbox_pl: dict[str, Any] | None = None
        pat_pl: dict[str, Any] | None = None
        try:
            op_outcomes = run_portfolio_outcomes(root, limit_history=lim_hist, write_artifacts=True)
            satellite_refresh["portfolio_outcomes"] = {
                "status": "ok",
                "run_id": op_outcomes.get("run_id"),
            }
        except Exception as e:
            satellite_refresh["portfolio_outcomes"] = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
        if op_outcomes is not None:
            try:
                pat_pl = run_portfolio_patterns(
                    root, limit_history=lim_hist, products_dir=products_dir, write_artifacts=True
                )
                satellite_refresh["portfolio_patterns"] = {
                    "status": "ok",
                    "run_id": pat_pl.get("run_id"),
                }
            except Exception as e:
                satellite_refresh["portfolio_patterns"] = {
                    "status": "error",
                    "error": str(e),
                    "error_type": type(e).__name__,
                }
        else:
            satellite_refresh["portfolio_patterns"] = {
                "status": "skipped",
                "reason": "portfolio_outcomes_unavailable",
            }
        try:
            inbox_pl = run_intervention_inbox(
                root,
                write_artifacts=True,
                intervention_report=intervention_payload,
            )
            satellite_refresh["intervention_inbox"] = {
                "status": "ok",
                "source_intervention_run_id": inbox_pl.get("source_intervention_run_id"),
            }
        except Exception as e:
            satellite_refresh["intervention_inbox"] = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
        try:
            if op_outcomes is not None and inbox_pl is not None and queue_payload is not None:
                strat_pl = build_portfolio_strategy_payload(
                    root,
                    limit_history=lim_hist,
                    products_dir=products_dir,
                    outcomes_payload=op_outcomes,
                    intervention_inbox_payload=inbox_pl,
                    operator_queue_payload=queue_payload,
                    patterns_payload=pat_pl,
                )
                write_portfolio_strategy_artifacts(root, strat_pl)
                satellite_refresh["portfolio_strategy"] = {
                    "status": "ok",
                    "run_id": strat_pl.get("run_id"),
                }
            else:
                satellite_refresh["portfolio_strategy"] = {
                    "status": "skipped",
                    "reason": "missing_prerequisite_payload",
                }
        except Exception as e:
            satellite_refresh["portfolio_strategy"] = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
        try:
            esc_pl = run_escalation_inbox(root, write_artifacts=True)
            satellite_refresh["escalation_inbox"] = {
                "status": "ok",
                "open_items_count": len(esc_pl.get("open_items") or []),
            }
        except Exception as e:
            satellite_refresh["escalation_inbox"] = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }

    overall, rationale_codes = synthesize_overall_operator_recommendation(
        quiescence=quiescence_payload,
        delta=delta_payload,
        intervention=intervention_payload,
        repo_root=root,
    )

    strat_posture, _strat_raw = load_latest_strategic_posture(root)

    q_summary: dict[str, Any] = {}
    portfolio_mission: dict[str, Any] | None = None
    if queue_payload:
        q_entries = queue_payload.get("entries") or []
        qpids = [str(e.get("product_id") or "") for e in q_entries if isinstance(e, dict) and e.get("product_id")]
        portfolio_mission = (
            queue_payload.get("portfolio_mission_provenance")
            if isinstance(queue_payload.get("portfolio_mission_provenance"), dict)
            else build_portfolio_mission_provenance(root, qpids)
        )
        q_summary = {
            "entry_count": len(q_entries),
            "top_product_ids": _top_queue_product_ids(queue_payload),
            "generated_at_utc": queue_payload.get("generated_at_utc"),
        }

    prog_summary: dict[str, Any] = {}
    if progression_payload:
        prog_summary = {
            "run_id": progression_payload.get("run_id"),
            "generated_at_utc": progression_payload.get("generated_at_utc"),
            "dry_run": progression_payload.get("dry_run"),
            "limit": progression_payload.get("limit"),
            "summary_counts": progression_payload.get("summary_counts") or {},
        }

    qn_summary: dict[str, Any] = {}
    if quiescence_payload:
        qn_summary = {
            "recommendation": quiescence_payload.get("recommendation"),
            "portfolio_quiescent": quiescence_payload.get("portfolio_quiescent"),
            "products_with_material_change": quiescence_payload.get("products_with_material_change") or [],
        }

    delta_summary: dict[str, Any] = {}
    if delta_payload:
        delta_summary = {
            "run_id": delta_payload.get("run_id"),
            "recommended_next_portfolio_action": delta_payload.get("recommended_next_portfolio_action"),
            "products_with_material_change": delta_payload.get("products_with_material_change") or [],
            "what_improved": delta_payload.get("what_improved") or [],
            "what_regressed": delta_payload.get("what_regressed") or [],
        }

    inv_summary: dict[str, Any] = {}
    if intervention_payload:
        inv_summary = {
            "run_id": intervention_payload.get("run_id"),
            "flagged_products": intervention_payload.get("flagged_products") or [],
            "stable_benign_products": intervention_payload.get("stable_benign_products") or [],
        }

    rel = Path("runs") / "portfolio"
    artifact_paths: dict[str, Any] = {
        "operator_queue": {"latest_json": str(rel / "operator_queue" / "latest.json")},
        "portfolio_progression": {
            "latest_json": str(rel / "progression" / "latest.json"),
            "stamped_json": (
                str(rel / "progression" / f"{progression_payload.get('run_id')}.json")
                if progression_payload and progression_payload.get("run_id")
                else None
            ),
        },
        "portfolio_quiescence": {"latest_json": str(rel / "quiescence" / "latest.json")},
        "portfolio_delta_report": {
            "latest_json": str(rel / "delta_report" / "latest.json"),
            "stamped_json": (
                str(rel / "delta_report" / f"{delta_payload.get('run_id')}.json")
                if delta_payload and delta_payload.get("run_id")
                else None
            ),
        },
        "portfolio_intervention": {
            "latest_json": str(rel / "intervention" / "latest.json"),
            "stamped_json": (
                str(rel / "intervention" / f"{intervention_payload.get('run_id')}.json")
                if intervention_payload and intervention_payload.get("run_id")
                else None
            ),
        },
    }

    out: dict[str, Any] = {
        "schema": PORTFOLIO_CYCLE_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": run_started,
        "portfolio_mission_provenance": portfolio_mission,
        "ok": all_ok,
        "inputs": {
            "limit": limit,
            "limit_history": lim_hist,
            "dry_run": dry_run,
            "execute": execute,
            "skip_import_failed": skip_import_failed,
            "skip_waiting": skip_waiting,
            "write_stage_artifacts": write_stage_artifacts,
            "write_artifact_coherence": write_artifact_coherence,
        },
        "stages": stages,
        "satellite_refresh": satellite_refresh,
        "summary": {
            "queue": q_summary,
            "progression": prog_summary,
            "quiescence": qn_summary,
            "material_deltas": delta_summary,
            "intervention": inv_summary,
            "overall_operator_recommendation": overall,
            "overall_rationale_codes": rationale_codes,
            "portfolio_strategy_context": cycle_strategy_context(strat_posture),
        },
        "artifact_paths": artifact_paths,
    }

    ac_ref: dict[str, Any] = {"status": "skipped", "reason": "disabled"}
    if write_artifact_coherence and write_cycle_artifacts:
        try:
            from argus.portfolio.artifact_coherence import (
                artifact_coherence_dir,
                run_artifact_coherence,
            )

            ac_payload = run_artifact_coherence(
                root, products_dir=products_dir, write_artifacts=True
            )
            ac_ref = {
                "status": "ok",
                "overall_status": ac_payload.get("overall_status"),
                "run_id": ac_payload.get("run_id"),
                "paths": {
                    "latest_json": str(artifact_coherence_dir(root) / "latest.json"),
                    "latest_md": str(artifact_coherence_dir(root) / "latest.md"),
                },
            }
        except Exception as e:
            ac_ref = {
                "status": "error",
                "error": str(e),
                "error_type": type(e).__name__,
            }
    out["artifact_coherence"] = ac_ref

    if write_cycle_artifacts:
        write_portfolio_cycle_artifacts(root, out, run_id=run_id)
    return out


def render_portfolio_cycle_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio operator cycle",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Generated (UTC):** {payload.get('generated_at_utc')}",
        f"**All stages ok:** {payload.get('ok')}",
        "",
    ]
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    lines.extend(
        [
            "## Inputs",
            "",
        ]
    )
    inp = payload.get("inputs") or {}
    lines.append(f"- **limit:** {inp.get('limit')} · **dry_run:** {inp.get('dry_run')} · **execute:** {inp.get('execute')}")
    lines.append(
        f"- **skip_import_failed:** {inp.get('skip_import_failed')} · **skip_waiting:** {inp.get('skip_waiting')}"
    )
    ac = payload.get("artifact_coherence") or {}
    if ac.get("status") == "ok":
        p = (ac.get("paths") or {}).get("latest_json")
        lines.append(
            f"- **artifact_coherence:** `{ac.get('overall_status')}`"
            + (f" → `{p}`" if p else "")
        )
    elif ac.get("status") == "error":
        lines.append(f"- **artifact_coherence:** ERROR — {ac.get('error')}")
    lines.extend(["", "## Stage status", ""])
    st = payload.get("stages") or {}
    for name in (
        "operator_queue",
        "portfolio_progression",
        "portfolio_quiescence",
        "portfolio_delta_report",
        "portfolio_intervention",
    ):
        row = st.get(name)
        if not isinstance(row, dict):
            lines.append(f"- **{name}:** —")
            continue
        if row.get("status") == "ok":
            lines.append(f"- **{name}:** ok")
        else:
            lines.append(f"- **{name}:** ERROR — {row.get('error')}")
    summ = payload.get("summary") or {}
    psc = summ.get("portfolio_strategy_context") or {}
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- **Overall operator recommendation:** `{summ.get('overall_operator_recommendation')}`",
            f"- **Rationale codes:** {', '.join(f'`{c}`' for c in (summ.get('overall_rationale_codes') or [])) or '—'}",
        ]
    )
    if psc.get("operator_cycle_note"):
        lines.append(f"- **Portfolio strategy:** {psc.get('operator_cycle_note')}")
    lines.extend(
        [
            "",
            "### Queue",
            "",
        ]
    )
    q = summ.get("queue") or {}
    lines.append(f"- Entries: **{q.get('entry_count', '—')}** · Top: {', '.join(f'`{p}`' for p in (q.get('top_product_ids') or [])) or '—'}")
    pg = summ.get("progression") or {}
    lines.extend(
        [
            "",
            "### Progression",
            "",
            f"- **run_id:** `{pg.get('run_id')}` · **dry_run:** {pg.get('dry_run')}",
            f"- **summary_counts:** `{pg.get('summary_counts')}`",
            "",
            "### Quiescence",
            "",
            f"- **recommendation:** `{summ.get('quiescence', {}).get('recommendation')}`",
            f"- **portfolio_quiescent:** {summ.get('quiescence', {}).get('portfolio_quiescent')}",
            "",
            "### Material deltas (delta report)",
            "",
            f"- **recommended_next_portfolio_action:** `{summ.get('material_deltas', {}).get('recommended_next_portfolio_action')}`",
            "",
            "### Intervention",
            "",
        ]
    )
    inv = summ.get("intervention") or {}
    flagged = inv.get("flagged_products") or []
    lines.append(f"- **Flagged products:** {len(flagged) if isinstance(flagged, list) else 0}")
    lines.extend(["", "## Artifact paths", ""])
    ap = payload.get("artifact_paths") or {}
    for k, v in ap.items():
        lines.append(f"- **{k}:** `{v}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_cycle_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    write_operator_policy_effective_artifact(root)
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = portfolio_cycle_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_cycle_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md
