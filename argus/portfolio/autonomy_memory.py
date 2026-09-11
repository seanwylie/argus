"""
Cross-session autonomy memory — descriptive aggregates over stamped autonomous sessions,
runner service heartbeats, lifecycle influence, promotions, and escalation context.

Includes :func:`evaluate_autonomous_confidence_adjustment` for **bounded, inspectable**
inputs to the autonomous runner (no promotion expansion; no unsafe stop suppression).
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.artifact_coherence import load_artifact_coherence_operational_snapshot
from argus.portfolio.escalation_inbox import (
    CATEGORY_UNSAFE_TO_CONTINUE,
    ESCALATION_INBOX_SCHEMA,
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    escalation_inbox_dir,
)
from argus.portfolio.runner_service import (
    PORTFOLIO_RUNNER_SERVICE_SCHEMA,
    portfolio_runner_service_dir,
)
from argus.products.apply_signal_instrumentation import signal_instrumentation_apply_latest_dir
from argus.products.instrumentation_feedback import (
    load_latest_signal_instrumentation_apply_by_product,
    refine_instrumentation_pressure_with_apply_context,
)
from argus.products.signal_instrumentation import (
    load_latest_signal_instrumentation_by_product,
    signal_instrumentation_latest_dir,
)

PORTFOLIO_AUTONOMY_MEMORY_SCHEMA = "argus.portfolio_autonomy_memory.v1"
PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA = "argus.portfolio_autonomous_runner.v1"
AUTONOMOUS_CONFIDENCE_ADJUSTMENT_SCHEMA = "argus.portfolio_autonomous_confidence_adjustment.v1"

_MIN_SESSIONS_FOR_ADJUSTMENT = 4
_MIN_PATTERN_REPEAT = 3
_MIN_SAFE_CAUTION_SESSIONS = 4


def _portfolio_autonomous_runner_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "autonomous_runner"

# --- Stop taxonomy (string labels from autonomous runner / scheduler) ---

_SAFE_CAUTION_STOPS = frozenset(
    {
        "empty_portfolio",
        "quiescence_recommendation",
        "no_material_change_streak",
        "max_cycles_reached",
    }
)

_RISKY_OR_FAILURE_STOPS = frozenset(
    {
        "portfolio_refresh_failed",
        "portfolio_cycle_failed",
        "portfolio_lifecycle_failed",
        "operator_summary_failed",
        "operator_narrative_failed",
        "explicit_stop_sentinel",
        "intervention_heavy_streak",
    }
)


def portfolio_autonomy_memory_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "autonomy_memory"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sorted_counter_items(c: Counter[str]) -> list[dict[str, Any]]:
    return [{"value": k, "count": int(c[k])} for k in sorted(c.keys())]


def _list_autonomous_session_paths(repo_root: Path, limit: int) -> list[Path]:
    d = _portfolio_autonomous_runner_dir(repo_root)
    if not d.is_dir():
        return []
    paths = [
        p
        for p in d.iterdir()
        if p.is_file() and p.suffix == ".json" and p.name not in ("latest.json",)
    ]
    # Deterministic: lexicographic descending on filename (session ids are timestamp-shaped).
    paths.sort(key=lambda p: p.name, reverse=True)
    lim = max(1, int(limit))
    return paths[:lim]


def _session_payload_ok(raw: dict[str, Any]) -> bool:
    return str(raw.get("schema") or "") == PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA


def _list_runner_service_json_paths(repo_root: Path, limit: int) -> list[Path]:
    d = portfolio_runner_service_dir(repo_root)
    if not d.is_dir():
        return []
    stamped = [p for p in d.iterdir() if p.is_file() and p.suffix == ".json" and "__" in p.stem]
    stamped.sort(key=lambda p: p.name, reverse=True)
    latest = d / "latest.json"
    out: list[Path] = []
    if latest.is_file():
        out.append(latest)
    lim = max(1, int(limit))
    for p in stamped:
        if len(out) >= lim:
            break
        if p not in out:
            out.append(p)
    return out[:lim]


def _guardrail_bucket_for_code(code: str) -> str:
    s = str(code).strip()
    if not s:
        return "empty"
    if "quiescence" in s:
        return "quiescence"
    if "cycle_overall" in s:
        return "cycle_overall"
    if "no_material_change" in s or "material_change" in s:
        return "no_material_change_streak"
    if "intervention" in s:
        return "intervention_heavy"
    if "max_cycles" in s:
        return "max_cycles_cap"
    if "sentinel" in s:
        return "sentinel"
    if "refresh" in s or "cycle_ok" in s or "lifecycle" in s:
        return "pipeline_failure"
    if "operator_summary" in s or "operator_narrative" in s:
        return "dashboard_failure"
    return "other"


def _extract_product_ids_from_lifecycle_influence(lsi: dict[str, Any]) -> list[str]:
    snap = lsi.get("inputs_snapshot") if isinstance(lsi.get("inputs_snapshot"), dict) else {}
    keys = (
        "products_under_repair_pressure",
        "products_under_retirement_pressure",
        "products_entering",
        "products_exiting",
    )
    out: list[str] = []
    for k in keys:
        for x in snap.get(k) or []:
            xs = str(x).strip()
            if xs:
                out.append(xs)
    return out


def _promotion_successful_steps(promotion_exec: dict[str, Any] | None) -> int:
    if not promotion_exec or not isinstance(promotion_exec, dict):
        return 0
    n = 0
    for step in promotion_exec.get("steps") or []:
        if not isinstance(step, dict):
            continue
        if str(step.get("result_status") or "") == "success" and bool(step.get("attempted")):
            n += 1
    return n


def _cycle_overall_from_codes(codes: list[str]) -> str | None:
    for c in codes:
        if "cycle_overall" in c:
            parts = c.split(".")
            if parts:
                return parts[-1]
    return None


def build_portfolio_autonomy_memory_payload(
    repo_root: Path,
    *,
    limit_history: int = 50,
    products_dir: Path | None = None,
    session_paths: list[Path] | None = None,
    runner_paths: list[Path] | None = None,
    escalation_inbox_path: Path | None = None,
) -> dict[str, Any]:
    """
    Build a deterministic cross-session memory payload from on-disk artifacts.

    When ``session_paths`` is None, scans ``runs/portfolio/autonomous_runner/*.json`` (excluding ``latest.json``).
    """
    root = repo_root.resolve()
    evaluated_at = _iso_now()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    paths = session_paths if session_paths is not None else _list_autonomous_session_paths(root, lim)
    sessions: list[dict[str, Any]] = []
    for p in paths:
        raw = _load_json(p)
        if raw and _session_payload_ok(raw):
            sessions.append(raw)

    # Re-sort by finished_at_utc / session_id for stable narrative order (newest first).
    def _sess_key(pl: dict[str, Any]) -> tuple[str, str]:
        fin = str(pl.get("finished_at_utc") or "")
        sid = str(pl.get("session_id") or "")
        return (fin, sid)

    sessions.sort(key=_sess_key, reverse=True)

    stop_reason_c = Counter[str]()
    lifecycle_sig_c = Counter[str]()
    code_bucket_c = Counter[str]()
    guardrail_type_c = Counter[str]()

    product_pressure_hits: Counter[str] = Counter()
    product_safe_stall_hits: Counter[str] = Counter()

    no_progress_ids: list[str] = []
    no_promotion_ids: list[str] = []
    unsafe_session_ids: list[str] = []
    safe_stall_session_ids: list[str] = []

    mixed_sparse_session_ids: list[str] = []

    sessions_with_inspect_specific_cycle_overall = 0
    sessions_with_quiescence_inspect_stop = 0

    for pl in sessions:
        sid = str(pl.get("session_id") or "").strip() or "unknown"
        sr = str(pl.get("stop_reason") or "").strip()
        if sr:
            stop_reason_c[sr] += 1

        lsi = pl.get("lifecycle_session_influence") if isinstance(pl.get("lifecycle_session_influence"), dict) else {}
        prim = str(lsi.get("primary_signal") or "neutral").strip() or "neutral"
        lifecycle_sig_c[prim] += 1
        if prim == "mixed_sparse":
            mixed_sparse_session_ids.append(sid)

        codes = [str(c) for c in (pl.get("stop_reason_codes") or []) if str(c).strip()]
        for c in codes:
            code_bucket_c[c] += 1
            guardrail_type_c[_guardrail_bucket_for_code(c)] += 1
        if any(
            c.endswith("inspect_specific_products") or c.endswith(".inspect_specific_products") for c in codes
        ):
            sessions_with_inspect_specific_cycle_overall += 1
        if sr == "quiescence_recommendation" and any(
            "quiescence" in c and c.endswith(".inspect") for c in codes
        ):
            sessions_with_quiescence_inspect_stop += 1

        pids = _extract_product_ids_from_lifecycle_influence(lsi)
        for pid in pids:
            product_pressure_hits[pid] += 1

        if sr in _SAFE_CAUTION_STOPS:
            safe_stall_session_ids.append(sid)
            for pid in pids:
                product_safe_stall_hits[pid] += 1

        if sr in ("quiescence_recommendation", "no_material_change_streak"):
            no_progress_ids.append(sid)

        pe = pl.get("promotion_execution") if isinstance(pl.get("promotion_execution"), dict) else {}
        promo_actions = [a for a in (pl.get("promotable_actions") or []) if isinstance(a, dict)]
        blocked = [b for b in (pl.get("blocked_promotions") or []) if isinstance(b, dict)]
        if str(pe.get("skipped_reason") or "").strip():
            no_promotion_ids.append(sid)
        elif _promotion_successful_steps(pe) == 0 and (promo_actions or blocked):
            no_promotion_ids.append(sid)

        if sr in _RISKY_OR_FAILURE_STOPS:
            unsafe_session_ids.append(sid)
        elif sr == "cycle_overall_recommendation":
            cov = str(_cycle_overall_from_codes(codes) or "").strip()
            # `inspect_specific_products` is caution-class for autonomy; not treated as unsafe-in-window here.
            if cov in ("request_human_review", "repair_imports"):
                unsafe_session_ids.append(sid)

    repeated_stop_reasons = [x for x in _sorted_counter_items(stop_reason_c) if x["count"] >= 2]
    repeated_lifecycle_primary_signals = [x for x in _sorted_counter_items(lifecycle_sig_c) if x["count"] >= 2]

    sparse_mixed_sessions_n = len(set(mixed_sparse_session_ids))
    repeated_sparse_or_mixed_conditions = {
        "sessions_with_primary_mixed_sparse": sparse_mixed_sessions_n,
        "distinct_session_ids": sorted(set(mixed_sparse_session_ids)),
    }

    safe_but_stalled_products = [
        {"product_id": k, "sessions_with_safe_caution_stop": int(product_safe_stall_hits[k])}
        for k in sorted(product_safe_stall_hits.keys())
        if product_safe_stall_hits[k] >= 2
    ]

    products_repeatedly_under_attention = [
        {
            "product_id": k,
            "mentions_across_sessions": int(product_pressure_hits[k]),
        }
        for k in sorted(product_pressure_hits.keys())
        if product_pressure_hits[k] >= 2
    ]

    recurring_guardrail_stops_by_type = _sorted_counter_items(guardrail_type_c)

    recurring_no_progress_sessions = sorted(set(no_progress_ids))
    recurring_no_promotion_sessions = sorted(set(no_promotion_ids))

    confidence_notes: list[str] = []
    caution_notes: list[str] = []

    if repeated_stop_reasons:
        top = repeated_stop_reasons[0]
        confidence_notes.append(
            f"Repeated stop_reason `{top['value']}` ({top['count']}× in window) — "
            "if guardrails match stable environmental sparsity, repeated stops can be informative rather than novel risk."
        )
    if sparse_mixed_sessions_n >= 3:
        confidence_notes.append(
            f"Primary lifecycle signal `mixed_sparse` appeared in {sparse_mixed_sessions_n} sessions — "
            "inventory/interpretation sparsity may be structural; distinguish from one-off noise."
        )
    if len(set(safe_stall_session_ids)) >= 3 and len(set(unsafe_session_ids)) == 0:
        confidence_notes.append(
            "Several consecutive sessions ended on caution-class stops with no failure-class stops in-window — "
            "evidence may support treating repetition as conservative looping, not new hazard."
        )

    if len(set(unsafe_session_ids)) >= 2:
        caution_notes.append(
            f"Multiple sessions ({len(set(unsafe_session_ids))}) flagged failure- or review-class stops — "
            "treat autonomy limits as materially constrained until inputs change."
        )
    for r in repeated_stop_reasons:
        if r["value"] in _RISKY_OR_FAILURE_STOPS and r["count"] >= 2:
            caution_notes.append(
                f"Repeated risky stop `{r['value']}` ({r['count']}×) — persistence indicates unresolved conditions, not impatience."
            )
    if len(recurring_no_promotion_sessions) >= 3:
        caution_notes.append(
            "Promotion success rarely appears across sessions — lifecycle moves may be blocked; do not infer slack from silence."
        )

    lessons: list[str] = []
    if sessions:
        lessons.append(
            f"Analyzed {len(sessions)} autonomous session artifact(s); newest finished_at in window drives ordering."
        )
    if repeated_stop_reasons:
        lessons.append(
            "Most frequent repeated stop reasons are listed under `repeated_stop_reasons` — compare against "
            "`recurring_guardrail_stops_by_type` for mechanism-level clustering."
        )
    if products_repeatedly_under_attention:
        lessons.append(
            "Products with repeated lifecycle pressure mentions may deserve human triage even when stops look 'soft'."
        )
    if not sessions:
        lessons.append(
            "No stamped autonomous session JSON found — run `argus portfolio run-autonomous` to populate memory."
        )

    sparse_signal_persistence = {
        "mixed_sparse_primary_count": int(lifecycle_sig_c.get("mixed_sparse", 0)),
        "sessions_window": len(sessions),
        "notes": [
            "mixed_sparse encodes small portfolios or ambiguous lanes — persistence across sessions increases confidence that sparsity is chronic.",
            "Pair with `stop_reason_frequencies`: quiescence/no-material streaks plus mixed_sparse often indicate safe caution loops.",
        ],
    }

    # Runner service / escalation provenance (descriptive only).
    rpaths = runner_paths if runner_paths is not None else _list_runner_service_json_paths(root, min(10, lim))
    runner_summaries: list[dict[str, Any]] = []
    for rp in rpaths:
        rw = _load_json(rp)
        if not rw or str(rw.get("schema") or "") != PORTFOLIO_RUNNER_SERVICE_SCHEMA:
            continue
        runner_summaries.append(
            {
                "path": str(rp.relative_to(root)) if rp.is_relative_to(root) else str(rp),
                "service_run_id": rw.get("service_run_id"),
                "loop_count": rw.get("loop_count"),
                "last_autonomous_stop_reason": rw.get("last_autonomous_stop_reason"),
                "current_status": rw.get("current_status"),
                "stop_reason": rw.get("stop_reason"),
            }
        )
    runner_summaries.sort(key=lambda x: str(x.get("path")))

    esc_path = escalation_inbox_path if escalation_inbox_path is not None else (escalation_inbox_dir(root) / "latest.json")
    esc = _load_json(esc_path) if esc_path.is_file() else None
    esc_ok = bool(esc and str(esc.get("schema") or "") == ESCALATION_INBOX_SCHEMA)
    esc_products: Counter[str] = Counter()
    if esc_ok:
        for row in esc.get("open_items") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "").strip()
            if pid:
                esc_products[pid] += 1

    confidence_pattern_metrics = {
        "sessions_with_inspect_specific_cycle_overall": sessions_with_inspect_specific_cycle_overall,
        "sessions_with_quiescence_inspect_stop": sessions_with_quiescence_inspect_stop,
    }

    session_window_summary = {
        "sessions_loaded": len(sessions),
        "limit_history": lim,
        "newest_session_id": str(sessions[0].get("session_id")) if sessions else None,
        "oldest_session_id": str(sessions[-1].get("session_id")) if sessions else None,
        "runner_heartbeat_snapshots": len(runner_summaries),
        "escalation_inbox_open_product_ids_distinct": len(esc_products),
    }

    confidence_adjustment_hints = list(confidence_notes)
    caution_persistence_notes = list(caution_notes)

    inst_by = load_latest_signal_instrumentation_by_product(root)
    apply_by = load_latest_signal_instrumentation_apply_by_product(root)
    inst_ref = refine_instrumentation_pressure_with_apply_context(
        inst_by_product=inst_by,
        apply_by_product=apply_by,
    )
    pressure_sorted = list(inst_ref["products_under_instrumentation_pressure_effective"])
    pressure_set = set(pressure_sorted)
    followup_sorted = list(inst_ref["products_instrumentation_apply_followup"])
    followup_set = set(followup_sorted)
    resolved_sorted = list(inst_ref["products_instrumentation_resolved_via_apply"])
    resolved_set = set(resolved_sorted)
    signal_instrumentation_advisory_notes: list[str] = []
    if sessions_with_inspect_specific_cycle_overall >= 2 and pressure_set:
        signal_instrumentation_advisory_notes.append(
            "Repeated autonomous stops on cycle-overall `inspect_specific_products` coincide with products "
            f"still under effective signal instrumentation pressure ({len(pressure_set)} id(s)) — "
            "sparse inspect loops may reflect thin observability; run `argus products instrument-signals` "
            "before expecting optimization evidence."
        )
    elif sessions_with_inspect_specific_cycle_overall >= 2 and not pressure_set and resolved_set:
        signal_instrumentation_advisory_notes.append(
            "Repeated autonomous stops on cycle-overall `inspect_specific_products` while latest worker instrumentation "
            f"applies recorded adequate post-apply validation for {len(resolved_set)} id(s) — stagnation may reflect "
            "product reality rather than missing observability; prefer learning synthesis over expanding contracts."
        )
    att_pids = {
        str(x.get("product_id"))
        for x in products_repeatedly_under_attention
        if isinstance(x, dict) and x.get("product_id")
    }
    overlap_inst = sorted(att_pids & pressure_set)
    if overlap_inst and sessions_with_inspect_specific_cycle_overall >= 2:
        signal_instrumentation_advisory_notes.append(
            "Instrumentation-weak products also repeat in lifecycle attention snapshots: "
            + ", ".join(f"`{p}`" for p in overlap_inst[:12])
            + "."
        )
    overlap_fu = sorted(att_pids & followup_set)
    if overlap_fu and sessions_with_inspect_specific_cycle_overall >= 2:
        signal_instrumentation_advisory_notes.append(
            "Products with partial or still-weak instrumentation applies repeat in lifecycle attention: "
            + ", ".join(f"`{p}`" for p in overlap_fu[:12])
            + "."
        )

    recurring_patterns = {
        "repeated_stop_reasons": repeated_stop_reasons,
        "repeated_lifecycle_primary_signals": repeated_lifecycle_primary_signals,
        "repeated_sparse_or_mixed": repeated_sparse_or_mixed_conditions,
        "safe_but_stalled_products": safe_but_stalled_products,
        "recurring_no_progress_sessions": recurring_no_progress_sessions,
        "recurring_no_promotion_sessions": recurring_no_promotion_sessions,
        "recurring_guardrail_stops_by_type": recurring_guardrail_stops_by_type,
        "unsafe_session_ids_in_window": sorted(set(unsafe_session_ids)),
        "safe_caution_session_ids_in_window": sorted(set(safe_stall_session_ids)),
    }

    payload: dict[str, Any] = {
        "schema": PORTFOLIO_AUTONOMY_MEMORY_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "session_window_summary": session_window_summary,
        "confidence_pattern_metrics": confidence_pattern_metrics,
        "stop_reason_frequencies": {k: int(stop_reason_c[k]) for k in sorted(stop_reason_c.keys())},
        "lifecycle_signal_frequencies": {k: int(lifecycle_sig_c[k]) for k in sorted(lifecycle_sig_c.keys())},
        "recurring_patterns": recurring_patterns,
        "repeated_stop_reasons": repeated_stop_reasons,
        "repeated_lifecycle_primary_signals": repeated_lifecycle_primary_signals,
        "repeated_sparse_or_mixed_conditions": repeated_sparse_or_mixed_conditions,
        "safe_but_stalled_products": safe_but_stalled_products,
        "recurring_no_progress_sessions": recurring_no_progress_sessions,
        "recurring_no_promotion_sessions": recurring_no_promotion_sessions,
        "recurring_guardrail_stops_by_type": recurring_guardrail_stops_by_type,
        "confidence_accumulation_notes": confidence_notes,
        "caution_persistence_notes": caution_persistence_notes,
        "products_repeatedly_under_attention": products_repeatedly_under_attention,
        "escalation_open_product_tally": {k: int(esc_products[k]) for k in sorted(esc_products.keys())},
        "confidence_adjustment_hints": confidence_adjustment_hints,
        "sparse_signal_persistence": sparse_signal_persistence,
        "top_autonomy_lessons": lessons,
        "runner_service_heartbeat": runner_summaries,
        "signal_instrumentation_advisory_notes": signal_instrumentation_advisory_notes,
        "signal_instrumentation_context": {
            "artifacts_loaded_count": len(inst_by),
            "apply_artifacts_loaded_count": len(apply_by),
            "products_under_instrumentation_pressure": pressure_sorted,
            "products_instrumentation_apply_followup": followup_sorted,
            "products_instrumentation_resolved_via_apply": resolved_sorted,
            "latest_dir_present": signal_instrumentation_latest_dir(root).is_dir(),
            "apply_latest_dir_present": signal_instrumentation_apply_latest_dir(root).is_dir(),
        },
        "inputs": {
            "limit_history": lim,
            "products_dir": str(products_dir.resolve()) if products_dir is not None else None,
            "session_paths": [str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in paths],
            "escalation_inbox_path": str(esc_path) if esc_path.is_file() else None,
            "escalation_inbox_loaded": esc_ok,
            "signal_instrumentation_latest_dir": str(signal_instrumentation_latest_dir(root)),
            "signal_instrumentation_apply_latest_dir": str(signal_instrumentation_apply_latest_dir(root)),
        },
    }
    return payload


def escalation_blocks_autonomous_confidence_adjustment(repo_root: Path) -> tuple[bool, str]:
    """Return (blocked, reason_code) when escalation inbox forbids confidence relaxation."""
    path = escalation_inbox_dir(repo_root) / "latest.json"
    raw = _load_json(path)
    if not raw or str(raw.get("schema") or "") != ESCALATION_INBOX_SCHEMA:
        return False, ""
    for item in raw.get("open_items") or []:
        if not isinstance(item, dict):
            continue
        cat = str(item.get("category") or "")
        sev = str(item.get("severity") or "")
        src = str(item.get("source") or "")
        if cat == CATEGORY_UNSAFE_TO_CONTINUE:
            return True, "open_escalation.unsafe_to_continue"
        if sev == SEVERITY_CRITICAL:
            return True, "open_escalation.severity_critical"
        if src == "blocked_promotion" and sev in (SEVERITY_HIGH, SEVERITY_CRITICAL):
            return True, "open_escalation.serious_blocked_promotion"
    return False, ""


def _patterns_support_confidence_adjustment(mem: dict[str, Any]) -> tuple[bool, list[str]]:
    metrics = mem.get("confidence_pattern_metrics") or {}
    rp = mem.get("recurring_patterns") or {}
    lf = mem.get("lifecycle_signal_frequencies") or {}
    matched: list[str] = []
    if int(metrics.get("sessions_with_inspect_specific_cycle_overall") or 0) >= _MIN_PATTERN_REPEAT:
        matched.append("repeated_cycle_overall_inspect_specific_products")
    if int(metrics.get("sessions_with_quiescence_inspect_stop") or 0) >= _MIN_PATTERN_REPEAT:
        matched.append("repeated_quiescence_inspect")
    if int(lf.get("mixed_sparse") or 0) >= _MIN_PATTERN_REPEAT:
        matched.append("repeated_lifecycle_mixed_sparse")
    if len(rp.get("recurring_no_progress_sessions") or []) >= _MIN_PATTERN_REPEAT:
        matched.append("recurring_no_progress")
    if len(rp.get("recurring_no_promotion_sessions") or []) >= _MIN_PATTERN_REPEAT:
        matched.append("recurring_no_promotion")
    safe_n = len(rp.get("safe_caution_session_ids_in_window") or [])
    unsafe = rp.get("unsafe_session_ids_in_window") or []
    if safe_n >= _MIN_SAFE_CAUTION_SESSIONS and not unsafe:
        matched.append("repeated_caution_class_without_unsafe")
    return (len(matched) > 0, sorted(matched))


def evaluate_autonomous_confidence_adjustment(
    repo_root: Path,
    *,
    limit_history: int = 30,
) -> dict[str, Any]:
    """
    Deterministic evaluation: whether bounded confidence adjustment may apply.

    Reads stamped autonomous session history (autonomy memory), escalation inbox, signal
    instrumentation context, and the **durable** substrate coherence snapshot
    (``runs/debug/artifact_coherence/latest.json``) without re-evaluating coherence.

    Does not mutate disk. The autonomous runner may consume at most one deferral per session
    for low-risk guardrails only, and/or extend the iteration budget by +1 when eligible.

    Call **after** a portfolio cycle (and coherence write) when possible so
    ``substrate_coherence_context`` reflects the latest persisted audit.
    """
    root = repo_root.resolve()
    lim = max(1, int(limit_history))
    mem = build_portfolio_autonomy_memory_payload(root, limit_history=lim)
    snap = load_artifact_coherence_operational_snapshot(root)
    substrate_ctx = {
        "coherence_artifact_present": bool(snap.get("present")),
        "overall_status": snap.get("overall_status") if snap.get("present") else None,
        "run_id": snap.get("run_id") if snap.get("present") else None,
        "evaluated_at_utc": snap.get("evaluated_at_utc") if snap.get("present") else None,
    }

    base: dict[str, Any] = {
        "schema": AUTONOMOUS_CONFIDENCE_ADJUSTMENT_SCHEMA,
        "autonomy_memory_consulted": True,
        "autonomy_memory_run_id": mem.get("run_id"),
        "substrate_coherence_context": substrate_ctx,
        "eligible": False,
        "block_reason": None,
        "patterns_matched": [],
        "effective_max_cycles_delta": 0,
        "deferral_available": False,
        "safe_to_adjust_notes": [],
    }

    blocked, br = escalation_blocks_autonomous_confidence_adjustment(root)
    if blocked:
        base["block_reason"] = br
        base["safe_to_adjust_notes"].append(f"Blocked by escalation inbox: {br}.")
        return base

    if snap.get("present") and str(snap.get("overall_status") or "").strip().lower() == "invalid":
        base["block_reason"] = "substrate_coherence_invalid"
        base["safe_to_adjust_notes"].append(
            "Durable artifact_coherence latest reports overall_status=invalid; deferrals and +1 cycle budget disabled."
        )
        return base

    sw = mem.get("session_window_summary") or {}
    if int(sw.get("sessions_loaded") or 0) < _MIN_SESSIONS_FOR_ADJUSTMENT:
        base["block_reason"] = "insufficient_session_history"
        base["safe_to_adjust_notes"].append(
            f"Need at least {_MIN_SESSIONS_FOR_ADJUSTMENT} stamped sessions in-window."
        )
        return base

    rp = mem.get("recurring_patterns") or {}
    if rp.get("unsafe_session_ids_in_window"):
        base["block_reason"] = "unsafe_sessions_in_autonomy_memory_window"
        base["safe_to_adjust_notes"].append("Recent window includes failure-class autonomous stops.")
        return base

    ok_pat, matched = _patterns_support_confidence_adjustment(mem)
    if not ok_pat:
        base["block_reason"] = "no_repeated_low_risk_pattern"
        base["safe_to_adjust_notes"].append(
            "No qualifying repetition of inspect / mixed_sparse / no-progress / no-promotion / caution-only signals."
        )
        return base

    notes = [
        "Escalation inbox has no unsafe_to_continue, critical severity, or high-severity blocked-promotion blockers.",
        "Autonomy memory window has no recorded unsafe autonomous session ids.",
        f"Matched patterns: {', '.join(matched)}.",
        "Session may use at most one deferral for inspect-class or no-material streak stops, and/or +1 max cycle.",
    ]
    if snap.get("present") and str(snap.get("overall_status") or "").strip().lower() == "degraded":
        notes.append(
            "Substrate coherence is degraded (yellow-light); deferrals remain allowed — pair with degraded_substrate policy."
        )
    return {
        **base,
        "eligible": True,
        "block_reason": None,
        "patterns_matched": matched,
        "effective_max_cycles_delta": 1,
        "deferral_available": True,
        "safe_to_adjust_notes": notes,
    }


def render_portfolio_autonomy_memory_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio autonomy memory",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Session window",
        "",
    ]
    sw = payload.get("session_window_summary") or {}
    for k in sorted(sw.keys()):
        lines.append(f"- **{k}:** {sw[k]}")
    lines.extend(["", "## Stop reason frequencies", ""])
    fr = payload.get("stop_reason_frequencies") or {}
    if not fr:
        lines.append("—")
    else:
        for k in sorted(fr.keys()):
            lines.append(f"- `{k}`: {fr[k]}")

    lines.extend(["", "## Lifecycle primary signal frequencies", ""])
    lf = payload.get("lifecycle_signal_frequencies") or {}
    if not lf:
        lines.append("—")
    else:
        for k in sorted(lf.keys()):
            lines.append(f"- `{k}`: {lf[k]}")

    sic = payload.get("signal_instrumentation_context") or {}
    adv = payload.get("signal_instrumentation_advisory_notes") or []
    if sic or adv:
        lines.extend(["", "## Signal instrumentation (advisory)", ""])
        if sic:
            lines.append(
                f"- **Artifacts loaded:** {sic.get('artifacts_loaded_count')} · **apply artifacts:** "
                f"{sic.get('apply_artifacts_loaded_count')} · "
                f"**latest dir present:** {sic.get('latest_dir_present')} · "
                f"**apply latest dir present:** {sic.get('apply_latest_dir_present')} · "
                f"**pressure ids:** {', '.join(f'`{p}`' for p in (sic.get('products_under_instrumentation_pressure') or [])[:16]) or '—'} · "
                f"**apply follow-up:** {', '.join(f'`{p}`' for p in (sic.get('products_instrumentation_apply_followup') or [])[:12]) or '—'} · "
                f"**resolved via apply:** {', '.join(f'`{p}`' for p in (sic.get('products_instrumentation_resolved_via_apply') or [])[:12]) or '—'}"
            )
        for n in adv:
            lines.append(f"- {n}")
        lines.append("")

    lines.extend(
        [
            "",
            "## Interpretation (advisory)",
            "",
            "### What looks chronic",
            "",
        ]
    )
    chronic_bits: list[str] = []
    rp = payload.get("recurring_patterns") or {}
    rsr = rp.get("repeated_stop_reasons") or payload.get("repeated_stop_reasons") or []
    if rsr:
        chronic_bits.append(
            "Repeated **stop reasons** (≥2×) and **lifecycle primaries** suggest stable environmental cues, not one-off noise."
        )
    rs = (payload.get("repeated_sparse_or_mixed_conditions") or {}).get("sessions_with_primary_mixed_sparse")
    if isinstance(rs, int) and rs >= 2:
        chronic_bits.append("`mixed_sparse` lifecycle primary recurring — small or ambiguous portfolio may be structural.")
    if not chronic_bits:
        chronic_bits.append("Insufficient repeated signals in-window to label chronic patterns.")
    for b in chronic_bits:
        lines.append(f"- {b}")

    lines.extend(["", "### What looks safely repetitive", ""])
    safe_bits: list[str] = []
    safe_ids = rp.get("safe_caution_session_ids_in_window") or []
    unsafe_ids = rp.get("unsafe_session_ids_in_window") or []
    if safe_ids and not unsafe_ids:
        safe_bits.append(
            "Sessions ending on caution-class stops without failure-class stops in-window may reflect conservative looping."
        )
    if any(str(x.get("value")) in _SAFE_CAUTION_STOPS for x in (rsr if isinstance(rsr, list) else [])):
        safe_bits.append("Repeated **quiescence**, **no_material_change**, or **max_cycles** stops often indicate stable stall, not new risk.")
    if not safe_bits:
        safe_bits.append("No strong safely-repetitive signature in-window (see per-session stops and risky list).")
    for b in safe_bits:
        lines.append(f"- {b}")

    lines.extend(["", "### What still looks genuinely risky", ""])
    risk_bits: list[str] = []
    if unsafe_ids:
        risk_bits.append(f"Failure- or review-class sessions in-window: {len(unsafe_ids)} distinct session id(s) — see payload `unsafe_session_ids_in_window`.")
    caut = payload.get("caution_persistence_notes") or []
    for c in caut[:6]:
        risk_bits.append(c)
    if not risk_bits:
        risk_bits.append("No failure-class stops aggregated in this window — still verify latest session and inbox.")
    for b in risk_bits:
        lines.append(f"- {b}")

    lines.extend(["", "## Recurring patterns (structured)", ""])
    for k in sorted(rp.keys()) if isinstance(rp, dict) else []:
        lines.append(f"- **{k}:** {rp[k]}")

    lines.extend(["", "## Confidence accumulation notes", ""])
    for x in payload.get("confidence_accumulation_notes") or []:
        lines.append(f"- {x}")
    lines.extend(["", "## Caution persistence notes", ""])
    for x in payload.get("caution_persistence_notes") or []:
        lines.append(f"- {x}")

    lines.extend(["", "## Products repeatedly under attention", ""])
    pr = payload.get("products_repeatedly_under_attention") or []
    if not pr:
        lines.append("—")
    else:
        for row in pr:
            lines.append(f"- `{row.get('product_id')}` — mentions {row.get('mentions_across_sessions')}")

    lines.extend(["", "## Sparse signal persistence", ""])
    sp = payload.get("sparse_signal_persistence") or {}
    lines.append(f"- **mixed_sparse primary (count in window):** {sp.get('mixed_sparse_primary_count')}")
    for n in sp.get("notes") or []:
        lines.append(f"- {n}")

    lines.extend(["", "## Top autonomy lessons", ""])
    for x in payload.get("top_autonomy_lessons") or []:
        lines.append(f"- {x}")

    lines.extend(["", "## Runner service heartbeat (snapshots)", ""])
    for row in payload.get("runner_service_heartbeat") or []:
        lines.append(f"- `{row.get('path')}` — loops {row.get('loop_count')} · last auto stop `{row.get('last_autonomous_stop_reason')}`")

    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_autonomy_memory_artifacts(
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
    d = portfolio_autonomy_memory_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_autonomy_memory_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_autonomy_memory(
    repo_root: Path,
    *,
    limit_history: int = 50,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = build_portfolio_autonomy_memory_payload(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
    )
    if write_artifacts:
        write_portfolio_autonomy_memory_artifacts(repo_root, payload)
    return payload


__all__ = [
    "AUTONOMOUS_CONFIDENCE_ADJUSTMENT_SCHEMA",
    "PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA",
    "PORTFOLIO_AUTONOMY_MEMORY_SCHEMA",
    "build_portfolio_autonomy_memory_payload",
    "evaluate_autonomous_confidence_adjustment",
    "escalation_blocks_autonomous_confidence_adjustment",
    "portfolio_autonomy_memory_dir",
    "render_portfolio_autonomy_memory_markdown",
    "run_portfolio_autonomy_memory",
    "write_portfolio_autonomy_memory_artifacts",
]
