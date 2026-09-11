"""
Single-pane operator summary — synthesize queue, progression, quiescence, delta, inbox, outcomes, patterns, cycle,
and a compact **Builder** rollup (from ``runs/portfolio/builder_activity/latest.json`` when present).

Loads existing ``runs/`` artifacts for most inputs. Portfolio outcomes come only from validated
``runs/portfolio/outcomes/latest.json`` (see :func:`_load_canonical_portfolio_outcomes`, same rules as
:func:`argus.portfolio.outcomes.load_canonical_portfolio_outcomes`) — no silent recompute.

When world-context and interpretation are present, creation candidates are computed with
:func:`argus.world_context.creation_candidates.build_creation_candidates_payload` and **persisted** to
``runs/world_context/creation_candidates/latest.json`` so the same payload backs operator summary,
dashboard/console bundles, and on-disk advisory artifacts (no dual-truth split).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.outcome import format_builder_outcome_compact_line
from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.portfolio.artifact_coherence import load_artifact_coherence_operational_snapshot
from argus.portfolio.builder_activity import build_operator_summary_builder_snapshot
from argus.portfolio.builder_activity_attention import (
    ATTENTION_GROUP_KEYS,
    ATTENTION_GROUP_TITLES,
)
from argus.portfolio.builder_outcome_validation import (
    build_builder_outcome_validation_report,
    derive_validation_interpretation,
)
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA, portfolio_cycle_dir
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA, portfolio_delta_report_dir
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.intervention_actions import merge_intervention_actions
from argus.portfolio.intervention_inbox import (
    INTERVENTION_INBOX_SCHEMA,
    build_intervention_inbox_payload,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, operator_queue_output_dir
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA, portfolio_patterns_dir
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA, portfolio_quiescence_dir
from argus.products.inventory import build_inventory
from argus.world_context.creation_candidates import (
    build_creation_candidates_payload,
    summarize_candidates_for_operator,
)
from argus.world_context.interpretation import (
    build_interpretation_payload,
    operator_advisory_from_world_context,
)
from argus.world_context.persist import (
    creation_candidates_output_dir,
    write_creation_candidates_artifact,
)
from argus.world_context.service import (
    load_world_context,
    load_world_context_creation_candidates,
    load_world_context_interpretation,
)

OPERATOR_SUMMARY_SCHEMA = "argus.operator_summary.v1"

# Zero-state portfolio (no validated products) — grounded next step (see also portfolio refresh guidance).
ZERO_PORTFOLIO_RECOMMENDED_NEXT_STEP = (
    "Portfolio is empty — add at least one validated `products/<id>/product.yaml` before routine loops. "
    "Next: `argus products propose-creation` or `argus products create`; import via `python tools/import_product.py`; "
    "then `argus portfolio refresh`. Use `argus reset --portfolio --dry-run` only if you intend to clear products."
)
# Keep in sync with ``argus.portfolio.outcomes.PORTFOLIO_OUTCOMES_SCHEMA`` — duplicated so this module
# does not import ``outcomes`` at load time (avoids circular-import / partial-init with dashboard).
_CANONICAL_PORTFOLIO_OUTCOMES_SCHEMA = "argus.portfolio_outcomes.v1"


def operator_summary_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "dashboard" / "operator_summary"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _load_canonical_portfolio_outcomes(repo_root: Path) -> dict[str, Any] | None:
    """
    Read only ``runs/portfolio/outcomes/latest.json`` when schema matches.

    Same behavior as :func:`argus.portfolio.outcomes.load_canonical_portfolio_outcomes`;
    implemented here so this module does not import that name from ``outcomes`` (avoids
    circular-import / partial-init failures when ``dashboard`` loads ``operator_summary`` early).
    """
    raw = _load_json(
        Path(repo_root).resolve() / "runs" / "portfolio" / "outcomes" / "latest.json"
    )
    if raw and str(raw.get("schema") or "") == _CANONICAL_PORTFOLIO_OUTCOMES_SCHEMA:
        return raw
    return None


def _build_operator_intervention_inbox_view(
    repo_root: Path,
    *,
    recent_intervention_limit: int = 15,
) -> dict[str, Any]:
    """
    Operator dashboard inbox rows from canonical ``runs/portfolio/intervention/latest.json``.

    Same behavior as :func:`argus.portfolio.intervention_inbox.build_operator_intervention_inbox_view`;
    implemented here so this module does not import that name from ``intervention_inbox`` (avoids
    circular-import / partial-init failures when ``dashboard`` loads ``operator_summary`` early).
    """
    root = repo_root.resolve()
    inv_p = portfolio_intervention_dir(root) / "latest.json"
    raw = _load_json(inv_p)
    if not raw or str(raw.get("schema") or "") != PORTFOLIO_INTERVENTION_SCHEMA:
        return {
            "schema": INTERVENTION_INBOX_SCHEMA,
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_intervention_run_id": None,
            "recent_intervention_runs_considered": int(recent_intervention_limit),
            "open_items": [],
            "action_state": merge_intervention_actions(root),
            "coherence_note": (
                "No valid canonical portfolio intervention report — open_items not derived "
                f"(expected schema {PORTFOLIO_INTERVENTION_SCHEMA})."
            ),
        }
    return build_intervention_inbox_payload(
        root,
        intervention_report=raw,
        recent_intervention_limit=recent_intervention_limit,
    )


def _load_queue(repo_root: Path) -> dict[str, Any] | None:
    p = operator_queue_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == OPERATOR_QUEUE_SCHEMA:
        return raw
    return None


def _load_progression(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_progression_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_PROGRESSION_SCHEMA:
        return raw
    return None


def _load_quiescence(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_quiescence_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_QUIESCENCE_SCHEMA:
        return raw
    return None


def _load_delta(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_delta_report_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_DELTA_REPORT_SCHEMA:
        return raw
    return None


def _load_cycle(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_cycle_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_CYCLE_SCHEMA:
        return raw
    return None


def _load_patterns(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_patterns_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw and str(raw.get("schema") or "") == PORTFOLIO_PATTERNS_SCHEMA:
        return raw
    return None


def _safe_float(x: Any) -> float | None:
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _top_products_to_watch(queue: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not queue:
        return out
    entries = list(queue.get("entries") or [])
    entries.sort(key=lambda e: (int(e.get("queue_rank") or 9999), str(e.get("product_id") or "")))
    for e in entries[:limit]:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("product_id") or "").strip()
        if not pid:
            continue
        tier = str(e.get("readiness_tier") or "")
        debt = _safe_float(e.get("understanding_debt"))
        reasons: list[str] = []
        if tier and tier != "advance_ready":
            reasons.append(f"readiness: {tier}")
        if debt is not None and debt >= 0.45:
            reasons.append("higher understanding debt")
        if not reasons:
            reasons.append("top of attention queue")
        out.append({"product_id": pid, "reason": "; ".join(reasons)})
    return out


def _top_products_to_advance(queue: dict[str, Any] | None, *, limit: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not queue:
        return out
    entries = [e for e in (queue.get("entries") or []) if isinstance(e, dict)]
    entries.sort(key=lambda e: (int(e.get("queue_rank") or 9999), str(e.get("product_id") or "")))
    for e in entries:
        if len(out) >= limit:
            break
        tier = str(e.get("readiness_tier") or "")
        pid = str(e.get("product_id") or "").strip()
        if not pid:
            continue
        if tier == "advance_ready":
            out.append({"product_id": pid, "reason": "advance_ready in queue"})
    return out


def _blocked_products(
    outcomes: dict[str, Any] | None,
    progression: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    if outcomes:
        for p in outcomes.get("per_product_outcomes") or []:
            if not isinstance(p, dict):
                continue
            bp = str(p.get("blocked_pattern") or "")
            if bp in ("persisted", "newly_blocked"):
                pid = str(p.get("product_id") or "")
                if pid and pid not in seen:
                    seen.add(pid)
                    rows.append({"product_id": pid, "reason": f"orchestration blocked pattern: {bp}"})
    if progression:
        for row in progression.get("products") or []:
            if not isinstance(row, dict):
                continue
            oc = str(row.get("outcome") or "")
            if oc in ("blocked_waiting", "blocked_approval"):
                pid = str(row.get("product_id") or "")
                if pid and pid not in seen:
                    seen.add(pid)
                    rows.append({"product_id": pid, "reason": f"last progression outcome: {oc}"})
    return rows


def _intervention_inbox_summary(inbox: dict[str, Any] | None) -> dict[str, Any]:
    if not inbox:
        return {
            "present": False,
            "active_items": 0,
            "deferred_items": 0,
            "high_severity_in_active": 0,
        }
    items = [x for x in (inbox.get("open_items") or []) if isinstance(x, dict)]
    active = [x for x in items if x.get("in_active_queue")]
    deferred = [x for x in items if not x.get("in_active_queue")]
    high = sum(1 for x in active if str(x.get("severity") or "") == "high")
    return {
        "present": True,
        "active_items": len(active),
        "deferred_items": len(deferred),
        "high_severity_in_active": high,
        "source_run_id": inbox.get("source_intervention_run_id"),
    }


def _builder_outcome_compact_display(b: dict[str, Any]) -> str:
    """Prefer embedded Phase 3B ``outcome_operator_one_liner``; fall back to compact line or rebuild."""
    one = str(b.get("outcome_operator_one_liner") or "").strip()
    if one:
        return one
    ocl = str(b.get("outcome_compact_line") or "").strip()
    if ocl:
        return ocl
    return format_builder_outcome_compact_line(
        attribution_status=b.get("attribution_status"),
        comparison_window_status=b.get("comparison_window_status"),
        comparison_basis=b.get("comparison_basis"),
    )


def _outcome_summary(outcomes: dict[str, Any] | None) -> dict[str, Any]:
    if not outcomes:
        return {
            "present": False,
            "products_evaluated": 0,
            "positive_count": 0,
            "negative_count": 0,
            "flat_count": 0,
        }
    s = outcomes.get("portfolio_outcome_summary") or {}
    return {
        "present": True,
        "products_evaluated": int(s.get("products_evaluated") or 0),
        "positive_count": int(s.get("positive_count") or 0),
        "negative_count": int(s.get("negative_count") or 0),
        "flat_count": int(s.get("no_meaningful_movement_count") or 0),
        "mixed_count": int(s.get("mixed_count") or 0),
    }


def _systemic_patterns_summary(patterns: dict[str, Any] | None) -> dict[str, Any]:
    if not patterns:
        return {"present": False, "pattern_count": 0, "high_severity_count": 0, "titles": []}
    detected = [p for p in (patterns.get("detected_patterns") or []) if isinstance(p, dict)]
    highs = [p for p in detected if str(p.get("severity") or "") == "high"]
    titles = [str(p.get("title") or p.get("pattern_id") or "") for p in detected[:12]]
    return {
        "present": True,
        "pattern_count": len(detected),
        "high_severity_count": len(highs),
        "titles": [t for t in titles if t],
    }


def _artifact_coverage(
    *,
    queue: Any,
    progression: Any,
    quiescence: Any,
    delta: Any,
    cycle: Any,
    outcomes: Any,
    inbox: Any,
    patterns: Any,
) -> tuple[int, list[str]]:
    keys = [
        ("operator_queue", queue),
        ("portfolio_progression", progression),
        ("portfolio_quiescence", quiescence),
        ("portfolio_delta_report", delta),
        ("portfolio_cycle", cycle),
        ("portfolio_outcomes", outcomes),
        ("intervention_inbox", inbox),
        ("portfolio_patterns", patterns),
    ]
    present = [name for name, v in keys if v is not None]
    return len(present), present


def _strongest_sparse_signal_warning(warnings: list[str] | None) -> str | None:
    if not warnings:
        return None
    for w in warnings:
        if "conflicting_signal" in str(w):
            return str(w)
    return str(warnings[0])


def _build_lifecycle_snapshot(lifecycle_pl: dict[str, Any]) -> dict[str, Any]:
    from argus.portfolio.lifecycle import PORTFOLIO_LIFECYCLE_SCHEMA

    summ = lifecycle_pl.get("portfolio_lifecycle_summary")
    narrative = None
    n_inst = None
    if isinstance(summ, dict):
        narrative = summ.get("narrative")
        n_inst = summ.get("products_needing_signal_instrumentation_count")
    return {
        "present": str(lifecycle_pl.get("schema") or "") == PORTFOLIO_LIFECYCLE_SCHEMA,
        "run_id": lifecycle_pl.get("run_id"),
        "lifecycle_counts": lifecycle_pl.get("lifecycle_counts") or {},
        "products_entering": lifecycle_pl.get("products_entering") or [],
        "products_exiting": lifecycle_pl.get("products_exiting") or [],
        "products_under_repair_pressure": lifecycle_pl.get("products_under_repair_pressure") or [],
        "products_under_retirement_pressure": lifecycle_pl.get("products_under_retirement_pressure") or [],
        "products_under_instrumentation_pressure": lifecycle_pl.get("products_under_instrumentation_pressure") or [],
        "products_under_instrumentation_pressure_raw": lifecycle_pl.get(
            "products_under_instrumentation_pressure_raw"
        )
        or [],
        "products_instrumentation_apply_followup": lifecycle_pl.get("products_instrumentation_apply_followup") or [],
        "products_instrumentation_resolved_via_apply": lifecycle_pl.get("products_instrumentation_resolved_via_apply")
        or [],
        "products_needing_signal_instrumentation_count": n_inst,
        "portfolio_strategy_posture": lifecycle_pl.get("portfolio_strategy_posture"),
        "summary_narrative": narrative,
    }


def _build_learning_snapshot(learning_pl: dict[str, Any]) -> dict[str, Any]:
    from argus.policy.learning_synthesis import OPERATOR_LEARNING_SYNTHESIS_SCHEMA

    w = [str(x) for x in (learning_pl.get("sparse_signal_warnings") or [])]
    return {
        "present": str(learning_pl.get("schema") or "") == OPERATOR_LEARNING_SYNTHESIS_SCHEMA,
        "run_id": learning_pl.get("run_id"),
        "top_lessons": (learning_pl.get("top_lessons_so_far") or [])[:8],
        "sparse_signal_warnings": w,
        "strongest_sparse_signal_warning": _strongest_sparse_signal_warning(w),
    }


def _confidence_level(n_present: int) -> str:
    if n_present >= 6:
        return "high"
    if n_present >= 3:
        return "medium"
    return "low"


def _derive_headline_status(
    *,
    blocked: list[dict[str, Any]],
    outcome_s: dict[str, Any],
    inbox_s: dict[str, Any],
    patterns_s: dict[str, Any],
    quiescence: dict[str, Any] | None,
    cycle: dict[str, Any] | None,
) -> str:
    """healthy | active | degraded | attention_needed"""
    stress = 0
    stress += min(8, len(blocked) * 2)
    stress += min(6, outcome_s.get("negative_count", 0))
    if inbox_s.get("active_items", 0) >= 4:
        stress += 5
    elif inbox_s.get("active_items", 0) >= 1:
        stress += 2
    stress += min(4, inbox_s.get("high_severity_in_active", 0) * 2)
    stress += min(4, patterns_s.get("high_severity_count", 0) * 2)
    stress += min(3, patterns_s.get("pattern_count", 0))

    qrec = str((quiescence or {}).get("recommendation") or "")
    if qrec == "human_review":
        stress += 4
    summ = (cycle or {}).get("summary") or {}
    overall = str(summ.get("overall_operator_recommendation") or "")
    if overall == "request_human_review":
        stress += 3

    if stress >= 10:
        return "attention_needed"
    if stress >= 5:
        return "degraded"
    n_eval = outcome_s.get("products_evaluated", 0)
    if n_eval == 0 and len(blocked) == 0:
        return "healthy"
    neg = outcome_s.get("negative_count", 0)
    if stress <= 1 and neg == 0 and len(blocked) == 0:
        return "healthy"
    if stress <= 3:
        return "active"
    return "degraded"


def _recommended_next_step(
    *,
    cycle: dict[str, Any] | None,
    quiescence: dict[str, Any] | None,
    delta: dict[str, Any] | None,
    instrumentation_pressure_ids: list[str] | None = None,
    instrumentation_followup_ids: list[str] | None = None,
    instrumentation_resolved_ids: list[str] | None = None,
) -> str:
    summ = (cycle or {}).get("summary") or {}
    o = str(summ.get("overall_operator_recommendation") or "").strip()
    pressure = sorted(set(instrumentation_pressure_ids or []))
    followup = sorted(set(instrumentation_followup_ids or []))
    resolved = sorted(set(instrumentation_resolved_ids or []))
    inst_note = ""
    if pressure:
        if followup:
            overlap = [p for p in pressure if p in set(followup)]
            inst_note = (
                f" Signal instrumentation: {len(overlap)} product(s) have partial or still-weak worker applies "
                f"({', '.join(f'`{p}`' for p in overlap[:6])}{'…' if len(overlap) > 6 else ''}) — enrich telemetry before "
                "treating inspect noise as product verdicts."
            )
        else:
            inst_note = (
                f" Signal instrumentation: {len(pressure)} product(s) lack adequate observability "
                f"({', '.join(f'`{p}`' for p in pressure[:6])}{'…' if len(pressure) > 6 else ''}) — "
                "consider `argus products instrument-signals` before treating sparse outcomes as optimization failures."
            )
    elif resolved:
        inst_note = (
            f" Signal instrumentation: latest worker applies recorded adequate post-apply validation for "
            f"{len(resolved)} id(s) ({', '.join(f'`{p}`' for p in resolved[:6])}{'…' if len(resolved) > 6 else ''}) — "
            "stagnation may reflect product reality rather than missing observability."
        )
    if o:
        mapping = {
            "run_again": "Run another bounded portfolio cycle when ready.",
            "wait": "Let artifacts age; skip forced progression this pass.",
            "inspect_specific_products": "Inspect the specific products called out in queue and intervention.",
            "repair_imports": "Fix importer / first-pass issues for flagged products, then re-run signals.",
            "request_human_review": "Unblock human approvals or refinement inputs before advancing.",
        }
        if o in mapping:
            base = mapping[o]
            if o == "inspect_specific_products" and inst_note:
                return base + inst_note
            return base
        return f"Follow latest cycle recommendation: {o}"

    q = str((quiescence or {}).get("recommendation") or "").strip()
    d = str((delta or {}).get("recommended_next_portfolio_action") or "").strip()
    if q and d:
        base = f"Quiescence suggests {q}; delta suggests {d} — pick the higher-severity path first."
        return base + inst_note if inst_note else base
    if q:
        base = f"Address quiescence recommendation: {q}"
        return base + inst_note if inst_note else base
    if d:
        base = f"Address portfolio delta recommendation: {d}"
        return base + inst_note if inst_note else base
    base = "Run `argus portfolio refresh` and `argus portfolio cycle` to populate portfolio artifacts."
    return base + inst_note if inst_note else base


def evaluate_operator_summary(
    repo_root: Path,
    *,
    limit_history: int = 30,
    products_dir: Path | None = None,
    operator_queue_override: dict[str, Any] | None = None,
    portfolio_quiescence_override: dict[str, Any] | None = None,
    persist_creation_candidates_artifact: bool = True,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    wc_payload = load_world_context(root)
    world_context_interpretation = build_interpretation_payload(wc_payload) if wc_payload else None
    external_context_advisory = operator_advisory_from_world_context(wc_payload)
    world_context_present = wc_payload is not None
    wc_interp_artifact = load_world_context_interpretation(root)
    world_context_creation_candidates = (
        build_creation_candidates_payload(wc_payload, world_context_interpretation)
        if wc_payload and world_context_interpretation
        else None
    )
    # Persist the same payload the summary embeds so runs/world_context/creation_candidates/latest.json,
    # console/dashboard bundles, and operator summary stay coherent (single generator:
    # :func:`argus.world_context.creation_candidates.build_creation_candidates_payload`).
    if persist_creation_candidates_artifact:
        if world_context_creation_candidates is not None:
            write_creation_candidates_artifact(root, world_context_creation_candidates)
        elif wc_payload and world_context_interpretation:
            _cc_stale = creation_candidates_output_dir(root) / "latest.json"
            if _cc_stale.is_file():
                _cc_stale.unlink()
    zero_state_creation_candidates_summary = summarize_candidates_for_operator(world_context_creation_candidates)
    wc_cc_artifact = load_world_context_creation_candidates(root)
    external_context_situation_brief: str | None = None
    if isinstance(world_context_creation_candidates, dict):
        _brief = str(world_context_creation_candidates.get("situation_summary") or "").strip()
        external_context_situation_brief = _brief or None

    queue = operator_queue_override if operator_queue_override is not None else _load_queue(root)
    progression = _load_progression(root)
    quiescence = (
        portfolio_quiescence_override if portfolio_quiescence_override is not None else _load_quiescence(root)
    )
    delta = _load_delta(root)
    cycle = _load_cycle(root)
    patterns = _load_patterns(root)

    outcomes = _load_canonical_portfolio_outcomes(root)
    coh_snap = load_artifact_coherence_operational_snapshot(root)
    inbox = _build_operator_intervention_inbox_view(root, recent_intervention_limit=15)

    outcome_s = _outcome_summary(outcomes)
    inbox_s = _intervention_inbox_summary(inbox)
    patterns_s = _systemic_patterns_summary(patterns)
    blocked = _blocked_products(outcomes, progression)
    watch = _top_products_to_watch(queue)
    advance = _top_products_to_advance(queue)

    n_pres, present = _artifact_coverage(
        queue=queue,
        progression=progression,
        quiescence=quiescence,
        delta=delta,
        cycle=cycle,
        outcomes=outcomes,
        inbox=inbox,
        patterns=patterns,
    )
    inv = build_inventory(root, products_dir=products_dir)
    portfolio_pids = sorted(inv.valid.keys())
    # Zero-state: no on-disk product manifests *and* no meaningful portfolio artifact signal
    # (outcomes / intervention / queue can reference work even when inventory is empty in tests).
    pev = int(outcome_s.get("products_evaluated") or 0)
    active_iv = int(inbox_s.get("active_items") or 0)
    q_entries = queue if isinstance(queue, dict) else {}
    q_count = len(q_entries.get("entries") or [])
    has_portfolio_signal = (
        pev > 0
        or active_iv > 0
        or len(blocked) > 0
        or q_count > 0
    )
    portfolio_empty = (
        inv.summary.valid_count == 0
        and inv.summary.invalid_count == 0
        and getattr(inv.summary, "total_candidates", 0) == 0
        and not has_portfolio_signal
    )

    conf = _confidence_level(n_pres)
    if portfolio_empty:
        headline = "portfolio_empty"
        conf = "low"
    else:
        headline = _derive_headline_status(
            blocked=blocked,
            outcome_s=outcome_s,
            inbox_s=inbox_s,
            patterns_s=patterns_s,
            quiescence=quiescence,
            cycle=cycle,
        )

    from argus.policy.learning_synthesis import evaluate_operator_learning_synthesis
    from argus.portfolio.lifecycle import evaluate_portfolio_lifecycle

    lifecycle_pl = evaluate_portfolio_lifecycle(root, products_dir=products_dir)
    learning_pl = evaluate_operator_learning_synthesis(
        root,
        limit_history=lim,
        products_dir=products_dir,
    )

    pressure_ids = list(lifecycle_pl.get("products_under_instrumentation_pressure") or [])
    followup_ids = list(lifecycle_pl.get("products_instrumentation_apply_followup") or [])
    resolved_ids = list(lifecycle_pl.get("products_instrumentation_resolved_via_apply") or [])
    pressure_set = set(pressure_ids)
    followup_set = set(followup_ids)
    resolved_set = set(resolved_ids)
    inst_loaded = int(
        (lifecycle_pl.get("inputs") or {}).get("signal_instrumentation_artifacts_loaded") or 0
    )
    apply_loaded = int(
        (lifecycle_pl.get("inputs") or {}).get("signal_instrumentation_apply_artifacts_loaded") or 0
    )
    watch_enhanced: list[dict[str, Any]] = []
    for row in watch:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        pid = str(r.get("product_id") or "").strip()
        if pid and pid in pressure_set:
            base = str(r.get("reason") or "")
            if pid in followup_set:
                extra = "instrumentation apply follow-up (partial or still weak post-apply)"
            else:
                extra = "candidate for signal instrumentation before optimization"
            r["reason"] = f"{base}; {extra}" if base else extra
        elif pid and pid in resolved_set:
            base = str(r.get("reason") or "")
            extra = "latest apply validation adequate — inspect loops may reflect product behavior, not missing telemetry"
            r["reason"] = f"{base}; {extra}" if base else extra
        watch_enhanced.append(r)

    if portfolio_empty:
        next_step = ZERO_PORTFOLIO_RECOMMENDED_NEXT_STEP
    else:
        next_step = _recommended_next_step(
            cycle=cycle,
            quiescence=quiescence,
            delta=delta,
            instrumentation_pressure_ids=pressure_ids,
            instrumentation_followup_ids=followup_ids,
            instrumentation_resolved_ids=resolved_ids,
        )

    sig_parts: list[str] = []
    if pressure_ids:
        sig_parts.append(
            f"{len(pressure_ids)} product(s) remain under effective instrumentation pressure (scan vs apply-refined)."
        )
    if followup_ids:
        sig_parts.append(
            f"{len(followup_ids)} product(s) have worker instrumentation apply follow-up (partial or still weak)."
        )
    if resolved_ids:
        sig_parts.append(
            f"{len(resolved_ids)} product(s) have adequate post-apply validation while scans may still read weak."
        )
    sig_note = " ".join(sig_parts) if sig_parts else ""

    builder_activity_snapshot = build_operator_summary_builder_snapshot(root)
    builder_outcome_validation = build_builder_outcome_validation_report(root)

    return {
        "schema": OPERATOR_SUMMARY_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "portfolio_mission_provenance": build_portfolio_mission_provenance(root, portfolio_pids),
        "lifecycle_snapshot": _build_lifecycle_snapshot(lifecycle_pl),
        "learning_snapshot": _build_learning_snapshot(learning_pl),
        "artifact_coherence": coh_snap,
        "inputs": {
            "limit_history_outcomes": lim,
            "products_dir": str(products_dir) if products_dir is not None else None,
            "artifacts_present": present,
            "artifact_coverage_count": n_pres,
            "canonical_portfolio_outcomes_loaded": outcomes is not None,
            "artifact_coherence_artifact_present": bool(coh_snap.get("present")),
            "world_context_artifact_present": world_context_present,
            "world_context_interpretation_artifact_present": wc_interp_artifact is not None,
            "world_context_creation_candidates_artifact_present": wc_cc_artifact is not None,
            "lifecycle_run_id": lifecycle_pl.get("run_id"),
            "learning_synthesis_run_id": learning_pl.get("run_id"),
        },
        "headline_status": headline,
        "confidence_level": conf,
        "top_products_to_watch": watch_enhanced,
        "top_products_to_advance": advance,
        "blocked_products": blocked,
        "intervention_inbox_summary": inbox_s,
        "outcome_summary": outcome_s,
        "systemic_patterns_summary": patterns_s,
        "recommended_next_step": next_step,
        "external_context_advisory": external_context_advisory,
        "external_context_situation_brief": external_context_situation_brief,
        "world_context_interpretation": world_context_interpretation,
        "world_context_creation_candidates": world_context_creation_candidates,
        "zero_state_creation_candidates_summary": zero_state_creation_candidates_summary,
        "world_context_present": world_context_present,
        "portfolio_state": "empty_portfolio" if portfolio_empty else "populated",
        "zero_state": portfolio_empty,
        "signal_instrumentation_snapshot": {
            "artifacts_loaded_count": inst_loaded,
            "apply_artifacts_loaded_count": apply_loaded,
            "products_under_instrumentation_pressure": sorted(pressure_ids),
            "products_instrumentation_apply_followup": sorted(followup_ids),
            "products_instrumentation_resolved_via_apply": sorted(resolved_ids),
            "note": sig_note or None,
        },
        "snapshots": {
            "quiescence_recommendation": (quiescence or {}).get("recommendation"),
            "delta_recommended_action": (delta or {}).get("recommended_next_portfolio_action"),
            "cycle_ok": (cycle or {}).get("ok"),
            "portfolio_quiescent": (quiescence or {}).get("portfolio_quiescent"),
        },
        "builder_activity_snapshot": builder_activity_snapshot,
        "builder_outcome_validation": builder_outcome_validation,
    }


def render_operator_summary_markdown(payload: dict[str, Any]) -> str:
    hl = str(payload.get("headline_status") or "")
    conf = str(payload.get("confidence_level") or "")
    lines = [
        "# Operator summary",
        "",
        f"**Status:** **{hl.replace('_', ' ')}** · **confidence:** {conf}",
        "",
    ]
    if payload.get("zero_state"):
        lines.extend(
            [
                "## Portfolio zero-state",
                "",
                "Validated inventory is empty (no `products/<id>/product.yaml` yet). "
                "Argus cannot run routine portfolio loops until at least one product exists — **this is expected**, not a broken pipeline.",
                "",
            ]
        )
    ext = str(payload.get("external_context_advisory") or "").strip()
    wci = payload.get("world_context_interpretation")
    wcc_pre = payload.get("world_context_creation_candidates")
    has_wcc_fold = isinstance(wcc_pre, dict) and (
        bool(str(wcc_pre.get("situation_summary") or "").strip())
        or (isinstance(wcc_pre.get("candidates"), list) and bool(wcc_pre["candidates"]))
    )
    if ext or (isinstance(wci, dict) and wci.get("per_entity")) or has_wcc_fold:
        lines.extend(
            [
                "## External world context (advisory)",
                "",
            ]
        )
        if isinstance(wcc_pre, dict):
            brief = str(wcc_pre.get("situation_summary") or "").strip()
            if brief:
                lines.append("**Situation (brief):** " + brief)
                lines.append("")
            cands_fold = wcc_pre.get("candidates")
            if isinstance(cands_fold, list) and cands_fold:
                primary = str(wcc_pre.get("primary_candidate_id") or "").strip()
                lines.append("**Creation directions (advisory, not decisions):**")
                for c in cands_fold[:3]:
                    if not isinstance(c, dict):
                        continue
                    cid = str(c.get("candidate_id") or "")
                    title = str(c.get("title") or "")
                    mark = (
                        " *(primary — most supported by current signals)*"
                        if primary and cid == primary
                        else ""
                    )
                    lines.append(f"- `{cid}` — {title}{mark}")
                lines.append("")
        if ext:
            lines.append("**Full advisory narrative:**")
            lines.append("")
            lines.append(ext)
            lines.append("")
        if isinstance(wci, dict) and wci.get("per_entity"):
            lines.append("**Grounded detail (per entity):**")
            for eid in wci.get("entities_ordered") or []:
                block = (wci.get("per_entity") or {}).get(eid)
                if isinstance(block, dict):
                    for sl in block.get("summary_lines") or []:
                        lines.append(f"- {sl}")
            comp = wci.get("comparison")
            if isinstance(comp, dict) and comp.get("summary_lines"):
                lines.append("")
                lines.append("**Comparison:**")
                for sl in comp.get("summary_lines") or []:
                    lines.append(f"- {sl}")
            if wci.get("limitations"):
                lines.append("")
                lines.append(
                    "*Limitations:* "
                    + " ".join(str(x) for x in (wci.get("limitations") or [])[:4])
                )
            lines.append("")
    wcc = payload.get("world_context_creation_candidates")
    if isinstance(wcc, dict) and isinstance(wcc.get("candidates"), list) and wcc["candidates"]:
        lines.extend(
            [
                "## Advisory creation directions (hypothesis-level — full detail)",
                "",
                "*These are not decisions — exploratory directions grounded in interpretation patterns and signal rows.*",
                "",
            ]
        )
        for c in wcc["candidates"][:4]:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("candidate_id") or "")
            title = str(c.get("title") or "")
            lines.append(f"### `{cid}` — {title}")
            lines.append("")
            lines.append(str(c.get("rationale") or ""))
            lines.append("")
            ents = c.get("entities") or []
            pats = c.get("interpretation_patterns") or []
            lines.append(f"- **Entities:** {', '.join(f'`{e}`' for e in ents) if ents else '—'}")
            lines.append(f"- **Patterns used:** {', '.join(f'`{p}`' for p in pats) if pats else '—'}")
            lines.append(f"- **Evidence:** {c.get('evidence_summary') or '—'}")
            lines.append(f"- **Strength (coarse):** `{c.get('strength') or '—'}`")
            ns = c.get("next_steps") or []
            if isinstance(ns, list) and ns:
                lines.append("- **Suggested next steps (operator-led):**")
                for step in ns[:2]:
                    lines.append(f"  - {step}")
            se = c.get("signal_evidence") or []
            if isinstance(se, list) and se:
                lines.append("- **Signal rows (audit):**")
                for row in se[:6]:
                    if not isinstance(row, dict):
                        continue
                    lines.append(
                        f"  - `{row.get('entity')}` · {row.get('signal_type')} · "
                        f"{row.get('unit')} = {row.get('value')!r}"
                    )
            lines.append("")
        lim2 = wcc.get("limitations") or []
        if lim2:
            lines.append("*Limitations:* " + " ".join(str(x) for x in lim2[:3]))
            lines.append("")
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    ac = payload.get("artifact_coherence") or {}
    if isinstance(ac, dict) and ac.get("present"):
        lines.extend(
            [
                "## Artifact coherence (substrate trust)",
                "",
                f"- **overall_status:** `{ac.get('overall_status')}`",
                f"- **run_id:** `{ac.get('run_id')}`",
                "",
            ]
        )
        s = str(ac.get("summary") or "").strip()
        if s:
            lines.append(s)
            lines.append("")
    elif isinstance(ac, dict):
        lines.extend(
            [
                "## Artifact coherence (substrate trust)",
                "",
                "- **present:** false — run `argus portfolio artifact-coherence` to materialize the report.",
                "",
            ]
        )
    bas = payload.get("builder_activity_snapshot") or {}
    if isinstance(bas, dict):
        lines.extend(["", "## Builder (portfolio rollup)", ""])
        if not bas.get("artifact_present"):
            lines.append(f"- {bas.get('operator_hint') or 'No Builder activity rollup on disk.'}")
            lines.append("")
        else:
            lines.append(
                f"- **Source:** `{bas.get('source_artifact')}` · generated `{bas.get('generated_at_utc')}` · "
                f"run `{bas.get('run_id')}`"
            )
            lines.append(f"- **Summary:** {bas.get('trust_summary_line')}")
            lines.append(f"- **Next (Builder):** {bas.get('next_action_hint')}")
            olp = bas.get("builder_outcome_lines_preview") or []
            if olp:
                lines.append(
                    "- **Phase 3 `builder_outcome` (observational, non-causal):** "
                    + " · ".join(str(x) for x in olp[:6])
                )
            att = bas.get("attention_products") or []
            grouped = bas.get("attention_products_grouped")
            if att:
                lines.extend(["", "### Attention needed", ""])
                if isinstance(grouped, dict):
                    any_section = False
                    for gkey in ATTENTION_GROUP_KEYS:
                        rows = grouped.get(gkey) or []
                        if not rows:
                            continue
                        any_section = True
                        title = ATTENTION_GROUP_TITLES.get(gkey, gkey)
                        lines.extend([f"#### {title}", ""])
                        for a in rows[:10]:
                            if not isinstance(a, dict):
                                continue
                            pid = a.get("product_id") or "?"
                            rsn = ", ".join(a.get("reasons") or [])
                            lines.append(f"- **`{pid}`** — {rsn}")
                        lines.append("")
                    if not any_section:
                        for a in att[:10]:
                            if not isinstance(a, dict):
                                continue
                            pid = a.get("product_id") or "?"
                            rsn = ", ".join(a.get("reasons") or [])
                            lines.append(f"- **`{pid}`** — {rsn}")
                else:
                    for a in att[:10]:
                        if not isinstance(a, dict):
                            continue
                        pid = a.get("product_id") or "?"
                        rsn = ", ".join(a.get("reasons") or [])
                        lines.append(f"- **`{pid}`** — {rsn}")
            else:
                lines.append(
                    "- **Attention:** none flagged (scope / outcomes / escalation / trust rules in rollup)"
                )
            lines.extend(["", "### Recent activity", ""])
            lines.append("*Compact: `product_id | contract | trust | next`*")
            lines.append("")
            for r in (bas.get("recent_runs") or [])[:10]:
                if not isinstance(r, dict):
                    continue
                cl = str(r.get("compact_line") or "").strip()
                boc = str(r.get("builder_outcome_compact") or "").strip()
                if cl:
                    ch = r.get("cleanup_hint")
                    extra = f" — *{ch}*" if ch else ""
                    oc_extra = f" — *{boc}*" if boc else ""
                    lines.append(f"- `{cl}`{extra}{oc_extra}")
                else:
                    tail = f" — *{boc}*" if boc else ""
                    lines.append(
                        f"- `{r.get('product_id')}` · updated `{r.get('updated_at')}` · invoke `{r.get('invoke')}` · "
                        f"merge `{r.get('merge')}` · outcome `{r.get('outcome')}` · trust `{r.get('trust')}`{tail}"
                    )
            lines.append("")
            lines.append("*Full row fields (invoke, merge, outcome, timestamps) are in the operator summary JSON.*")
            lines.append("")
            bos = [x for x in (bas.get("builder_outcome_summaries") or []) if isinstance(x, dict)]
            pc = int(bas.get("product_count") or 0)
            lines.extend(["", "### Builder outcome bridge (Phase 3 entry)", ""])
            lines.append(
                "*Non-causal; full payload: `runs/builder/outcome/<product_id>/latest.json` "
                f"(`{bas.get('builder_outcome_schema') or 'argus.builder_outcome.v1'}`).*"
            )
            if not bos and pc > 0:
                lines.append(
                    "*No embedded outcome summaries in this snapshot file — re-run `argus portfolio builder-activity` "
                    "(or `argus dashboard summary`) to refresh.*"
                )
            for b in bos[:8]:
                pid = b.get("product_id") or "?"
                ocl = _builder_outcome_compact_display(b)
                ap = b.get("artifact_path_repo") or ""
                lines.append(
                    f"- **`{pid}`** — {ocl}"
                    + (f" · `{ap}`" if ap else "")
                )
            if bos and str(bos[0].get("outcome_disclaimer_short") or "").strip():
                lines.append("")
                lines.append(f"*{bos[0].get('outcome_disclaimer_short')}*")
            lines.append("")
            lines.append(
                "*Coordination snapshot only — same truth as `argus portfolio builder-activity`; not learning or strategy.*"
            )
            lines.append("")
    bov = payload.get("builder_outcome_validation")
    if isinstance(bov, dict) and bov.get("schema"):
        bov_view = dict(bov)
        if not str(bov_view.get("validation_headline") or "").strip():
            bov_view.update(derive_validation_interpretation(bov_view))
        lines.extend(["", "## Builder outcome validation (Phase 3 observational)", ""])
        lines.append(f"*{bov_view.get('disclaimer') or 'Observational layer calibration only.'}*")
        lines.append("")
        vh = str(bov_view.get("validation_headline") or "").strip()
        if vh:
            lines.append(f"- **Summary:** {vh}")
        vil = bov_view.get("validation_interpretation_lines") or []
        if isinstance(vil, list) and vil:
            for ln in vil[:8]:
                if str(ln).strip():
                    lines.append(f"  - {str(ln).strip()}")
        val = str(bov_view.get("validation_attention_level") or "").strip()
        if val:
            lines.append(f"- **Coverage hint:** `{val}` — plain words only; not a score.")
        vid = str(bov_view.get("validation_interpretation_disclaimer") or "").strip()
        if vid:
            lines.append(f"- *{vid}*")
        lines.append("")
        n_art = int(bov_view.get("products_with_outcome_artifact") or 0)
        lines.append(
            f"- **Readable outcome artifacts:** {n_art} (`{bov_view.get('source_glob') or 'runs/builder/outcome/*/latest.json'}`)"
        )
        for title, key in (
            ("Attribution", "counts_by_attribution_status"),
            ("Comparison evidence", "counts_by_comparison_evidence_strength"),
            ("Observation timing", "counts_by_observation_timing_status"),
            ("Recent pattern", "counts_by_recent_observation_pattern"),
        ):
            c = bov_view.get(key)
            if isinstance(c, dict) and c:
                parts = [f"`{k}`={v}" for k, v in sorted(c.items())]
                lines.append(f"- **{title}:** " + " · ".join(parts[:14]))
        rnp = bov_view.get("products_repeated_negative_observations") or []
        if isinstance(rnp, list) and rnp:
            lines.append(
                "- **Products (repeat negative pattern):** "
                + ", ".join(f"`{x}`" for x in rnp[:12])
                + (" …" if len(rnp) > 12 else "")
            )
        wk = bov_view.get("products_weak_current_evidence") or []
        if isinstance(wk, list) and wk:
            lines.append(
                "- **Products (current evidence none/weak):** "
                + ", ".join(f"`{x}`" for x in wk[:12])
                + (" …" if len(wk) > 12 else "")
            )
        wm = bov_view.get("products_weak_majority_recent_window") or []
        if isinstance(wm, list) and wm:
            lines.append(
                "- **Products (majority none/weak in recent window):** "
                + ", ".join(f"`{x}`" for x in wm[:12])
                + (" …" if len(wm) > 12 else "")
            )
        lines.append("")
        lines.append(
            "*Full JSON:* `runs/portfolio/builder_outcome_validation/latest.json` "
            f"(`{bov_view.get('schema') or 'argus.builder_outcome_validation.v1'}`)."
        )
        lines.append("")
    lines.extend(
        [
            f"**Next step:** {payload.get('recommended_next_step')}",
            "",
            "## Portfolio lifecycle",
            "",
        ]
    )
    ls = payload.get("lifecycle_snapshot") or {}
    if ls.get("present"):
        lc = ls.get("lifecycle_counts") or {}
        lines.append(
            f"- **Lanes:** proposed {lc.get('proposed', 0)} · incubating {lc.get('incubating', 0)} · active {lc.get('active', 0)} · "
            f"repairing {lc.get('repairing', 0)} · harvesting {lc.get('harvesting', 0)} · retiring {lc.get('retiring', 0)} · "
            f"archived {lc.get('archived_candidate', 0)} · mixed {lc.get('mixed_or_unclear', 0)}"
        )
        if ls.get("portfolio_strategy_posture"):
            lines.append(f"- **Strategy posture (context):** `{ls.get('portfolio_strategy_posture')}`")
        ent = ls.get("products_entering") or []
        ex = ls.get("products_exiting") or []
        if ent:
            lines.append(f"- **Entering:** {', '.join(f'`{x}`' for x in ent[:16])}")
        if ex:
            lines.append(f"- **Exit-oriented:** {', '.join(f'`{x}`' for x in ex[:16])}")
        rp = ls.get("products_under_repair_pressure") or []
        rt = ls.get("products_under_retirement_pressure") or []
        if rp:
            lines.append(f"- **Repair pressure:** {', '.join(f'`{x}`' for x in rp[:16])}")
        if rt:
            lines.append(f"- **Retirement pressure:** {', '.join(f'`{x}`' for x in rt[:16])}")
        ip = ls.get("products_under_instrumentation_pressure") or []
        if ip:
            lines.append(
                f"- **Signal instrumentation pressure (effective):** {', '.join(f'`{x}`' for x in ip[:16])}"
            )
        fu = ls.get("products_instrumentation_apply_followup") or []
        if fu:
            lines.append(
                f"- **Instrumentation apply follow-up:** {', '.join(f'`{x}`' for x in fu[:16])}"
            )
        rv = ls.get("products_instrumentation_resolved_via_apply") or []
        if rv:
            lines.append(
                f"- **Resolved via apply (adequate post-apply):** {', '.join(f'`{x}`' for x in rv[:16])}"
            )
        if ls.get("summary_narrative"):
            lines.append(f"- **Summary:** {ls.get('summary_narrative')}")
    else:
        lines.append("- *Lifecycle synthesis unavailable — run `argus portfolio lifecycle` after portfolio inputs exist.*")
    sis = payload.get("signal_instrumentation_snapshot") or {}
    if sis.get("note"):
        lines.extend(["", "## Signal instrumentation", "", f"- {sis.get('note')}", ""])
    lines.extend(
        [
            "",
            "## Operator learning (associative)",
            "",
        ]
    )
    ln = payload.get("learning_snapshot") or {}
    if ln.get("present"):
        for t in (ln.get("top_lessons") or [])[:6]:
            lines.append(f"- {t}")
        if not (ln.get("top_lessons") or []):
            lines.append("— *No condensed lessons yet — run policy feedback / effectiveness / learning synthesis inputs.*")
        warn = ln.get("strongest_sparse_signal_warning")
        if warn:
            lines.append(f"- **Sparse / conflict signal:** `{warn}`")
    else:
        lines.append("— *Learning synthesis unavailable.*")
    lines.extend(
        [
            "",
            "## At a glance",
            "",
        ]
    )
    o = payload.get("outcome_summary") or {}
    lines.append(
        f"- **Outcomes:** {o.get('positive_count', 0)} improving · {o.get('negative_count', 0)} struggling · "
        f"{o.get('flat_count', 0)} flat ({o.get('products_evaluated', 0)} products)"
    )
    inv = payload.get("intervention_inbox_summary") or {}
    lines.append(
        f"- **Inbox:** {inv.get('active_items', 0)} active · {inv.get('deferred_items', 0)} snoozed/resolved/quiet"
    )
    pat = payload.get("systemic_patterns_summary") or {}
    lines.append(f"- **Systemic patterns:** {pat.get('pattern_count', 0)} detected")
    lines.append(f"- **Blocked (tracked):** {len(payload.get('blocked_products') or [])}")
    lines.extend(["", "## Who needs attention", ""])
    for row in (payload.get("top_products_to_watch") or [])[:8]:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('product_id')}` — {row.get('reason')}")
    if not (payload.get("top_products_to_watch") or []):
        lines.append("—")
    lines.extend(["", "## Ready to advance", ""])
    for row in (payload.get("top_products_to_advance") or [])[:8]:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('product_id')}` — {row.get('reason')}")
    if not (payload.get("top_products_to_advance") or []):
        lines.append("—")
    lines.extend(["", "## Blocked", ""])
    for row in (payload.get("blocked_products") or [])[:12]:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('product_id')}` — {row.get('reason')}")
    if not (payload.get("blocked_products") or []):
        lines.append("—")
    lines.extend(["", "## Systemic issues (patterns)", ""])
    for t in (pat.get("titles") or [])[:8]:
        lines.append(f"- {t}")
    if not (pat.get("titles") or []):
        lines.append("— None flagged, or run `argus portfolio patterns` to refresh.")
    lines.extend(["", "## Data freshness", ""])
    lines.append(f"Artifacts loaded: {', '.join((payload.get('inputs') or {}).get('artifacts_present') or []) or '—'}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_operator_summary_artifacts(
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
    d = operator_summary_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_operator_summary_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_operator_summary(
    repo_root: Path,
    *,
    limit_history: int = 30,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_operator_summary(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
        persist_creation_candidates_artifact=write_artifacts,
    )
    if write_artifacts:
        write_operator_summary_artifacts(repo_root, payload)
        from argus.portfolio.builder_outcome_validation import (
            write_builder_outcome_validation_artifact,
        )

        bov = payload.get("builder_outcome_validation")
        if isinstance(bov, dict):
            write_builder_outcome_validation_artifact(repo_root, bov)
    return payload
