"""
Load operator-console artifacts from ``runs/`` — pure, testable helpers (no Streamlit).

Each artifact is read from ``latest.json`` at a known path relative to the repo root.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.dashboard.operator_guidance import enrich_escalation_item
from argus.portfolio.artifact_coherence import ARTIFACT_COHERENCE_REPORT_SCHEMA
from argus.portfolio.autonomous_runner import PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA
from argus.portfolio.escalation_inbox import (
    ACK_STATE_RESOLVED,
    ACK_STATE_SNOOZED,
    CATEGORY_INFORMATIONAL,
    ESCALATION_INBOX_SCHEMA,
)
from argus.portfolio.runner_service import PORTFOLIO_RUNNER_SERVICE_SCHEMA
from argus.world_context.persist import (
    WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
    WORLD_CONTEXT_INTERPRETATION_SCHEMA,
    WORLD_CONTEXT_SCHEMA,
)

AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS = 10
AUTONOMOUS_PER_CYCLE_CELL_MAX_LEN = 120

# If heartbeat says the service is active but the file has not been updated in this long, flag stale.
RUNNER_SERVICE_STALE_ACTIVE_SECONDS = 900


def _iso_mtime(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
    except OSError:
        return None


def console_artifact_specs() -> dict[str, str]:
    """Logical key -> posix path relative to repo root."""
    return {
        "operator_summary": "runs/dashboard/operator_summary/latest.json",
        "narrative": "runs/dashboard/narrative/latest.json",
        "operator_queue": "runs/portfolio/operator_queue/latest.json",
        "portfolio_lifecycle": "runs/portfolio/lifecycle/latest.json",
        "portfolio_strategy": "runs/portfolio/strategy/latest.json",
        "learning_synthesis": "runs/policy/learning_synthesis/latest.json",
        "intervention_inbox": "runs/portfolio/intervention_inbox/latest.json",
        "autonomous_runner": "runs/portfolio/autonomous_runner/latest.json",
        "runner_service": "runs/portfolio/runner_service/latest.json",
        "escalation_inbox": "runs/portfolio/escalation_inbox/latest.json",
        "builder_activity": "runs/portfolio/builder_activity/latest.json",
        "artifact_coherence": "runs/debug/artifact_coherence/latest.json",
        "world_context": "runs/world_context/latest.json",
        "world_context_interpretation": "runs/world_context/interpretation/latest.json",
        "world_context_creation_candidates": "runs/world_context/creation_candidates/latest.json",
    }


@dataclass
class LoadedArtifact:
    """Result of loading one ``latest.json`` file."""

    key: str
    rel_path: str
    abs_path: Path
    exists: bool
    mtime_utc: str | None = None
    schema: str | None = None
    data: dict[str, Any] | None = None
    error: str | None = None


def load_json_artifact(repo_root: Path, key: str, rel_path: str) -> LoadedArtifact:
    """Load a single JSON file; never raises — errors recorded on ``LoadedArtifact``."""
    root = repo_root.resolve()
    abs_path = (root / rel_path).resolve()
    try:
        abs_path.relative_to(root)
    except ValueError:
        return LoadedArtifact(
            key=key,
            rel_path=rel_path,
            abs_path=abs_path,
            exists=False,
            error="path escapes repo root",
        )
    if not abs_path.is_file():
        return LoadedArtifact(
            key=key,
            rel_path=rel_path,
            abs_path=abs_path,
            exists=False,
            mtime_utc=None,
        )
    mtime = _iso_mtime(abs_path)
    try:
        raw = json.loads(abs_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return LoadedArtifact(
            key=key,
            rel_path=rel_path,
            abs_path=abs_path,
            exists=True,
            mtime_utc=mtime,
            error=str(e),
        )
    if not isinstance(raw, dict):
        return LoadedArtifact(
            key=key,
            rel_path=rel_path,
            abs_path=abs_path,
            exists=True,
            mtime_utc=mtime,
            error="root JSON value is not an object",
        )
    return LoadedArtifact(
        key=key,
        rel_path=rel_path,
        abs_path=abs_path,
        exists=True,
        mtime_utc=mtime,
        schema=str(raw.get("schema") or "") or None,
        data=raw,
    )


def load_operator_console_bundle(repo_root: Path) -> dict[str, LoadedArtifact]:
    """Load all console artifacts; missing files yield ``exists=False`` without error."""
    specs = console_artifact_specs()
    return {k: load_json_artifact(repo_root, k, p) for k, p in specs.items()}


OPERATOR_CONSOLE_SNAPSHOT_SCHEMA = "argus.operator_console_snapshot.v1"


def build_operator_console_snapshot(
    repo_root: Path,
    bundle: dict[str, LoadedArtifact],
) -> dict[str, Any]:
    """
    Single JSON-serializable document with every artifact the operator console loads (all tabs).

    Suitable for export to other systems; keys are sorted for stable diffs.
    """
    root_s = str(repo_root.resolve())
    artifacts: dict[str, Any] = {}
    for key in sorted(bundle.keys()):
        art = bundle[key]
        artifacts[key] = {
            "rel_path": art.rel_path,
            "exists": art.exists,
            "mtime_utc": art.mtime_utc,
            "schema": art.schema,
            "error": art.error,
            "data": art.data,
        }
    ac_art = bundle.get("artifact_coherence")
    wc_art = bundle.get("world_context")
    wci_art = bundle.get("world_context_interpretation")
    wcc_art = bundle.get("world_context_creation_candidates")
    views: dict[str, Any] = {
        "artifact_coherence": artifact_coherence_view(ac_art.data if ac_art else None),
        "world_context": world_context_view(
            wc_art.data if wc_art else None,
            wci_art.data if wci_art else None,
            wcc_art.data if wcc_art else None,
        ),
    }
    return {
        "schema": OPERATOR_CONSOLE_SNAPSHOT_SCHEMA,
        "generated_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "repo_root": root_s,
        "artifact_specs": console_artifact_specs(),
        "artifacts": artifacts,
        "views": views,
    }


# --- View-model helpers (deterministic summaries for UI) ---


def overview_from_summary(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True}
    bas = data.get("builder_activity_snapshot")
    if not isinstance(bas, dict):
        bas = {}
    return {
        "empty": False,
        "headline_status": data.get("headline_status"),
        "confidence_level": data.get("confidence_level"),
        "recommended_next_step": data.get("recommended_next_step"),
        "evaluated_at_utc": data.get("evaluated_at_utc"),
        "run_id": data.get("run_id"),
        "portfolio_state": data.get("portfolio_state"),
        "zero_state": bool(data.get("zero_state")),
        "external_context_advisory": data.get("external_context_advisory"),
        "external_context_situation_brief": data.get("external_context_situation_brief"),
        "world_context_present": bool(data.get("world_context_present")),
        "zero_state_creation_candidates_summary": data.get("zero_state_creation_candidates_summary"),
        "builder_activity_snapshot": bas,
    }


def world_context_view(
    data: dict[str, Any] | None,
    interpretation: dict[str, Any] | None = None,
    creation_candidates: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    View-model for ``runs/world_context/latest.json`` (schema ``argus.world_context.v1``),
    optionally merged with ``runs/world_context/interpretation/latest.json``.
    """
    if not data:
        return {"empty": True, "present": False, "interpretation_present": False}
    if str(data.get("schema") or "") != WORLD_CONTEXT_SCHEMA:
        return {
            "empty": True,
            "present": False,
            "note": "file exists but schema is not argus.world_context.v1",
            "interpretation_present": False,
        }
    summ = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    sigs = data.get("signals") if isinstance(data.get("signals"), list) else []
    rows: list[dict[str, Any]] = []
    for s in sigs[:32]:
        if not isinstance(s, dict):
            continue
        rows.append(
            {
                "source": s.get("source"),
                "entity": s.get("entity"),
                "signal_type": s.get("signal_type"),
                "value": s.get("value"),
                "unit": s.get("unit"),
                "freshness": s.get("freshness_status"),
                "confidence": s.get("confidence"),
            }
        )
    out: dict[str, Any] = {
        "empty": False,
        "present": True,
        "advisory_only": bool(data.get("advisory_only")),
        "disclaimer": str(data.get("disclaimer") or ""),
        "generated_at_utc": data.get("generated_at_utc"),
        "headline": summ.get("headline"),
        "signal_count": summ.get("signal_count"),
        "fresh_count": summ.get("fresh_count"),
        "stale_count": summ.get("stale_count"),
        "sources": summ.get("sources"),
        "signals_preview_rows": rows,
        "interpretation_present": False,
        "creation_candidates_present": False,
    }
    if interpretation and str(interpretation.get("schema") or "") == WORLD_CONTEXT_INTERPRETATION_SCHEMA:
        out["interpretation_present"] = True
        out["operator_narrative"] = interpretation.get("operator_narrative")
        out["interpretation_entities"] = interpretation.get("entities_ordered")
        out["interpretation_per_entity"] = interpretation.get("per_entity")
        out["interpretation_comparison"] = interpretation.get("comparison")
        out["notable_patterns"] = interpretation.get("notable_patterns")
        out["interpretation_limitations"] = interpretation.get("limitations")
    if creation_candidates and str(creation_candidates.get("schema") or "") == WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA:
        out["creation_candidates_present"] = True
        out["creation_candidates_rows"] = creation_candidates.get("candidates") or []
        out["creation_candidates_disclaimer"] = str(creation_candidates.get("disclaimer") or "")
        out["creation_candidates_situation_summary"] = creation_candidates.get("situation_summary")
        out["creation_candidates_primary_id"] = creation_candidates.get("primary_candidate_id")
    else:
        out["creation_candidates_present"] = bool(creation_candidates)
    return out


def artifact_coherence_view(data: dict[str, Any] | None) -> dict[str, Any]:
    """
    View-model for durable ``runs/debug/artifact_coherence/latest.json`` (read-only; no evaluation).

    Accepts the raw JSON object (schema ``argus.artifact_coherence_report.v1``).
    """
    if not data:
        return {"empty": True, "present": False}
    if str(data.get("schema") or "") != ARTIFACT_COHERENCE_REPORT_SCHEMA:
        return {
            "empty": True,
            "present": False,
            "note": "file exists but schema is not argus.artifact_coherence_report.v1",
        }
    os = str(data.get("overall_status") or "")
    checks = data.get("checks") or {}
    preview: list[dict[str, Any]] = []
    for name, block in sorted(checks.items()):
        if isinstance(block, dict):
            st = str(block.get("status") or "")
            if st in ("fail", "warn"):
                preview.append({"check": name, "status": st})
    ui = "unknown"
    if os == "invalid":
        ui = "invalid"
    elif os in ("degraded", "warning"):
        ui = "caution"
    elif os == "valid":
        ui = "ok"
    return {
        "empty": False,
        "present": True,
        "overall_status": os,
        "run_id": data.get("run_id"),
        "evaluated_at_utc": data.get("evaluated_at_utc"),
        "summary": data.get("summary"),
        "checks_preview": preview[:16],
        "ui_severity": ui,
    }


def overview_from_narrative(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True}
    sec = data.get("sections") or {}
    return {
        "empty": False,
        "overall_trajectory": sec.get("overall_trajectory"),
        "evaluated_at_utc": data.get("evaluated_at_utc"),
        "run_id": data.get("run_id"),
        "narrative_preview": (data.get("narrative_text") or "")[:800],
    }


def queue_view(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True, "entries": [], "generated_at": None}
    entries = []
    for e in (data.get("entries") or [])[:50]:
        if not isinstance(e, dict):
            continue
        entries.append(
            {
                "product_id": e.get("product_id"),
                "queue_rank": e.get("queue_rank"),
                "readiness_tier": e.get("readiness_tier"),
                "priority_score": e.get("priority_score"),
                "orchestration_status": e.get("orchestration_status"),
            }
        )
    return {
        "empty": False,
        "generated_at_utc": data.get("generated_at_utc"),
        "run_id": data.get("run_id"),
        "entries": entries,
        "entry_count": len(data.get("entries") or []),
    }


def lifecycle_view(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True}
    summ = data.get("portfolio_lifecycle_summary")
    narrative = None
    if isinstance(summ, dict):
        narrative = summ.get("narrative")
    return {
        "empty": False,
        "lifecycle_counts": data.get("lifecycle_counts") or {},
        "products_entering": data.get("products_entering") or [],
        "products_exiting": data.get("products_exiting") or [],
        "repair_pressure": data.get("products_under_repair_pressure") or [],
        "retirement_pressure": data.get("products_under_retirement_pressure") or [],
        "strategy_posture": data.get("portfolio_strategy_posture"),
        "summary_narrative": narrative,
        "evaluated_at_utc": data.get("evaluated_at_utc"),
        "run_id": data.get("run_id"),
    }


def learning_view(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True}
    return {
        "empty": False,
        "top_lessons": (data.get("top_lessons_so_far") or [])[:16],
        "sparse_warnings": data.get("sparse_signal_warnings") or [],
        "evaluated_at_utc": data.get("evaluated_at_utc"),
        "run_id": data.get("run_id"),
    }


def strategy_view(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True}
    return {
        "empty": False,
        "strategic_posture": data.get("strategic_posture"),
        "rationale": data.get("rationale"),
        "evaluated_at_utc": data.get("evaluated_at_utc"),
        "run_id": data.get("run_id"),
    }


def intervention_inbox_view(data: dict[str, Any] | None) -> dict[str, Any]:
    if not data:
        return {"empty": True}
    items = [x for x in (data.get("open_items") or []) if isinstance(x, dict)]
    open_n = len([x for x in items if x.get("in_active_queue")])
    high = sum(1 for x in items if x.get("in_active_queue") and str(x.get("severity")) == "high")
    return {
        "empty": False,
        "open_items_count": len(items),
        "active_in_queue": open_n,
        "high_severity_active": high,
        "items_preview": items[:24],
        "built_at_utc": data.get("built_at_utc"),
        "source_intervention_run_id": data.get("source_intervention_run_id"),
    }


def _escalation_row_table(row: dict[str, Any]) -> dict[str, Any]:
    ev = str(row.get("evidence_summary") or "")
    ra = str(row.get("requested_action") or "")
    return {
        "item_id": row.get("item_id"),
        "category": str(row.get("category") or ""),
        "severity": str(row.get("severity") or ""),
        "product_id": row.get("product_id") or "—",
        "source": str(row.get("source") or ""),
        "ack_state": str(row.get("ack_state") or ""),
        "evidence_summary": ev[:400] + ("…" if len(ev) > 400 else ""),
        "requested_action": ra[:300] + ("…" if len(ra) > 300 else ""),
    }


def _count_keys(items: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in items:
        k = str(row.get(key) or "") or "—"
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items(), key=lambda x: (-x[1], x[0])))


def needs_you_view(data: dict[str, Any] | None) -> dict[str, Any]:
    """
    View-model for ``runs/portfolio/escalation_inbox/latest.json`` — autonomy boundary / operator action.

    Default **actionable** rows: ``in_active_queue`` and ``requires_operator_action``.
    """
    if data is None:
        return {"empty": True, "schema_ok": False, "actionable_raw": [], "actionable_guidance": []}
    if str(data.get("schema") or "") != ESCALATION_INBOX_SCHEMA:
        return {
            "empty": True,
            "schema_ok": False,
            "note": "file exists but schema is not argus.escalation_inbox.v1",
            "actionable_raw": [],
            "actionable_guidance": [],
        }

    raw_items = [x for x in (data.get("open_items") or []) if isinstance(x, dict)]
    actionable: list[dict[str, Any]] = []
    informational: list[dict[str, Any]] = []
    settled: list[dict[str, Any]] = []

    for row in raw_items:
        ack = str(row.get("ack_state") or "")
        if ack in (ACK_STATE_RESOLVED, ACK_STATE_SNOOZED):
            settled.append(row)
            continue
        if row.get("in_active_queue") and row.get("requires_operator_action"):
            actionable.append(row)
            continue
        if str(row.get("category") or "") == CATEGORY_INFORMATIONAL:
            informational.append(row)
            continue
        if not row.get("in_active_queue"):
            settled.append(row)
            continue
        # In active queue but not flagged as requiring action (edge case)
        informational.append(row)

    def _rows(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [_escalation_row_table(r) for r in xs[:64]]

    ac_ids = {str(x.get("item_id")) for x in actionable}
    info_only = [x for x in informational if str(x.get("item_id")) not in ac_ids]

    actionable_raw = actionable[:48]
    actionable_guidance = [enrich_escalation_item(r) for r in actionable_raw]

    cat_all = _count_keys(raw_items, "category")
    sev_all = _count_keys(raw_items, "severity")
    cat_act = _count_keys(actionable, "category")
    sev_act = _count_keys(actionable, "severity")

    return {
        "empty": False,
        "schema_ok": True,
        "built_at_utc": data.get("built_at_utc"),
        "inbox_artifact_run_id": data.get("inbox_artifact_run_id"),
        "total_items": len(raw_items),
        "actionable_count": len(actionable),
        "informational_count": len(info_only),
        "settled_count": len(settled),
        "category_counts_all": cat_all,
        "severity_counts_all": sev_all,
        "category_counts_actionable": cat_act,
        "severity_counts_actionable": sev_act,
        "actionable_rows": _rows(actionable),
        "actionable_raw": actionable_raw,
        "actionable_guidance": actionable_guidance,
        "informational_rows": _rows(info_only),
        "settled_rows": _rows(settled),
        "no_actionable_items": len(actionable) == 0,
    }


def _parse_iso_dt(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def runner_service_view(
    data: dict[str, Any] | None,
    *,
    artifact_mtime_utc: str | None = None,
    reference_now: datetime | None = None,
) -> dict[str, Any]:
    """
    View-model for ``runs/portfolio/runner_service/latest.json`` (cadence wrapper heartbeat).

    ``artifact_mtime_utc`` is the loaded file mtime (UTC ISO) from :class:`LoadedArtifact` — used if
    payload timestamps are missing. ``reference_now`` is for tests (staleness math).
    """
    if data is None:
        return {"empty": True, "schema_ok": False}
    if str(data.get("schema") or "") != PORTFOLIO_RUNNER_SERVICE_SCHEMA:
        return {
            "empty": True,
            "schema_ok": False,
            "note": "file exists but schema is not argus.portfolio_runner_service.v1",
        }

    ref_now = reference_now or datetime.now(timezone.utc)
    cs = str(data.get("current_status") or "").strip()
    nxt_raw = data.get("next_planned_run_at_utc")
    nxt = str(nxt_raw).strip() if nxt_raw is not None else ""
    inp = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
    raw_iv = inp.get("interval_seconds")
    try:
        interval_f = float(raw_iv) if raw_iv is not None else 0.0
    except (TypeError, ValueError):
        interval_f = 0.0

    heartbeat_ref = (
        _parse_iso_dt(data.get("updated_at_utc"))
        or _parse_iso_dt(data.get("service_finished_at_utc"))
        or _parse_iso_dt(data.get("last_run_finished_at_utc"))
        or _parse_iso_dt(data.get("service_started_at_utc"))
        or _parse_iso_dt(artifact_mtime_utc)
    )
    age_seconds: float | None = None
    if heartbeat_ref is not None:
        age_seconds = max(0.0, (ref_now - heartbeat_ref).total_seconds())

    stale_suspected = False
    stale_note: str | None = None
    if cs in ("starting", "running", "sleeping") and age_seconds is not None:
        if age_seconds > RUNNER_SERVICE_STALE_ACTIVE_SECONDS:
            stale_suspected = True
            stale_note = (
                f"Heartbeat age ~{int(age_seconds)}s with status `{cs}` — "
                "the runner service process may have crashed or been killed."
            )

    has_embedded = isinstance(data.get("last_autonomous_session"), dict)
    no_next_scheduled = not nxt

    sr = str(data.get("stop_reason") or "").strip()
    badges: list[str] = []
    if cs:
        badges.append(cs)
    if sr and cs == "stopped":
        badges.append(f"stop:{sr}")
    if stale_suspected:
        badges.append("heartbeat_stale")

    result: dict[str, Any] = {
        "empty": False,
        "schema_ok": True,
        "service_run_id": data.get("service_run_id"),
        "current_status": cs or None,
        "last_run_started_at_utc": data.get("last_run_started_at_utc"),
        "last_run_finished_at_utc": data.get("last_run_finished_at_utc"),
        "last_session_id": data.get("last_session_id"),
        "last_autonomous_stop_reason": data.get("last_autonomous_stop_reason"),
        "next_planned_run_at_utc": nxt or None,
        "no_next_run_scheduled": no_next_scheduled,
        "loop_count": data.get("loop_count"),
        "stop_reason": sr,
        "stop_reason_codes": [str(c) for c in (data.get("stop_reason_codes") or [])],
        "last_run_summary": str(data.get("last_run_summary") or ""),
        "has_embedded_autonomous_session": has_embedded,
        "service_started_at_utc": data.get("service_started_at_utc"),
        "service_finished_at_utc": data.get("service_finished_at_utc"),
        "updated_at_utc": data.get("updated_at_utc"),
        "inputs_interval_seconds": interval_f,
        "heartbeat_age_seconds": age_seconds,
        "stale_suspected": stale_suspected,
        "stale_note": stale_note,
        "status_badges": badges,
    }
    ac = data.get("artifact_coherence")
    if isinstance(ac, dict):
        if ac.get("present") is False:
            result["artifact_coherence"] = {"present": False, "source": "runner_service_payload"}
        elif ac.get("present"):
            summ = str(ac.get("summary") or "")
            result["artifact_coherence"] = {
                "present": True,
                "overall_status": ac.get("overall_status"),
                "run_id": ac.get("run_id"),
                "evaluated_at_utc": ac.get("evaluated_at_utc"),
                "summary_preview": summ[:500] + ("…" if len(summ) > 500 else ""),
                "source": "runner_service_payload",
            }
    return result


def autonomous_session_primary_status(stop_reason: str | None) -> str:
    """
    Outcome-oriented label for the session (demo-friendly).
    ``dry_run`` is surfaced separately via ``autonomous_session_status_badges``.
    """
    sr = str(stop_reason or "").strip()
    if sr == "explicit_stop_sentinel":
        return "stopped_manual"
    if sr == "quiescence_recommendation":
        return "stopped_quiescent"
    if sr == "intervention_heavy_streak":
        return "stopped_intervention_heavy"
    if sr == "max_cycles_reached":
        return "completed"
    if sr == "empty_portfolio":
        return "stopped_empty_portfolio"
    if sr in (
        "portfolio_refresh_failed",
        "portfolio_cycle_failed",
        "portfolio_lifecycle_failed",
        "operator_summary_failed",
        "operator_narrative_failed",
    ):
        return "stopped_pipeline_error"
    if sr == "cycle_overall_recommendation":
        return "stopped_cycle_guardrail"
    if sr == "no_material_change_streak":
        return "completed_quiet"
    if sr == "unknown" or not sr:
        return "unknown"
    return "completed"


def autonomous_session_status_badges(data: dict[str, Any] | None) -> list[str]:
    """Display labels (order: mode flags first, then outcome)."""
    if not data or data.get("schema") != PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA:
        return []
    inp = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
    dry = bool(inp.get("dry_run"))
    allow = bool(inp.get("allow_promotion"))
    boot = bool(inp.get("promotion_include_bootstrap"))
    sr = str(data.get("stop_reason") or "")
    badges: list[str] = []
    if dry:
        badges.append("dry_run")
    if allow:
        badges.append("allow_promotion")
    if boot:
        badges.append("promotion_bootstrap")
    primary = autonomous_session_primary_status(sr)
    if primary not in badges:
        badges.append(primary)
    return badges


def _rows_promotable_actions(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for a in data.get("promotable_actions") or []:
        if not isinstance(a, dict):
            continue
        kind = str(a.get("kind") or "")
        row = {
            "kind": kind,
            "safe_for_auto": a.get("safe_for_auto"),
            "proposal_id": a.get("proposal_id"),
            "product_id": a.get("product_id") or a.get("derived_product_id"),
            "detail": "",
        }
        if kind == "creation_proposal_to_scaffold":
            row["detail"] = f"proposal `{a.get('proposal_id')}` → `{a.get('derived_product_id')}`"
        elif kind == "scaffolded_product_to_bootstrap":
            row["detail"] = f"bootstrap `{a.get('product_id')}`"
        elif kind == "deprecation_proposal_to_plan":
            row["detail"] = f"proposal `{a.get('proposal_id')}` · product `{a.get('product_id')}`"
        else:
            row["detail"] = str(a)[:200]
        rows.append(row)
    return rows[:48]


def _rows_blocked_promotions(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for b in data.get("blocked_promotions") or []:
        if not isinstance(b, dict):
            continue
        rows.append(
            {
                "kind": str(b.get("kind") or ""),
                "proposal_id": b.get("proposal_id"),
                "product_id": b.get("product_id"),
                "reason": str(b.get("reason") or "")[:500],
            }
        )
    return rows[:48]


def _truncate_cell(text: str, max_len: int = AUTONOMOUS_PER_CYCLE_CELL_MAX_LEN) -> str:
    t = str(text or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max(0, max_len - 1)] + "…"


def _actions_taken_from_per_cycle_row(row: dict[str, Any]) -> str:
    """Short pipeline label for one ``per_cycle_outcomes`` entry."""
    pr = row.get("portfolio_refresh") if isinstance(row.get("portfolio_refresh"), dict) else {}
    stages: list[str] = []
    if pr.get("ok"):
        stages.append("refresh")
    else:
        stages.append("refresh✗")
    pc = row.get("portfolio_cycle")
    if pc is None:
        return " → ".join(stages)
    stages.append("cycle")
    pl = row.get("portfolio_lifecycle")
    if isinstance(pl, dict) and pl.get("error") and "schema" not in pl:
        stages.append("lifecycle✗")
    elif pl is not None:
        stages.append("lifecycle")
    osum = row.get("operator_summary")
    if isinstance(osum, dict) and osum.get("error") and "headline_status" not in osum:
        stages.append("summary✗")
    elif osum is not None:
        stages.append("summary")
    onar = row.get("operator_narrative")
    if isinstance(onar, dict) and onar.get("error") and "sections" not in onar:
        stages.append("narrative✗")
    elif onar is not None:
        stages.append("narrative")
    return " → ".join(stages)


def _outcome_from_per_cycle_row(row: dict[str, Any]) -> str:
    pc = row.get("portfolio_cycle")
    if not isinstance(pc, dict):
        pr = row.get("portfolio_refresh") if isinstance(row.get("portfolio_refresh"), dict) else {}
        if not pr.get("ok"):
            return "refresh_failed"
        return "incomplete"
    parts: list[str] = []
    ov = str(pc.get("overall_operator_recommendation") or "").strip()
    if ov:
        parts.append(ov)
    qr = str(pc.get("quiescence_recommendation") or "").strip()
    if qr:
        parts.append(f"quiescence:{qr}")
    if parts:
        return _truncate_cell(" · ".join(parts), 200)
    if pc.get("ok") is False:
        return "cycle_not_ok"
    return "ok"


def _notes_from_per_cycle_row(
    row: dict[str, Any],
    *,
    is_last: bool,
    session_stop_reason: str,
) -> str:
    bits: list[str] = []
    pc = row.get("portfolio_cycle") if isinstance(row.get("portfolio_cycle"), dict) else {}
    if pc:
        n = pc.get("products_with_material_change_count")
        if n is not None:
            bits.append(f"materialΔ={n}")
        fc = pc.get("intervention_flagged_count")
        if fc is not None:
            bits.append(f"flagged={fc}")
    osum = row.get("operator_summary")
    if isinstance(osum, dict) and osum.get("headline_status"):
        bits.append(f"headline={osum['headline_status']}")
    if is_last and session_stop_reason:
        bits.insert(0, f"stop={session_stop_reason}")
    out = "; ".join(bits) if bits else "—"
    return _truncate_cell(out, 200)


def _promotions_executed_summary_for_table(
    promotion_execution: dict[str, Any] | None,
    *,
    for_last_cycle: bool,
) -> str:
    if not for_last_cycle:
        return "—"
    if not isinstance(promotion_execution, dict):
        return "—"
    sk = promotion_execution.get("skipped_reason")
    if sk:
        return _truncate_cell(f"skipped: {sk}", AUTONOMOUS_PER_CYCLE_CELL_MAX_LEN)
    steps = [s for s in (promotion_execution.get("steps") or []) if isinstance(s, dict)]
    if not steps:
        return "—"
    kinds = [str(s.get("kind") or "?") for s in steps]
    ok_n = sum(1 for s in steps if str(s.get("result_status") or "") == "success")
    head = f"{len(steps)} step(s), {ok_n} ok"
    tail = ", ".join(kinds[:5])
    return _truncate_cell(f"{head}: {tail}", AUTONOMOUS_PER_CYCLE_CELL_MAX_LEN)


def build_autonomous_per_cycle_table_rows(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Normalize ``per_cycle_outcomes`` for the operator dashboard table.

    Expects a dict with optional ``per_cycle_outcomes`` (list). Does not require schema
    validation — callers pass only autonomous-runner payloads.
    """
    raw = payload.get("per_cycle_outcomes")
    if raw is None:
        return {"rows": [], "total": 0, "truncated": False, "empty": True, "schema_mismatch_items": 0}
    if not isinstance(raw, list):
        return {"rows": [], "total": 0, "truncated": False, "empty": True, "schema_mismatch_items": 1}

    session_stop = str(payload.get("stop_reason") or "").strip()
    pex = payload.get("promotion_execution") if isinstance(payload.get("promotion_execution"), dict) else None

    norm: list[dict[str, Any]] = []
    mismatch = 0
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            mismatch += 1
            continue
        idx = item.get("cycle_index")
        try:
            cyc = int(idx) if idx is not None else i + 1
        except (TypeError, ValueError):
            cyc = i + 1
        is_last = i == len(raw) - 1
        norm.append(
            {
                "cycle_index": cyc,
                "outcome": _outcome_from_per_cycle_row(item),
                "actions_taken": _truncate_cell(_actions_taken_from_per_cycle_row(item)),
                "promotions_executed": _promotions_executed_summary_for_table(pex, for_last_cycle=is_last),
                "stop_reason": session_stop if is_last else "",
                "notes": _notes_from_per_cycle_row(
                    item, is_last=is_last, session_stop_reason=session_stop
                ),
            }
        )

    total = len(norm)
    truncated = total > AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS
    display = norm[-AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS :] if truncated else norm

    # DataFrame-friendly column names (Streamlit)
    rows_out: list[dict[str, Any]] = []
    for r in display:
        note = r["notes"]
        sr = str(r.get("stop_reason") or "").strip()
        if sr and sr not in note:
            note = _truncate_cell(f"{note} · {sr}" if note != "—" else f"stop={sr}", 200)
        rows_out.append(
            {
                "Cycle": r["cycle_index"],
                "Outcome": r["outcome"],
                "Actions": r["actions_taken"],
                "Promotions": r["promotions_executed"],
                "Notes": note,
            }
        )

    return {
        "rows": rows_out,
        "total": total,
        "truncated": truncated,
        "empty": total == 0,
        "schema_mismatch_items": mismatch,
    }


def _rows_promotion_execution(data: dict[str, Any]) -> list[dict[str, Any]]:
    pex = data.get("promotion_execution")
    if not isinstance(pex, dict):
        return []
    out: list[dict[str, Any]] = []
    for step in pex.get("steps") or []:
        if not isinstance(step, dict):
            continue
        prom = step.get("promotion")
        nested_ok = None
        if isinstance(prom, dict):
            nr = prom.get("nested_results") or {}
            if isinstance(nr, dict):
                for _k, v in nr.items():
                    if isinstance(v, dict) and "ok" in v:
                        nested_ok = v.get("ok")
                        break
        out.append(
            {
                "kind": str(step.get("kind") or ""),
                "result_status": str(step.get("result_status") or ""),
                "detail": str(step.get("detail") or "")[:300],
                "promotion_ok": (prom or {}).get("result_status") if isinstance(prom, dict) else None,
                "nested_ok": nested_ok,
            }
        )
    return out[:48]


def autonomous_session_view(data: dict[str, Any] | None) -> dict[str, Any]:
    """
    View-model for ``runs/portfolio/autonomous_runner/latest.json`` (read-only console).
    """
    if data is None:
        return {"empty": True, "schema_ok": False}
    if str(data.get("schema") or "") != PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA:
        return {
            "empty": True,
            "schema_ok": False,
            "note": "file exists but schema is not argus.portfolio_autonomous_runner.v1",
        }
    inp = data.get("inputs") if isinstance(data.get("inputs"), dict) else {}
    dry = bool(inp.get("dry_run"))
    sr = str(data.get("stop_reason") or "")
    lsi = data.get("lifecycle_session_influence") if isinstance(data.get("lifecycle_session_influence"), dict) else {}
    pex = data.get("promotion_execution") if isinstance(data.get("promotion_execution"), dict) else {}
    promo_recs = [str(x) for x in (data.get("promotion_recommendations") or []) if x][:12]

    return {
        "empty": False,
        "schema_ok": True,
        "session_id": data.get("session_id"),
        "started_at_utc": data.get("started_at_utc"),
        "finished_at_utc": data.get("finished_at_utc"),
        "cycles_run": data.get("cycles_run"),
        "stop_reason": sr,
        "stop_reason_codes": [str(c) for c in (data.get("stop_reason_codes") or [])],
        "session_summary": str(data.get("session_summary") or ""),
        "lifecycle_primary_signal": str(lsi.get("primary_signal") or "") or None,
        "lifecycle_session_notes": [str(x) for x in (lsi.get("session_notes") or []) if x][:16],
        "lifecycle_priority_hints": [str(x) for x in (lsi.get("priority_hints") or []) if x][:16],
        "stop_continue": lsi.get("stop_continue_context") if isinstance(lsi.get("stop_continue_context"), dict) else None,
        "promotion_recommendations": promo_recs,
        "promotable_actions_rows": _rows_promotable_actions(data),
        "blocked_promotions_rows": _rows_blocked_promotions(data),
        "promotion_execution_rows": _rows_promotion_execution(data),
        "promotion_execution_skipped_reason": pex.get("skipped_reason"),
        "promotion_effective_dry_run": pex.get("effective_dry_run"),
        "artifacts_refreshed": [str(x) for x in (data.get("artifacts_refreshed") or []) if x],
        "inputs": {
            "dry_run": dry,
            "allow_promotion": bool(inp.get("allow_promotion")),
            "promotion_include_bootstrap": bool(inp.get("promotion_include_bootstrap")),
            "max_cycles": inp.get("max_cycles"),
        },
        "status_badges": autonomous_session_status_badges(data),
        "primary_status": autonomous_session_primary_status(sr),
        "per_cycle_count": len(data.get("per_cycle_outcomes") or []),
        **{f"per_cycle_table_{k}": v for k, v in build_autonomous_per_cycle_table_rows(data).items()},
    }


__all__ = [
    "AUTONOMOUS_PER_CYCLE_CELL_MAX_LEN",
    "AUTONOMOUS_PER_CYCLE_TABLE_MAX_ROWS",
    "OPERATOR_CONSOLE_SNAPSHOT_SCHEMA",
    "PORTFOLIO_RUNNER_SERVICE_SCHEMA",
    "RUNNER_SERVICE_STALE_ACTIVE_SECONDS",
    "PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA",
    "LoadedArtifact",
    "build_operator_console_snapshot",
    "autonomous_session_primary_status",
    "autonomous_session_status_badges",
    "autonomous_session_view",
    "build_autonomous_per_cycle_table_rows",
    "console_artifact_specs",
    "intervention_inbox_view",
    "learning_view",
    "lifecycle_view",
    "load_json_artifact",
    "load_operator_console_bundle",
    "needs_you_view",
    "runner_service_view",
    "overview_from_narrative",
    "overview_from_summary",
    "world_context_view",
    "queue_view",
    "strategy_view",
    "artifact_coherence_view",
]
