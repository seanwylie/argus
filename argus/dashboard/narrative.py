"""
Cross-cycle operator narrative — concise story from portfolio history, outcomes, deltas, patterns, interventions, cycles.

Read-only: aggregates existing artifacts; does not run pipeline stages.
"""

from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.portfolio.artifact_coherence import load_artifact_coherence_operational_snapshot
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA
from argus.portfolio.history import (
    evaluate_portfolio_history,
    load_latest_n_artifacts,
)
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA
from argus.portfolio.outcomes import load_canonical_portfolio_outcomes
from argus.portfolio.patterns import evaluate_portfolio_patterns
from argus.products.inventory import build_inventory

OPERATOR_NARRATIVE_SCHEMA = "argus.operator_narrative.v1"


def _substrate_integrity_section(snap: dict[str, Any]) -> dict[str, Any]:
    if not snap.get("present"):
        return {"coherence_artifact_present": False}
    return {
        "coherence_artifact_present": True,
        "overall_status": snap.get("overall_status"),
        "summary": snap.get("summary"),
    }


def _substrate_caveat_line(snap: dict[str, Any]) -> str:
    if not snap.get("present"):
        return ""
    os = str(snap.get("overall_status") or "")
    if os == "invalid":
        return (
            "Artifact coherence is invalid; downstream readings may be unreliable until "
            "canonical artifacts are refreshed."
        )
    if os in ("degraded", "warning"):
        return "Substrate integrity is degraded; strategy should be interpreted cautiously."
    return ""


def operator_narrative_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "dashboard" / "narrative"


def _schema_ok(payload: dict[str, Any], schema: str) -> bool:
    return str(payload.get("schema") or "") == schema


def _cycle_recommendation_counts(
    cycles: list[tuple[str, dict[str, Any]]],
) -> dict[str, int]:
    c: Counter[str] = Counter()
    for _rid, pl in cycles:
        summ = pl.get("summary") or {}
        o = str(summ.get("overall_operator_recommendation") or "unknown").strip() or "unknown"
        c[o] += 1
    return dict(c)


def _flagged_counts(interventions: list[tuple[str, dict[str, Any]]]) -> list[int]:
    out: list[int] = []
    for _rid, pl in interventions:
        if not _schema_ok(pl, PORTFOLIO_INTERVENTION_SCHEMA):
            continue
        fp = pl.get("flagged_products") or []
        out.append(len(fp) if isinstance(fp, list) else 0)
    return out


def _intervention_load_note(counts: list[int]) -> str | None:
    if len(counts) < 4:
        return None
    mid = len(counts) // 2
    older = sum(counts[:mid]) / max(1, mid)
    recent = sum(counts[mid:]) / max(1, len(counts) - mid)
    if recent > older * 1.2:
        return (
            f"Intervention load is up: average flagged products per stamped run "
            f"~{recent:.1f} recently vs ~{older:.1f} earlier (same window)."
        )
    if recent < older * 0.8:
        return (
            f"Intervention load is down: average flagged products per run "
            f"~{recent:.1f} recently vs ~{older:.1f} earlier."
        )
    return None


def _delta_product_sets(
    deltas: list[tuple[str, dict[str, Any]]],
) -> tuple[set[str], set[str]]:
    improved: set[str] = set()
    regressed: set[str] = set()
    for _rid, pl in deltas:
        if not _schema_ok(pl, PORTFOLIO_DELTA_REPORT_SCHEMA):
            continue
        for row in pl.get("what_improved") or []:
            if isinstance(row, dict) and row.get("product_id"):
                improved.add(str(row["product_id"]))
        for row in pl.get("what_regressed") or []:
            if isinstance(row, dict) and row.get("product_id"):
                regressed.add(str(row["product_id"]))
    return improved, regressed


def _signal_instrumentation_lines(lifecycle_pl: dict[str, Any]) -> list[str]:
    """Deterministic lines when lifecycle reports instrumentation pressure."""
    from argus.portfolio.lifecycle import PORTFOLIO_LIFECYCLE_SCHEMA

    if str(lifecycle_pl.get("schema") or "") != PORTFOLIO_LIFECYCLE_SCHEMA:
        return []
    pids = lifecycle_pl.get("products_under_instrumentation_pressure") or []
    followup = lifecycle_pl.get("products_instrumentation_apply_followup") or []
    resolved = lifecycle_pl.get("products_instrumentation_resolved_via_apply") or []
    lines: list[str] = []
    fu_set = set(followup)
    eff_set = set(pids)
    need_only = sorted(eff_set - fu_set)
    fu_eff = sorted(eff_set & fu_set)
    if fu_eff:
        sample = ", ".join(f"`{x}`" for x in fu_eff[:10])
        lines.append(
            f"{len(fu_eff)} product(s) have worker instrumentation applies that are partial or still weak post-apply "
            f"({sample}) — keep enriching real telemetry; inspect loops may stay noisy until signals arrive."
        )
    if need_only:
        sample = ", ".join(f"`{x}`" for x in need_only[:10])
        lines.append(
            f"{len(need_only)} product(s) still need stronger signal instrumentation contracts before optimization-style "
            f"work ({sample})."
        )
    if resolved:
        sample = ", ".join(f"`{x}`" for x in resolved[:10])
        lines.append(
            f"{len(resolved)} product(s) have adequate post-apply worker validation while the latest scan artifact may "
            f"still look weak — prefer learning from signals over assuming missing observability ({sample})."
        )
    if not lines:
        return []
    lines.append(
        "See `runs/products/signal_instrumentation/latest/` and `runs/products/signal_instrumentation_apply/latest/` "
        "for scan vs apply context."
    )
    return lines


def _lifecycle_transition_lines(lifecycle_pl: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    lc = lifecycle_pl.get("lifecycle_counts") or {}
    proposed, inc = int(lc.get("proposed") or 0), int(lc.get("incubating") or 0)
    if proposed or inc:
        lines.append(f"Creation/early lane: {proposed} proposed concept(s) not yet scaffolded, {inc} incubating.")
    rep = int(lc.get("repairing") or 0)
    if rep:
        lines.append(f"Repair-first lane: {rep} product(s) under deprecation repair_instead posture.")
    wd = (
        int(lc.get("retiring") or 0)
        + int(lc.get("harvesting") or 0)
        + int(lc.get("archived_candidate") or 0)
    )
    if wd:
        lines.append(
            f"Wind-down lanes: {lc.get('retiring', 0)} retiring, {lc.get('harvesting', 0)} harvesting, "
            f"{lc.get('archived_candidate', 0)} archive-candidate (from lifecycle synthesis)."
        )
    ent = lifecycle_pl.get("products_entering") or []
    if ent:
        sample = ", ".join(f"`{x}`" for x in ent[:12])
        lines.append(f"Entering: {sample}.")
    ex = lifecycle_pl.get("products_exiting") or []
    if ex:
        sample = ", ".join(f"`{x}`" for x in ex[:12])
        lines.append(f"Exit-oriented: {sample}.")
    strat = lifecycle_pl.get("portfolio_strategy_posture")
    if strat:
        lines.append(f"Strategy posture (context): `{strat}`.")
    return lines[:14]


def _operator_learning_lines(learning_pl: dict[str, Any], *, limit: int = 6) -> list[str]:
    return [
        str(x).strip()
        for x in (learning_pl.get("top_lessons_so_far") or [])[:limit]
        if str(x).strip()
    ]


def _intervention_pressure_lines(iv_note: str | None, fc: list[int]) -> list[str]:
    out: list[str] = []
    if iv_note:
        out.append(iv_note)
    if len(fc) >= 4:
        mid = len(fc) // 2
        older = sum(fc[:mid]) / max(1, mid)
        recent = sum(fc[mid:]) / max(1, len(fc) - mid)
        out.append(
            f"Intervention load: ~{recent:.1f} flagged products per run recently vs ~{older:.1f} earlier "
            f"({len(fc)} stamped runs)."
        )
    elif fc:
        out.append(f"Intervention series: {len(fc)} stamped runs in the window.")
    return out


def _sparse_history(
    loaded: dict[str, Any],
    *,
    min_cycles: int = 2,
) -> bool:
    lc = loaded or {}
    return int(lc.get("cycle") or 0) < min_cycles and int(lc.get("delta_report") or 0) < min_cycles


def classify_overall_trajectory(
    *,
    portfolio_summary: dict[str, Any],
    trend_summaries: dict[str, Any],
    cycle_counts: dict[str, int],
) -> str:
    """Deterministic label: improving | stagnating | degrading | mixed | unknown."""
    summ = portfolio_summary or {}
    n = int(summ.get("products_evaluated") or 0)
    if n <= 0:
        return "unknown"

    pos = int(summ.get("positive_count") or 0) / n
    neg = int(summ.get("negative_count") or 0) / n
    flat = int(summ.get("no_meaningful_movement_count") or 0) / n

    ts = trend_summaries or {}
    si = len(ts.get("steadily_improving") or [])
    cd = len(ts.get("cooling_down") or [])
    rp = len(ts.get("rising_priority") or [])
    blk = len(ts.get("chronically_blocked") or [])
    thr = len(ts.get("thrashing_or_oscillating") or [])

    run_again = int(cycle_counts.get("run_again") or 0)
    wait = int(cycle_counts.get("wait") or 0)
    humanish = int(cycle_counts.get("request_human_review") or 0) + int(
        cycle_counts.get("inspect_specific_products") or 0
    )

    if flat > 0.48 and pos < 0.35:
        return "stagnating"
    if wait >= run_again and wait >= 2 and flat > 0.3:
        return "stagnating"
    if neg > pos + 0.12 or (cd > si + 1 and neg > 0.25):
        return "degrading"
    if pos > neg + 0.1 and flat < 0.38 and (si >= cd or rp >= 2):
        return "improving"
    if pos > neg + 0.05 and si > 0 and humanish <= 2:
        return "improving"
    if blk + thr >= 3 and neg > pos:
        return "degrading"
    if abs(pos - neg) < 0.08 and flat > 0.35:
        return "stagnating"
    return "mixed"


def _recommended_next_focus(
    trajectory: str,
    cycle_counts: dict[str, int],
    patterns: list[dict[str, Any]],
    *,
    instrumentation_pressure_count: int = 0,
    instrumentation_followup_count: int = 0,
    instrumentation_resolved_count: int = 0,
    inspect_heavy: bool = False,
) -> str:
    top = max(cycle_counts.items(), key=lambda x: x[1])[0] if cycle_counts else "unknown"
    high = [p for p in patterns if isinstance(p, dict) and p.get("severity") == "high"]
    if trajectory == "degrading":
        return (
            "Stabilize regressing products: inspect top queue ranks, unblock waiting work, and verify import health "
            "before further orchestration pushes."
        )
    if trajectory == "stagnating":
        return (
            "Break stagnation: narrow the next portfolio cycle (fewer products, deeper passes), refresh signals where "
            "stale, and confirm quiescence materiality thresholds match operator intent."
        )
    if trajectory == "improving":
        return (
            "Keep momentum: continue bounded cycles; shift attention to any isolated negatives not covered by systemic patterns."
        )
    if high:
        return (
            f"Address systemic pattern `{high[0].get('pattern_id')}` first — {high[0].get('title', 'see patterns artifact')}."
        )
    if (
        instrumentation_pressure_count > 0
        and inspect_heavy
        and int(cycle_counts.get("inspect_specific_products") or 0) >= 1
    ):
        if instrumentation_followup_count > 0:
            return (
                "Signal instrumentation: some products have partial or still-weak worker applies — keep enriching "
                "telemetry; inspect loops may remain ambiguous until real signals accumulate."
            )
        return (
            "Signal instrumentation: products lack adequate observability while cycles recommend inspection — "
            "run `argus products instrument-signals` for pressured ids before treating outcomes as optimization gaps."
        )
    if (
        instrumentation_pressure_count == 0
        and instrumentation_resolved_count > 0
        and inspect_heavy
        and int(cycle_counts.get("inspect_specific_products") or 0) >= 1
    ):
        return (
            "Signal instrumentation: latest worker applies recorded adequate post-apply validation for previously "
            "pressured ids — repeated inspect stalls may reflect product reality; prioritize learning synthesis over "
            "expanding observability contracts."
        )
    if cycle_counts.get("repair_imports") or cycle_counts.get("inspect_specific_products"):
        return "Prioritize import health and targeted inspections flagged by recent cycle synthesis."
    if top == "run_again":
        return "Portfolio still warrants routine passes — keep operator queue and progression cadence."
    if top == "wait":
        return "Portfolio looks comparatively quiet — good time for consolidation, docs, or strategic work."
    return "Review latest cycle summary and intervention inbox for the next concrete actions."


def evaluate_operator_narrative(
    repo_root: Path,
    *,
    limit_history: int = 30,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    coh_snap = load_artifact_coherence_operational_snapshot(root)

    hist = evaluate_portfolio_history(root, limit_history=lim)
    outcomes = load_canonical_portfolio_outcomes(root)
    patterns_pl = evaluate_portfolio_patterns(root, limit_history=lim, products_dir=products_dir)

    cycle_limit = min(lim, 24)
    cycles = load_latest_n_artifacts(root, "cycle", limit=cycle_limit)
    deltas = load_latest_n_artifacts(root, "delta_report", limit=min(lim, 24))
    interventions = load_latest_n_artifacts(root, "intervention", limit=min(lim, 24))

    loaded = hist.get("loaded_artifact_counts") or {}
    sparse = _sparse_history(loaded)

    summ = (outcomes.get("portfolio_outcome_summary") or {}) if outcomes else {}
    ts = hist.get("trend_summaries") or {}
    cc = _cycle_recommendation_counts(cycles)
    trajectory = classify_overall_trajectory(
        portfolio_summary=summ,
        trend_summaries=ts,
        cycle_counts=cc,
    )

    improved_pids, regressed_pids = _delta_product_sets(deltas)
    fc = _flagged_counts(interventions)
    iv_note = _intervention_load_note(fc)

    detected = [p for p in (patterns_pl.get("detected_patterns") or []) if isinstance(p, dict)]

    key_wins: list[str] = []
    for r in ts.get("steadily_improving") or []:
        if isinstance(r, dict) and r.get("product_id"):
            key_wins.append(
                f"`{r['product_id']}` — readiness tier or understanding debt improved over the window."
            )
    for r in (ts.get("rising_priority") or [])[:8]:
        if isinstance(r, dict) and r.get("product_id"):
            sig = str(r.get("signal") or "")
            key_wins.append(f"`{r['product_id']}` — {sig.replace('_', ' ')}.")
    if improved_pids:
        sample = sorted(improved_pids)[:10]
        key_wins.append(
            f"Delta reports recorded improvement signals for {len(improved_pids)} product(s); e.g. {', '.join(f'`{p}`' for p in sample)}."
        )

    key_regressions: list[str] = []
    for r in ts.get("cooling_down") or []:
        if isinstance(r, dict) and r.get("product_id"):
            key_regressions.append(
                f"`{r['product_id']}` — rank/score/tier movement suggests cooling or regression in the window."
            )
    if regressed_pids:
        sample = sorted(regressed_pids)[:10]
        key_regressions.append(
            f"Delta reports flagged regressions touching {len(regressed_pids)} product(s); e.g. {', '.join(f'`{p}`' for p in sample)}."
        )

    recurring: list[str] = []
    for p in detected:
        sev = str(p.get("severity") or "")
        if sev in ("high", "medium"):
            recurring.append(f"{p.get('pattern_id')}: {p.get('title')} ({sev}).")
    for r in ts.get("chronically_blocked") or []:
        if isinstance(r, dict) and r.get("product_id"):
            recurring.append(
                f"`{r['product_id']}` — progression often blocked ({r.get('blocked_ratio', '?')} blocked ratio in sample)."
            )
    for r in ts.get("thrashing_or_oscillating") or []:
        if isinstance(r, dict) and r.get("product_id"):
            recurring.append(f"`{r['product_id']}` — oscillation or next-action thrash in history.")

    movements: list[str] = []
    for label, key in (
        ("Rising priority", "rising_priority"),
        ("Cooling / regressing", "cooling_down"),
        ("Steadily improving", "steadily_improving"),
    ):
        rows = ts.get(key) or []
        if not rows:
            continue
        pids = [str(r.get("product_id")) for r in rows[:6] if isinstance(r, dict) and r.get("product_id")]
        if pids:
            movements.append(f"{label}: {', '.join(f'`{p}`' for p in pids)}")

    systemic: list[str] = []
    for p in detected[:12]:
        systemic.append(f"{p.get('title')} (`{p.get('pattern_id')}`) — {p.get('recommended_systemic_action', '')[:200]}")
    iso = patterns_pl.get("isolated_negative_products") or []
    if iso:
        systemic.append(
            f"{len(iso)} product(s) with negative trajectories not attributed to a systemic pattern: "
            f"{', '.join(f'`{x}`' for x in iso[:8])}."
        )

    if iv_note:
        recurring.append(iv_note)

    from argus.policy.learning_synthesis import evaluate_operator_learning_synthesis
    from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle

    lifecycle_pl = evaluate_portfolio_lifecycle(root, products_dir=products_dir)
    learning_pl = evaluate_operator_learning_synthesis(
        root,
        limit_history=lim,
        products_dir=products_dir,
    )
    lifecycle_lines = _lifecycle_transition_lines(lifecycle_pl)
    inst_lines = _signal_instrumentation_lines(lifecycle_pl)
    learning_lines = _operator_learning_lines(learning_pl)
    intervention_lines = _intervention_pressure_lines(iv_note, fc)

    inst_n = len(lifecycle_pl.get("products_under_instrumentation_pressure") or [])
    inst_fu = len(lifecycle_pl.get("products_instrumentation_apply_followup") or [])
    inst_res = len(lifecycle_pl.get("products_instrumentation_resolved_via_apply") or [])
    inspect_heavy = int(cc.get("inspect_specific_products") or 0) >= 2

    sections: dict[str, Any] = {
        "substrate_integrity": _substrate_integrity_section(coh_snap),
        "overall_trajectory": trajectory,
        "key_wins": key_wins[:24],
        "key_regressions": key_regressions[:24],
        "recurring_issues": recurring[:24],
        "notable_product_movements": movements[:16],
        "systemic_observations": systemic[:16],
        "recommended_next_focus": _recommended_next_focus(
            trajectory,
            cc,
            detected,
            instrumentation_pressure_count=inst_n,
            instrumentation_followup_count=inst_fu,
            instrumentation_resolved_count=inst_res,
            inspect_heavy=inspect_heavy,
        ),
        "lifecycle_transitions": lifecycle_lines,
        "signal_instrumentation": inst_lines,
        "operator_learning": learning_lines,
        "intervention_pressure": intervention_lines,
    }

    narrative_text = _compose_narrative_text(
        trajectory=trajectory,
        sparse=sparse,
        summ=summ,
        cycle_counts=cc,
        sections=sections,
        loaded=loaded,
        iv_note=iv_note,
        lifecycle_lines=lifecycle_lines,
        inst_lines=inst_lines,
        learning_lines=learning_lines,
        intervention_lines=intervention_lines,
    )
    caveat = _substrate_caveat_line(coh_snap)
    if caveat:
        narrative_text = narrative_text.rstrip() + "\n\n" + caveat

    inv = build_inventory(root, products_dir=products_dir)
    portfolio_pids = sorted(inv.valid.keys())

    nar_notes = [
        "Narrative is derived from local artifacts only; run portfolio pipelines to refresh underlying data.",
    ]
    if outcomes is None:
        nar_notes.insert(
            0,
            "Canonical `runs/portfolio/outcomes/latest.json` absent — portfolio outcome trajectory uses empty summary; "
            "run `argus portfolio outcomes` or a full portfolio cycle with outcomes refresh.",
        )
    if not coh_snap.get("present"):
        nar_notes.append(
            "No durable `runs/debug/artifact_coherence/latest.json` — substrate coherence not assessed; "
            "run `argus portfolio artifact-coherence` or `argus portfolio cycle --artifact-coherence`.",
        )

    return {
        "schema": OPERATOR_NARRATIVE_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "portfolio_mission_provenance": build_portfolio_mission_provenance(root, portfolio_pids),
        "inputs": {
            "limit_history": lim,
            "products_dir": str(products_dir) if products_dir is not None else None,
            "canonical_portfolio_outcomes_loaded": outcomes is not None,
            "artifact_coherence_artifact_present": bool(coh_snap.get("present")),
        },
        "sources": {
            "portfolio_history_run_id": hist.get("run_id"),
            "artifact_coherence_run_id": coh_snap.get("run_id") if coh_snap.get("present") else None,
            "portfolio_outcomes_run_id": outcomes.get("run_id") if outcomes else None,
            "portfolio_patterns_run_id": patterns_pl.get("run_id"),
            "portfolio_lifecycle_run_id": lifecycle_pl.get("run_id"),
            "operator_learning_synthesis_run_id": learning_pl.get("run_id"),
            "cycles_considered": len(cycles),
            "delta_reports_considered": len(deltas),
            "intervention_runs_considered": len(interventions),
        },
        "cycle_overall_counts": cc,
        "sections": sections,
        "narrative_text": narrative_text,
        "sparse_history_warning": sparse,
        "notes": nar_notes,
    }


def _compose_narrative_text(
    *,
    trajectory: str,
    sparse: bool,
    summ: dict[str, Any],
    cycle_counts: dict[str, int],
    sections: dict[str, Any],
    loaded: dict[str, Any],
    iv_note: str | None,
    lifecycle_lines: list[str],
    inst_lines: list[str] | None = None,
    learning_lines: list[str],
    intervention_lines: list[str],
) -> str:
    n = int(summ.get("products_evaluated") or 0)
    pos = int(summ.get("positive_count") or 0)
    neg = int(summ.get("negative_count") or 0)
    flat = int(summ.get("no_meaningful_movement_count") or 0)

    paras: list[str] = []

    if sparse:
        paras.append(
            "History is sparse: few stamped cycles or delta reports are available, so trends below are provisional. "
            "Run portfolio cycles on a steady cadence to strengthen this narrative."
        )

    paras.append(
        f"Across the last window, the portfolio trajectory reads as **{trajectory}** "
        f"(outcomes evaluated: {n} products — {pos} positive, {neg} negative, {flat} no meaningful movement)."
    )

    if cycle_counts:
        parts = [f"{k}: {v}" for k, v in sorted(cycle_counts.items(), key=lambda x: -x[1])[:6]]
        paras.append("Recent portfolio cycle summaries skew toward: " + "; ".join(parts) + ".")

    if lifecycle_lines:
        paras.append(
            "**Portfolio lifecycle (creation / steady / wind-down):** "
            + " ".join(lifecycle_lines[:4])
        )

    if inst_lines:
        paras.append("**Signal instrumentation:** " + " ".join((inst_lines or [])[:3]))

    if learning_lines:
        paras.append(
            "**Operator learning (associative, not causal):** " + " ".join(learning_lines[:5])
        )

    wins = sections.get("key_wins") or []
    if wins:
        paras.append("Wins to keep in view: " + " ".join(wins[:3]))

    reg = sections.get("key_regressions") or []
    if reg:
        paras.append("Pressure points: " + " ".join(reg[:3]))

    if intervention_lines:
        paras.append("**Intervention pressure:** " + " ".join(intervention_lines))

    paras.append(f"Suggested focus: {sections.get('recommended_next_focus', '').strip()}")

    paras.append(
        f"Artifacts loaded for this story: cycles={loaded.get('cycle')}, deltas={loaded.get('delta_report')}, "
        f"interventions={loaded.get('intervention')}, progression={loaded.get('progression')}."
    )

    return "\n\n".join(p for p in paras if p)


def render_operator_narrative_markdown(payload: dict[str, Any]) -> str:
    sec = payload.get("sections") or {}
    lines = [
        "# Operator narrative",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    if payload.get("sparse_history_warning"):
        lines.extend(
            [
                "> **Sparse history:** Few stamped portfolio artifacts — narrative is provisional.",
                "",
            ]
        )

    lines.extend(
        [
            "## Story",
            "",
            payload.get("narrative_text") or "—",
            "",
            f"**Overall trajectory:** `{sec.get('overall_trajectory')}`",
            "",
            "## Portfolio lifecycle",
            "",
        ]
    )
    for w in sec.get("lifecycle_transitions") or []:
        lines.append(f"- {w}")
    if not sec.get("lifecycle_transitions"):
        lines.append("—")
    lines.extend(["", "## Signal instrumentation", ""])
    for w in sec.get("signal_instrumentation") or []:
        lines.append(f"- {w}")
    if not sec.get("signal_instrumentation"):
        lines.append("—")
    lines.extend(["", "## Operator learning", ""])
    for w in sec.get("operator_learning") or []:
        lines.append(f"- {w}")
    if not sec.get("operator_learning"):
        lines.append("—")
    lines.extend(["", "## Intervention pressure", ""])
    for w in sec.get("intervention_pressure") or []:
        lines.append(f"- {w}")
    if not sec.get("intervention_pressure"):
        lines.append("—")

    lines.extend(
        [
            "",
            "## Portfolio motion — key wins",
            "",
        ]
    )
    for w in sec.get("key_wins") or []:
        lines.append(f"- {w}")
    if not sec.get("key_wins"):
        lines.append("—")

    lines.extend(["", "## Portfolio motion — key regressions", ""])
    for w in sec.get("key_regressions") or []:
        lines.append(f"- {w}")
    if not sec.get("key_regressions"):
        lines.append("—")

    lines.extend(["", "## Recurring issues (patterns + history)", ""])
    for w in sec.get("recurring_issues") or []:
        lines.append(f"- {w}")
    if not sec.get("recurring_issues"):
        lines.append("—")

    lines.extend(["", "## Notable product movements", ""])
    for w in sec.get("notable_product_movements") or []:
        lines.append(f"- {w}")
    if not sec.get("notable_product_movements"):
        lines.append("—")

    lines.extend(["", "## Systemic observations", ""])
    for w in sec.get("systemic_observations") or []:
        lines.append(f"- {w}")
    if not sec.get("systemic_observations"):
        lines.append("—")

    lines.extend(["", "## Recommended next focus", "", sec.get("recommended_next_focus") or "—", ""])

    src = payload.get("sources") or {}
    lines.extend(
        [
            "## Sources",
            "",
            f"- Portfolio history: `{src.get('portfolio_history_run_id')}`",
            f"- Outcomes: `{src.get('portfolio_outcomes_run_id')}`",
            f"- Patterns: `{src.get('portfolio_patterns_run_id')}`",
            f"- Portfolio lifecycle: `{src.get('portfolio_lifecycle_run_id')}`",
            f"- Operator learning synthesis: `{src.get('operator_learning_synthesis_run_id')}`",
            f"- Cycles considered: **{src.get('cycles_considered')}** · Delta reports: **{src.get('delta_reports_considered')}** · "
            f"Intervention stamps: **{src.get('intervention_runs_considered')}**",
            "",
        ]
    )
    cc = payload.get("cycle_overall_counts") or {}
    if cc:
        lines.append(f"- Cycle overall recommendations: `{cc}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_operator_narrative_artifacts(
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
    d = operator_narrative_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_operator_narrative_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_operator_narrative(
    repo_root: Path,
    *,
    limit_history: int = 30,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_operator_narrative(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
    )
    if write_artifacts:
        write_operator_narrative_artifacts(repo_root, payload)
    return payload
