"""Aggregate local Argus artifacts into a single JSON payload for the dashboard."""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.product import ProductNode
from argus.core.serialize import to_jsonable
from argus.dashboard.actions_panel import build_actions_panel
from argus.dashboard.diagnostics import DashboardDiagnostics, empty_integrity
from argus.dashboard.ideas_block import build_ideas_dashboard_block
from argus.dashboard.operator_visibility import collect_operator_alerts
from argus.dashboard.strategy_planning_read import (
    summarize_orchestration_planning_explainability,
    summarize_planning_latest_for_dashboard,
    summarize_strategy_latest_for_dashboard,
)
from argus.decision.persistence import (
    latest_portfolio_path,
    latest_product_path,
    load_latest_portfolio,
    load_latest_product_decisions,
)
from argus.escalation.packet import list_packets
from argus.findings.persistence import load_latest_findings
from argus.findings.rules.temporal import TEMPORAL_FINDING_KINDS
from argus.history.storage import iter_snapshot_dirs, load_snapshot_file
from argus.orchestrator.artifact_paths import (
    portfolio_priorities_path,
    portfolio_priority_trends_path,
)
from argus.orchestrator.portfolio_priorities import read_portfolio_priorities_json
from argus.orchestrator.portfolio_priority_trends import read_portfolio_priority_trends_json
from argus.products.inventory import build_inventory
from argus.refinement.dashboard_block import build_refinement_dashboard_block
from argus.signals.persistence import load_latest_bundle
from argus.temporal.visibility import (
    build_temporal_dashboard_block,
    collect_recent_temporal_findings,
)

logger = logging.getLogger("argus.dashboard")


def _last_signal_at(bundle: Any) -> str | None:
    if bundle is None:
        return None
    ts = bundle.collected_at_utc
    if not ts and bundle.records:
        obs = [getattr(r, "observed_at", None) for r in bundle.records]
        obs = [o for o in obs if o is not None]
        if obs:
            latest = max(obs)
            if hasattr(latest, "isoformat"):
                return latest.isoformat()
    return ts or None


def _chronological_snapshots(
    repo_root: Path,
    diag: DashboardDiagnostics,
    *,
    strict: bool,
) -> tuple[list[Any], dict[str, int]]:
    """Oldest-first portfolio snapshots from ``runs/history/snapshots/``."""
    dirs = list(reversed(iter_snapshot_dirs(repo_root.resolve())))
    out: list[Any] = []
    skipped = 0
    for d in dirs:
        p = d / "snapshot.json"
        try:
            out.append(load_snapshot_file(p))
        except (OSError, ValueError, json.JSONDecodeError) as e:
            skipped += 1
            diag.json_failure(p, e, strict=strict, label="history_snapshot_invalid")
            continue
    return out, {
        "attempted": len(dirs),
        "loaded": len(out),
        "skipped_invalid": skipped,
    }


def _trend_summary_for_product(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    """Deterministic trend/drift summary; ``None`` if fewer than 2 history points."""
    from argus.trends.analyze import analyze_product

    t = analyze_product(repo_root, product_id)
    if t.window_size < 2:
        return None
    return {
        "trend_flags": list(t.trend_flags),
        "drift_signals": list(t.drift_signals[:16]),
        "confidence": t.confidence,
        "summary": t.summary,
        "recommended_interpretation": t.recommended_interpretation,
        "window_size": t.window_size,
    }


def _enrich_history(
    repo_root: Path,
    products: list[dict[str, Any]],
    diag: DashboardDiagnostics,
    *,
    strict: bool,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    """
    Attach per-product ``history_points`` and build ``snapshots_catalog`` (oldest → newest).

    Returns ``(catalog, artifact_links)``.
    """
    root = repo_root.resolve()
    snaps, hist_stats = _chronological_snapshots(root, diag, strict=strict)
    catalog: list[dict[str, Any]] = []
    for s in snaps:
        catalog.append(
            {
                "snapshot_id": s.snapshot_id,
                "observed_at_utc": s.observed_at_utc,
                "path_repo": f"runs/history/snapshots/{s.snapshot_id}/snapshot.json",
                "path_from_dashboard": f"../history/snapshots/{s.snapshot_id}/snapshot.json",
            }
        )

    pid_to_points: dict[str, list[dict[str, Any]]] = {p["product_id"]: [] for p in products}
    for s in snaps:
        for ps in s.products:
            pid = ps.product_id
            if pid not in pid_to_points:
                continue
            pid_to_points[pid].append(
                {
                    "snapshot_id": s.snapshot_id,
                    "observed_at_utc": s.observed_at_utc,
                    "active_findings_count": ps.active_findings_count,
                    "monthly_cost_usd": ps.monthly_cost_usd,
                    "priority_score": ps.priority_score,
                    "top_recommended_action": (ps.top_recommended_action or "")[:240],
                    "lifecycle_stage": ps.lifecycle_stage,
                    "escalation_count": ps.escalation_count,
                    "kill_candidate": ps.kill_candidate,
                }
            )

    for p in products:
        pid = p["product_id"]
        p["history_points"] = pid_to_points.get(pid, [])
        p["trend_summary"] = _trend_summary_for_product(root, pid)

    has_hist = (root / "runs" / "history" / "latest.json").is_file()
    has_tr = (root / "runs" / "trends" / "latest.json").is_file()
    has_econ = (root / "runs" / "economics" / "resources_latest.json").is_file()
    artifact_links: dict[str, Any] = {
        "history_latest_repo": "runs/history/latest.json" if has_hist else None,
        "history_latest_from_dashboard": "../history/latest.json" if has_hist else None,
        "trends_latest_repo": "runs/trends/latest.json" if has_tr else None,
        "trends_latest_from_dashboard": "../trends/latest.json" if has_tr else None,
        "portfolio_decisions_repo": "runs/decisions/latest/portfolio.json",
        "portfolio_decisions_from_dashboard": "../decisions/latest/portfolio.json",
        "decisions_generations_repo": "runs/decisions/generations/",
        "decisions_generations_from_dashboard": "../decisions/generations/",
        "economics_resources_repo": "runs/economics/resources_latest.json" if has_econ else None,
        "economics_resources_from_dashboard": "../economics/resources_latest.json" if has_econ else None,
    }
    return catalog, artifact_links, hist_stats


def _autonomy_safety_block(
    repo_root: Path,
    diag: DashboardDiagnostics,
    *,
    strict: bool,
) -> dict[str, Any]:
    """Autonomy mode, last autonomy run, blocked actions, capability requests, execution stats."""
    root = repo_root.resolve()
    base: dict[str, Any] = {
        "schema": "argus.dashboard_autonomy_safety.v2",
        "autonomy_mode": None,
        "autonomy_tier": None,
        "last_autonomy_run": None,
        "blocked_actions": [],
        "pending_capability_requests": 0,
        "pending_approvals": 0,
        "execution": {"success": 0, "failed": 0, "other": 0, "total": 0},
        "guardrail_summary": {},
        "guardrail_limits": {},
        "escalation_packets_recent": [],
        "escalation_dedupe": {},
    }

    try:
        from argus.autonomy.operator_policy import effective_policy

        _mode, pol, tier = effective_policy(root)
        base["autonomy_tier"] = int(tier)
        base["guardrail_limits"] = pol.to_jsonable()
    except (OSError, ValueError, TypeError) as e:
        diag.warn("autonomy_tier_unavailable", str(e))

    ap = root / "runs" / "autonomy" / "autonomy.json"
    if ap.is_file():
        try:
            raw = json.loads(ap.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                base["autonomy_mode"] = raw.get("mode")
                if raw.get("tier") is not None:
                    base["autonomy_tier_configured"] = raw.get("tier")
            else:
                diag.warn("autonomy_config_shape", "autonomy.json is not an object", path=ap)
        except (OSError, json.JSONDecodeError) as e:
            diag.json_failure(ap, e, strict=strict, label="autonomy_config_invalid")

    auto_base = root / "runs" / "autonomy"
    best_dir: Path | None = None
    best_ts = ""
    if auto_base.is_dir():
        for d in auto_base.iterdir():
            if not d.is_dir() or d.name.startswith("."):
                continue
            mf = d / "manifest.json"
            if not mf.is_file():
                continue
            try:
                m = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                diag.json_failure(mf, e, strict=strict, label="autonomy_manifest_invalid")
                continue
            if not isinstance(m, dict):
                continue
            st = str(m.get("started_at_utc") or "")
            if st >= best_ts:
                best_ts = st
                best_dir = d
    if best_dir is not None:
        mf = best_dir / "manifest.json"
        try:
            m = json.loads(mf.read_text(encoding="utf-8"))
            if isinstance(m, dict):
                base["last_autonomy_run"] = {
                    "run_id": m.get("run_id"),
                    "started_at_utc": m.get("started_at_utc"),
                    "finished_at_utc": m.get("finished_at_utc"),
                    "ok": m.get("ok"),
                    "path_repo": f"runs/autonomy/{best_dir.name}",
                }
            else:
                diag.warn(
                    "autonomy_manifest_shape",
                    "manifest.json is not an object",
                    path=mf,
                )
        except (OSError, json.JSONDecodeError) as e:
            diag.json_failure(mf, e, strict=strict, label="autonomy_manifest_invalid")

    blk = root / "runs" / "autonomy" / "blocked_actions.json"
    if blk.is_file():
        try:
            raw = json.loads(blk.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                items = raw.get("items")
                if isinstance(items, list):
                    base["blocked_actions"] = items[:50]
            else:
                diag.warn("blocked_actions_shape", "blocked_actions.json is not an object", path=blk)
        except (OSError, json.JSONDecodeError) as e:
            diag.json_failure(blk, e, strict=strict, label="blocked_actions_invalid")
    else:
        try:
            from argus.capabilities.resume import sync_blocked_actions_artifact

            sync_blocked_actions_artifact(root)
            if blk.is_file():
                raw = json.loads(blk.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and isinstance(raw.get("items"), list):
                    base["blocked_actions"] = raw["items"][:50]
        except (OSError, json.JSONDecodeError, ValueError, TypeError) as e:
            diag.warn("blocked_actions_sync_failed", str(e))

    try:
        from argus.capabilities.requests.models import CapabilityRequestStatus
        from argus.capabilities.requests.store import list_requests

        base["pending_capability_requests"] = sum(
            1 for r in list_requests(root) if r.status == CapabilityRequestStatus.OPEN
        )
    except Exception as e:
        diag.warn("capability_requests_unavailable", f"Could not list capability requests: {e}")
        logger.debug("capability_requests_unavailable", exc_info=True)

    ex_root = root / "runs" / "execution"
    if ex_root.is_dir():
        succ = fail = other = 0
        for d in ex_root.iterdir():
            if not d.is_dir():
                continue
            rp = d / "run.json"
            if not rp.is_file():
                continue
            try:
                raw = json.loads(rp.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as e:
                diag.json_failure(rp, e, strict=strict, label="execution_stats_json_invalid")
                continue
            if not isinstance(raw, dict):
                continue
            st = str(raw.get("status") or "").lower()
            if st == "success":
                succ += 1
            elif st == "failed":
                fail += 1
            else:
                other += 1
        base["execution"] = {
            "success": succ,
            "failed": fail,
            "other": other,
            "total": succ + fail + other,
        }

    try:
        from argus.autonomy.controller import load_state

        st = load_state(root)
        base["guardrail_summary"] = {
            "block_streak": int(float(st.get("block_streak", 0))),
            "actions_executed_today": int(float(st.get("actions_executed_today", 0))),
            "cost_usd_today": float(st.get("cost_usd_today", 0.0)),
            "product_spawns_today": int(float(st.get("product_spawns_today", 0))),
            "experiments_created_today": int(float(st.get("experiments_created_today", 0))),
            "shutdowns_applied_today": int(float(st.get("shutdowns_applied_today", 0))),
            "recent_guardrail_events": (st.get("recent_guardrail_events") or [])[-12:]
            if isinstance(st.get("recent_guardrail_events"), list)
            else [],
        }
    except (OSError, TypeError, ValueError) as e:
        diag.warn("autonomy_state_unavailable", str(e))

    try:
        from argus.approval.models import ApprovalStatus
        from argus.approval.store import list_records

        base["pending_approvals"] = sum(
            1 for r in list_records(root) if r.status == ApprovalStatus.PENDING
        )
    except (OSError, TypeError, ValueError) as e:
        diag.warn("approval_pending_unavailable", str(e))

    try:
        from argus.escalation.dedupe import summarize_escalation_groups
        from argus.escalation.packet import list_packets

        base["escalation_packets_recent"] = list_packets(root, limit=8)
        base["escalation_dedupe"] = summarize_escalation_groups(root)
    except (OSError, TypeError, ValueError) as e:
        diag.warn("escalation_list_unavailable", str(e))

    return base


def _economics_resources_block(
    repo_root: Path,
    diag: DashboardDiagnostics,
) -> dict[str, Any]:
    """
    Compact slice of ``runs/economics/resources_latest.json`` for the static dashboard.

    When the file is missing, ``present`` is false so the UI can prompt for
    ``argus economics resources``.
    """
    root = repo_root.resolve()
    path = root / "runs" / "economics" / "resources_latest.json"
    base: dict[str, Any] = {
        "schema": "argus.dashboard_economics_resources.v1",
        "present": False,
        "path_repo": "runs/economics/resources_latest.json",
        "path_from_dashboard": "../economics/resources_latest.json",
    }
    if not path.is_file():
        diag.warn(
            "economics_resources_missing",
            "No runs/economics/resources_latest.json — run `argus economics resources` to populate.",
            path=str(path),
        )
        return base
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        diag.warn("economics_resources_invalid", str(e), path=path)
        return {**base, "present": False, "error": "invalid_json"}
    if not isinstance(raw, dict):
        diag.warn("economics_resources_shape", "resources_latest.json is not a JSON object", path=path)
        return {**base, "present": False, "error": "not_an_object"}
    orphans = raw.get("orphan_resources") if isinstance(raw.get("orphan_resources"), list) else []
    hclv = raw.get("high_cost_low_value") if isinstance(raw.get("high_cost_low_value"), list) else []
    resources = raw.get("resources") if isinstance(raw.get("resources"), list) else []
    return {
        "schema": "argus.dashboard_economics_resources.v1",
        "present": True,
        "generated_at_utc": raw.get("generated_at_utc"),
        "total_mapped_cost_usd": raw.get("total_mapped_cost_usd"),
        "total_orphan_cost_usd": raw.get("total_orphan_cost_usd"),
        "thresholds": raw.get("thresholds") if isinstance(raw.get("thresholds"), dict) else {},
        "resource_count": len(resources),
        "orphan_count": len(orphans),
        "hclv_count": len(hclv),
        "orphans_preview": orphans[:16],
        "hclv_preview": hclv[:16],
        "path_repo": "runs/economics/resources_latest.json",
        "path_from_dashboard": "../economics/resources_latest.json",
    }


def _last_loop_run_block(repo_root: Path, diag: DashboardDiagnostics) -> dict[str, Any]:
    """Latest ``runs/loop/<run_id>/`` harness summary for first-run / operator visibility."""
    root = repo_root.resolve()
    base = root / "runs" / "loop"
    out: dict[str, Any] = {
        "schema": "argus.dashboard_last_loop_run.v1",
        "present": False,
        "run_id": None,
        "summary_json_repo": None,
        "summary_txt_repo": None,
        "ok": None,
        "stage_ok_count": 0,
        "stage_fail_count": 0,
        "dry_run_execution": True,
    }
    if not base.is_dir():
        diag.warn("loop_runs_missing", "No runs/loop/ yet — run `uv run argus loop full`.")
        return out
    subs = [d for d in base.iterdir() if d.is_dir() and not d.name.startswith(".")]
    if not subs:
        return out
    latest = max(subs, key=lambda p: p.stat().st_mtime)
    rid = latest.name
    sj = latest / "summary.json"
    stxt = latest / "summary.txt"
    out["present"] = True
    out["run_id"] = rid
    out["summary_json_repo"] = f"runs/loop/{rid}/summary.json"
    out["summary_txt_repo"] = f"runs/loop/{rid}/summary.txt" if stxt.is_file() else None
    if not sj.is_file():
        diag.warn("loop_summary_missing", f"runs/loop/{rid}/summary.json missing", path=str(sj))
        return out
    try:
        raw = json.loads(sj.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        diag.warn("loop_summary_invalid", str(e), path=sj)
        return {**out, "present": False}
    if not isinstance(raw, dict):
        return {**out, "present": False}
    out["ok"] = raw.get("ok")
    out["dry_run_execution"] = bool(raw.get("dry_run_execution", True))
    stages = raw.get("stages") if isinstance(raw.get("stages"), list) else []
    ok_n = sum(1 for s in stages if isinstance(s, dict) and s.get("ok"))
    fail_n = sum(1 for s in stages if isinstance(s, dict) and not s.get("ok"))
    out["stage_ok_count"] = ok_n
    out["stage_fail_count"] = fail_n
    out["target_product_ids"] = raw.get("target_product_ids") or []
    return out


DASHBOARD_ORCH_PORTFOLIO_PRIORITIES_SCHEMA = "argus.dashboard_orchestration_portfolio_priorities.v1"


def _orchestration_portfolio_priorities_block(
    repo_root: Path,
    diag: DashboardDiagnostics,
    *,
    strict: bool,
) -> dict[str, Any]:
    """
    Compact read-only view of ``runs/orchestration/latest/portfolio_priorities.json`` for the UI.
    """
    root = repo_root.resolve()
    path = portfolio_priorities_path(root)
    base: dict[str, Any] = {
        "schema": DASHBOARD_ORCH_PORTFOLIO_PRIORITIES_SCHEMA,
        "present": False,
        "artifact_path_repo": "runs/orchestration/latest/portfolio_priorities.json",
        "artifact_path_from_dashboard": "../orchestration/latest/portfolio_priorities.json",
        "generated_at_utc": None,
        "recommended_product_id": None,
        "recommended_next_action": None,
        "rank_1_priority_reasons": None,
        "rank_1_evidence_summary": None,
        "top_products": [],
        "load_error": None,
    }
    raw, err = read_portfolio_priorities_json(root)
    if err is not None:
        base["load_error"] = err
        diag.json_failure(path, ValueError(err), strict=strict, label="portfolio_priorities_invalid")
        return base
    if raw is None:
        return base

    base["present"] = True
    base["generated_at_utc"] = raw.get("generated_at_utc")
    base["recommended_product_id"] = raw.get("recommended_product_id")
    base["recommended_next_action"] = raw.get("recommended_next_action")

    rows_in = raw.get("products") or []
    top: list[dict[str, Any]] = []
    if isinstance(rows_in, list):
        for row in rows_in[:12]:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "").strip()
            if not pid:
                continue
            pr = row.get("priority_reasons")
            reasons = [str(x) for x in pr] if isinstance(pr, list) else []
            fel = row.get("freshness_explanation_lines")
            fel_out = [str(x) for x in fel] if isinstance(fel, list) else []
            top.append(
                {
                    "product_id": pid,
                    "rank": int(row["rank"]) if row.get("rank") is not None else 0,
                    "priority_score": row.get("priority_score"),
                    "orchestration_status": row.get("orchestration_status"),
                    "next_action": row.get("next_action"),
                    "strategy_posture": row.get("strategy_posture"),
                    "planning_mode": row.get("planning_mode"),
                    "priority_reasons": reasons,
                    "evidence_summary": str(row.get("evidence_summary") or ""),
                    "freshness_explanation_lines": fel_out,
                }
            )
    base["top_products"] = top
    r1 = next((r for r in top if r.get("rank") == 1), top[0] if top else None)
    if r1:
        base["rank_1_priority_reasons"] = r1.get("priority_reasons") or []
        base["rank_1_evidence_summary"] = r1.get("evidence_summary") or None
    return base


DASHBOARD_ORCH_PORTFOLIO_TRENDS_SCHEMA = "argus.dashboard_orchestration_portfolio_trends.v1"
DASHBOARD_ORCH_TRENDS_TOP_CAP = 8


def _orchestration_portfolio_priority_trends_block(
    repo_root: Path,
    diag: DashboardDiagnostics,
    *,
    strict: bool,
) -> dict[str, Any]:
    """Read-only block from ``runs/orchestration/latest/portfolio_priority_trends.json``."""
    root = repo_root.resolve()
    path = portfolio_priority_trends_path(root)
    base: dict[str, Any] = {
        "schema": DASHBOARD_ORCH_PORTFOLIO_TRENDS_SCHEMA,
        "present": False,
        "artifact_path_repo": "runs/orchestration/latest/portfolio_priority_trends.json",
        "artifact_path_from_dashboard": "../orchestration/latest/portfolio_priority_trends.json",
        "generated_at_utc": None,
        "window_size": None,
        "generations_considered": None,
        "churn_summary": None,
        "portfolio_stability": None,
        "portfolio_stability_score": None,
        "top_products_to_inspect": [],
        "operator_recommendations": [],
        "top_trending_products": [],
        "load_error": None,
    }
    raw, err = read_portfolio_priority_trends_json(root)
    if err is not None:
        base["load_error"] = err
        diag.json_failure(path, ValueError(err), strict=strict, label="portfolio_priority_trends_invalid")
        return base
    if raw is None:
        return base

    base["present"] = True
    base["generated_at_utc"] = raw.get("generated_at_utc")
    base["window_size"] = raw.get("window_size")
    base["generations_considered"] = raw.get("generations_considered")
    base["churn_summary"] = raw.get("churn_summary")
    base["portfolio_stability"] = raw.get("portfolio_stability")
    base["portfolio_stability_score"] = raw.get("portfolio_stability_score")
    tp = raw.get("top_products_to_inspect")
    base["top_products_to_inspect"] = [str(x) for x in tp] if isinstance(tp, list) else []
    ore = raw.get("operator_recommendations")
    base["operator_recommendations"] = [str(x) for x in ore] if isinstance(ore, list) else []

    rows_in = raw.get("products") or []
    top: list[dict[str, Any]] = []
    if isinstance(rows_in, list):
        for row in rows_in[:DASHBOARD_ORCH_TRENDS_TOP_CAP]:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "").strip()
            if not pid:
                continue
            top.append(
                {
                    "product_id": pid,
                    "latest_rank": row.get("latest_rank"),
                    "average_rank": row.get("average_rank"),
                    "best_rank": row.get("best_rank"),
                    "worst_rank": row.get("worst_rank"),
                    "times_ranked_first": row.get("times_ranked_first"),
                    "rising": bool(row.get("rising")),
                    "falling": bool(row.get("falling")),
                    "stable": bool(row.get("stable")),
                    "trend_summary": str(row.get("trend_summary") or ""),
                    "latest_priority_score": row.get("latest_priority_score"),
                    "average_priority_score": row.get("average_priority_score"),
                }
            )
    base["top_trending_products"] = top
    return base


def _self_improvement_block(repo_root: Path) -> dict[str, Any]:
    from argus.self_improvement.planning import load_latest_plan_json

    root = repo_root.resolve()
    base: dict[str, Any] = {
        "schema": "argus.dashboard_self_improvement.v1",
        "present": False,
        "path_repo": "runs/self_improvement/plan_latest.json",
        "path_from_dashboard": "../self_improvement/plan_latest.json",
    }
    raw = load_latest_plan_json(root)
    if not raw:
        return base
    top = raw.get("top_this_week") or []
    props = {p["id"]: p for p in (raw.get("proposals") or []) if isinstance(p, dict)}
    titles: list[str] = []
    for pid in top[:8]:
        p = props.get(pid)
        if isinstance(p, dict):
            titles.append(str(p.get("title") or pid))
    return {
        **base,
        "present": True,
        "generated_at_utc": raw.get("generated_at_utc"),
        "strategy_mode": raw.get("strategy_mode"),
        "top_this_week_titles": titles,
        "high_risk_count": len(raw.get("high_risk_require_approval") or []),
        "findings_count": len(raw.get("findings") or []),
        "path_md_repo": "runs/self_improvement/plan_latest.md",
        "path_md_from_dashboard": "../self_improvement/plan_latest.md",
    }


def _apply_operator_visibility(products: list[dict[str, Any]]) -> None:
    """Attach ``operator_visibility`` using decision metadata + trends + escalations."""
    from argus.dashboard.operator_visibility import build_operator_visibility

    for p in products:
        p["operator_visibility"] = build_operator_visibility(
            top_confidence=p.get("top_confidence"),
            top_intent=p.get("top_intent"),
            top_metadata=p.get("top_candidate_metadata"),
            portfolio_freshness_warnings=p.get("portfolio_freshness_warnings"),
            findings_by_severity=p.get("findings_by_severity"),
            active_findings_count=int(p.get("active_findings_count") or 0),
            signal_record_count=int(p.get("signal_record_count") or 0),
            temporal_findings_count=int(p.get("temporal_findings_count") or 0),
            kill_candidate=bool(p.get("kill_candidate")),
            escalations=p.get("escalations") or [],
            trend_summary=p.get("trend_summary"),
            temporal_visibility=p.get("temporal_visibility"),
            decision_context=p.get("decision_context"),
        )
        p.pop("top_candidate_metadata", None)
        p.pop("portfolio_freshness_warnings", None)


def _portfolio_rank_by_product(
    repo_root: Path,
    diag: DashboardDiagnostics,
    *,
    strict: bool,
) -> dict[str, dict[str, Any]]:
    """Map product_id -> ranked row from latest portfolio.json if present."""
    root = repo_root.resolve()
    try:
        raw = load_latest_portfolio(root)
    except (OSError, json.JSONDecodeError) as e:
        diag.json_failure(
            latest_portfolio_path(root),
            e,
            strict=strict,
            label="portfolio_json_invalid",
        )
        return {}
    if not raw or not isinstance(raw, dict):
        if raw is not None:
            diag.warn("portfolio_json_shape", "latest portfolio.json is not a JSON object")
        return {}
    ranked = raw.get("ranked") or []
    out: dict[str, dict[str, Any]] = {}
    if isinstance(ranked, list):
        for row in ranked:
            if isinstance(row, dict) and row.get("product_id"):
                out[str(row["product_id"])] = row
    return out


def build_dashboard_payload(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """
    Load inventory + per-product findings, signals, decisions, escalations.

    All paths are repo-relative POSIX strings where applicable.

    ``strict=True`` treats invalid JSON and other artifact issues as errors (surfaced in
    ``diagnostics.errors`` and non-zero CLI exit) while still emitting a payload for the UI.
    """
    root = repo_root.resolve()
    diag = DashboardDiagnostics()
    inv = build_inventory(root, products_dir=products_dir)
    portfolio_by_pid = _portfolio_rank_by_product(root, diag, strict=strict)
    all_escalations = list_packets(root, limit=500)
    esc_by_pid: dict[str, list[dict[str, Any]]] = {}
    for row in all_escalations:
        pid = row.get("product_id")
        if not pid:
            continue
        esc_by_pid.setdefault(str(pid), []).append(row)

    ideas_block = build_ideas_dashboard_block(root, sorted(inv.valid.keys()))

    products: list[dict[str, Any]] = []
    for pid in sorted(inv.valid.keys()):
        rec = inv.valid[pid]
        node = rec.node
        tid = node.type_info.type if node.type_info else ""
        status = node.type_info.status if node.type_info else ""
        state = (
            node.type_info.state
            if node.type_info and node.type_info.state
            else node.lifecycle.stage.value
        )

        monthly = node.cost.monthly_usd
        cap = node.constraints.max_monthly_cost_usd

        fb = load_latest_findings(root, pid)
        findings_count = len(fb.findings) if fb else 0
        sev_counts: Counter[str] = Counter()
        temporal_by_kind: Counter[str] = Counter()
        for f in (fb.findings if fb else []):
            sev_counts[f.severity.value] += 1
            if f.kind in TEMPORAL_FINDING_KINDS:
                temporal_by_kind[f.kind.value] += 1
        temporal_findings_count = sum(temporal_by_kind.values())

        sig_b = load_latest_bundle(root, pid)
        last_sig = _last_signal_at(sig_b)
        sig_n = len(sig_b.records) if sig_b else 0
        signal_records_json: list[dict[str, Any]] = []
        if sig_b:
            for r in sig_b.records[:80]:
                signal_records_json.append(
                    {
                        "id": r.id,
                        "signal_type": r.signal_type.value,
                        "source": r.source,
                        "observed_at": r.observed_at.isoformat()
                        if hasattr(r.observed_at, "isoformat")
                        else str(r.observed_at),
                        "confidence": r.confidence,
                        "tags": list(r.tags),
                        "payload_preview": _preview_payload(r.payload),
                    }
                )

        try:
            dec_raw = load_latest_product_decisions(root, pid)
        except (OSError, json.JSONDecodeError) as e:
            diag.json_failure(
                latest_product_path(root, pid),
                e,
                strict=strict,
                label="decisions_latest_invalid",
            )
            dec_raw = None
        top_summary = ""
        top_intent = ""
        priority_score: float | None = None
        confidence: float | None = None
        kill_candidate = False
        lifecycle_scores: dict[str, float] = {}
        candidates_json: list[dict[str, Any]] = []
        top_candidate_metadata: dict[str, Any] = {}
        decision_context: dict[str, Any] | None = None
        if dec_raw:
            lc = dec_raw.get("lifecycle") or {}
            kill_candidate = bool(lc.get("kill_candidate"))
            scores = lc.get("scores")
            if isinstance(scores, dict):
                lifecycle_scores = {str(k): float(v) for k, v in scores.items() if isinstance(v, (int, float))}
            for c in dec_raw.get("candidates") or []:
                if isinstance(c, dict):
                    md = c.get("metadata") if isinstance(c.get("metadata"), dict) else {}
                    candidates_json.append(
                        {
                            "summary": c.get("summary", ""),
                            "priority_score": c.get("priority_score"),
                            "intent": md.get("intent"),
                            "confidence": c.get("confidence"),
                            "metadata": md,
                        }
                    )
            if candidates_json:
                top_summary = str(candidates_json[0].get("summary") or "")
                top_intent = str(candidates_json[0].get("intent") or "")
                ps = candidates_json[0].get("priority_score")
                priority_score = float(ps) if ps is not None else None
                confidence = (
                    float(candidates_json[0]["confidence"])
                    if candidates_json[0].get("confidence") is not None
                    else None
                )
                top_candidate_metadata = dict(candidates_json[0].get("metadata") or {})
            dc = dec_raw.get("decision_context")
            if isinstance(dc, dict):
                decision_context = dc

        if decision_context is None:
            try:
                from argus.decision_assessment.persistence import load_latest_assessment

                ass = load_latest_assessment(root, pid)
                if ass is not None:
                    decision_context = ass.to_jsonable()
            except OSError:
                pass

        prow = portfolio_by_pid.get(pid)
        if prow is not None:
            if priority_score is None and prow.get("priority_score") is not None:
                priority_score = float(prow["priority_score"])
            if not top_summary and prow.get("summary"):
                top_summary = str(prow["summary"])
            if not top_intent and prow.get("top_intent"):
                top_intent = str(prow["top_intent"])

        portfolio_freshness_warnings: list[str] = []
        if prow is not None and isinstance(prow.get("freshness_warnings"), list):
            portfolio_freshness_warnings = [str(x) for x in prow["freshness_warnings"] if str(x).strip()]

        findings_json = [to_jsonable(f) for f in (fb.findings if fb else [])]

        ip = (ideas_block.get("per_product") or {}).get(pid, {})
        products.append(
            {
                "product_id": pid,
                "name": node.name,
                "type": tid,
                "state": state,
                "status": status,
                "lifecycle_stage": node.lifecycle.stage.value,
                "monthly_cost_usd": monthly,
                "max_monthly_cost_usd": cap,
                "last_signal_at": last_sig,
                "signal_record_count": sig_n,
                "active_findings_count": findings_count,
                "temporal_findings_count": temporal_findings_count,
                "temporal_findings_by_kind": dict(sorted(temporal_by_kind.items())),
                "findings_by_severity": dict(sev_counts),
                "top_recommended_action": top_summary,
                "top_intent": top_intent,
                "priority_score": priority_score,
                "top_confidence": confidence,
                "kill_candidate": kill_candidate,
                "lifecycle_scores": lifecycle_scores,
                "escalations": esc_by_pid.get(pid, []),
                "artifacts": {
                    "product_yaml": f"products/{pid}/product.yaml",
                    "findings_latest": f"runs/findings/latest/{pid}.json",
                    "signals_latest": f"runs/signals/latest/{pid}.json",
                    "decisions_latest": f"runs/decisions/latest/{pid}.json",
                    "decisions_generations": "runs/decisions/generations/",
                },
                "findings": findings_json,
                "signal_records": signal_records_json,
                "candidates": candidates_json,
                "top_candidate_metadata": top_candidate_metadata,
                "portfolio_freshness_warnings": portfolio_freshness_warnings,
                "product_yaml_summary_lines": _yaml_summary_lines(node),
                "decision_context": decision_context,
                "ideas_count": int(ip.get("ideas_count") or 0),
                "ideas_invent_count": int(ip.get("ideas_invent_count") or 0),
                "ideas_high_risk_reward_count": int(ip.get("ideas_high_risk_reward_count") or 0),
                "ideas": ip.get("ideas") or [],
                "strategy_snapshot": summarize_strategy_latest_for_dashboard(root, pid),
                "planning_snapshot": summarize_planning_latest_for_dashboard(root, pid),
                "orchestration_planning_influence": summarize_orchestration_planning_explainability(
                    root, pid
                ),
            }
        )

    temporal_block = build_temporal_dashboard_block(root, inv.valid)
    temporal_rows = temporal_block.pop("products", [])
    vis_by_pid = {row["product_id"]: row for row in temporal_rows}
    for p in products:
        p["temporal_visibility"] = vis_by_pid.get(p["product_id"], {})

    catalog, artifact_links, hist_stats = _enrich_history(
        root, products, diag, strict=strict
    )

    _apply_operator_visibility(products)
    operator_alerts = collect_operator_alerts(products)

    actions_panel = build_actions_panel(
        root, products_dir=products_dir, diagnostics=diag, strict=strict
    )
    economics_resources = _economics_resources_block(root, diag)
    autonomy_safety = _autonomy_safety_block(root, diag, strict=strict)
    last_loop_run = _last_loop_run_block(root, diag)
    self_improvement = _self_improvement_block(root)
    if self_improvement.get("present"):
        artifact_links = {
            **artifact_links,
            "self_improvement_plan_from_dashboard": "../self_improvement/plan_latest.json",
            "self_improvement_md_from_dashboard": "../self_improvement/plan_latest.md",
        }
    artifact_links = {
        **artifact_links,
        "ideas_latest_from_dashboard": "../ideas/latest.json",
        "orchestration_portfolio_priorities_from_dashboard": "../orchestration/latest/portfolio_priorities.json",
        "orchestration_portfolio_priority_trends_from_dashboard": "../orchestration/latest/portfolio_priority_trends.json",
        "strategy_latest_dir_repo": "runs/strategy/latest/",
        "strategy_latest_dir_from_dashboard": "../strategy/latest/",
        "planning_latest_dir_repo": "runs/planning/latest/",
        "planning_latest_dir_from_dashboard": "../planning/latest/",
    }
    orchestration_portfolio_priorities = _orchestration_portfolio_priorities_block(
        root, diag, strict=strict
    )
    orchestration_portfolio_priority_trends = _orchestration_portfolio_priority_trends_block(
        root, diag, strict=strict
    )

    integrity: dict[str, Any] = empty_integrity()
    integrity["history_snapshots"] = hist_stats
    integrity["inventory"] = {
        "valid": inv.summary.valid_count,
        "invalid": inv.summary.invalid_count,
    }
    integrity["strict_mode"] = strict
    integrity["has_diagnostics_errors"] = len(diag.errors) > 0

    portfolio_temporal = sum(p.get("temporal_findings_count", 0) for p in products)

    return {
        "schema": "argus.dashboard.v4",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "strict": strict,
        "diagnostics": diag.to_payload(),
        "integrity": integrity,
        "inventory": {
            "valid_count": inv.summary.valid_count,
            "invalid_count": inv.summary.invalid_count,
        },
        "temporal_findings_portfolio_total": portfolio_temporal,
        "temporal_finding_kind_values": sorted(k.value for k in TEMPORAL_FINDING_KINDS),
        "history": {
            "snapshots_catalog": catalog,
            "snapshots_count": len(catalog),
            "window_presets": [3, 5, "all"],
        },
        "artifact_links": artifact_links,
        "products": products,
        "operator_alerts": operator_alerts,
        "economics_resources": economics_resources,
        "actions_panel": actions_panel,
        "autonomy_safety": autonomy_safety,
        "last_loop_run": last_loop_run,
        "self_improvement": self_improvement,
        "temporal": {
            **temporal_block,
            "recent_temporal_findings": collect_recent_temporal_findings(
                root, sorted(inv.valid.keys())
            ),
        },
        "ideas": ideas_block,
        "refinement": build_refinement_dashboard_block(root),
        "orchestration_portfolio_priorities": orchestration_portfolio_priorities,
        "orchestration_portfolio_priority_trends": orchestration_portfolio_priority_trends,
    }


def _preview_payload(payload: dict[str, Any]) -> str:
    try:
        s = json.dumps(payload, sort_keys=True)[:240]
        return s + ("…" if len(s) >= 240 else "")
    except (TypeError, ValueError):
        return str(payload)[:240]


def _yaml_summary_lines(node: ProductNode) -> list[str]:
    """Short bullet lines for display (not raw file)."""
    from argus.products.reporting import format_product_summary

    return format_product_summary(node).splitlines()
