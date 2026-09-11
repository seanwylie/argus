"""Read-only slices of strategy / planning / orchestration artifacts for dashboard and doctor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.orchestrator.artifact_paths import orchestration_latest_path, portfolio_priorities_path
from argus.orchestrator.portfolio_priorities import read_portfolio_priorities_json
from argus.planning.snapshot import PLANNING_SNAPSHOT_SCHEMA, planning_latest_path
from argus.strategy.snapshot import STRATEGY_SNAPSHOT_SCHEMA, strategy_latest_path


def _safe_read_json_dict(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return raw if isinstance(raw, dict) else None


def summarize_strategy_latest_for_dashboard(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Compact, display-oriented fields from ``runs/strategy/latest/<product_id>.json``."""
    root = repo_root.resolve()
    rel = f"runs/strategy/latest/{product_id}.json"
    base: dict[str, Any] = {
        "schema": "argus.dashboard_strategy_snapshot_slice.v1",
        "present": False,
        "readable": False,
        "path_repo": rel,
        "path_from_dashboard": f"../strategy/latest/{product_id}.json",
    }
    p = strategy_latest_path(root, product_id)
    if not p.is_file():
        return base
    raw = _safe_read_json_dict(p)
    if raw is None:
        return {**base, "present": True, "parse_error": True}
    out = {**base, "present": True, "readable": True, "schema": "argus.dashboard_strategy_snapshot_slice.v1"}
    sch = str(raw.get("schema") or "")
    if sch != STRATEGY_SNAPSHOT_SCHEMA:
        out["schema_mismatch"] = True
        out["schema_seen"] = sch or None
        return out
    out["generated_at_utc"] = raw.get("generated_at_utc")
    out["posture"] = raw.get("posture")
    out["posture_raw"] = raw.get("posture_raw")
    out["skepticism_applied"] = raw.get("skepticism_applied")
    out["skepticism_reason"] = raw.get("skepticism_reason")
    out["recommended_mode"] = raw.get("recommended_mode")
    summ = raw.get("summary")
    if isinstance(summ, str) and summ.strip():
        out["summary"] = summ.strip()[:720]
    return out


def summarize_planning_latest_for_dashboard(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Compact fields from ``runs/planning/latest/<product_id>.json``."""
    root = repo_root.resolve()
    rel = f"runs/planning/latest/{product_id}.json"
    base: dict[str, Any] = {
        "schema": "argus.dashboard_planning_snapshot_slice.v1",
        "present": False,
        "readable": False,
        "path_repo": rel,
        "path_from_dashboard": f"../planning/latest/{product_id}.json",
    }
    p = planning_latest_path(root, product_id)
    if not p.is_file():
        return base
    raw = _safe_read_json_dict(p)
    if raw is None:
        return {**base, "present": True, "parse_error": True}
    out = {**base, "present": True, "readable": True, "schema": "argus.dashboard_planning_snapshot_slice.v1"}
    sch = str(raw.get("schema") or "")
    if sch != PLANNING_SNAPSHOT_SCHEMA:
        out["schema_mismatch"] = True
        out["schema_seen"] = sch or None
        return out
    out["generated_at_utc"] = raw.get("generated_at_utc")
    out["planning_mode"] = raw.get("planning_mode")
    out["posture"] = raw.get("posture")
    ws = raw.get("priority_workstreams")
    if isinstance(ws, list):
        out["priority_workstreams"] = [str(x) for x in ws[:12] if str(x).strip()]
    else:
        out["priority_workstreams"] = []
    ra = raw.get("recommended_actions")
    out["recommended_actions_count"] = len(ra) if isinstance(ra, list) else 0
    return out


def summarize_orchestration_planning_explainability(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Planning-influence fields from ``runs/orchestration/latest/<product_id>.json`` ``eligibility_facts``."""
    root = repo_root.resolve()
    rel = f"runs/orchestration/latest/{product_id}.json"
    base: dict[str, Any] = {
        "schema": "argus.dashboard_orchestration_planning_influence.v1",
        "present": False,
        "readable": False,
        "path_repo": rel,
        "path_from_dashboard": f"../orchestration/latest/{product_id}.json",
    }
    p = orchestration_latest_path(root, product_id)
    if not p.is_file():
        return base
    raw = _safe_read_json_dict(p)
    if raw is None:
        return {**base, "present": True, "parse_error": True}
    facts = raw.get("eligibility_facts")
    ef = facts if isinstance(facts, dict) else {}
    return {
        **base,
        "present": True,
        "readable": True,
        "planning_mode_considered": ef.get("planning_mode_considered"),
        "planning_priority_adjustment_applied": ef.get("planning_priority_adjustment_applied"),
        "next_action_planning_note": ef.get("next_action_planning_note"),
    }


def build_doctor_strategy_planning_section(repo: Path, valid_product_ids: list[str]) -> dict[str, Any]:
    """
    Informational summary for ``argus doctor`` JSON (no writes).

    Counts readable vs malformed optional artifacts; surfaces portfolio_priorities when present.
    """
    root = repo.resolve()
    pids = sorted({str(p).strip() for p in valid_product_ids if str(p).strip()})
    st_pres = st_read = st_bad = 0
    pl_pres = pl_read = pl_bad = 0
    planning_without_orch_state = 0

    for pid in pids:
        sp = strategy_latest_path(root, pid)
        if sp.is_file():
            st_pres += 1
            raw = _safe_read_json_dict(sp)
            if raw is None:
                st_bad += 1
            elif str(raw.get("schema") or "") != STRATEGY_SNAPSHOT_SCHEMA:
                st_bad += 1
            else:
                st_read += 1

        plp = planning_latest_path(root, pid)
        if plp.is_file():
            pl_pres += 1
            rawp = _safe_read_json_dict(plp)
            if rawp is None:
                pl_bad += 1
            elif str(rawp.get("schema") or "") != PLANNING_SNAPSHOT_SCHEMA:
                pl_bad += 1
            else:
                pl_read += 1
                if not orchestration_latest_path(root, pid).is_file():
                    planning_without_orch_state += 1

    pp_path = portfolio_priorities_path(root)
    pp_raw, _pp_err = read_portfolio_priorities_json(root)
    pp_ok = pp_raw is not None

    notes: list[str] = []
    if st_bad:
        notes.append(
            f"Some strategy snapshot JSON under runs/strategy/latest/ could not be read ({st_bad} file(s))."
        )
    if pl_bad:
        notes.append(
            f"Some planning snapshot JSON under runs/planning/latest/ could not be read ({pl_bad} file(s))."
        )
    if planning_without_orch_state:
        notes.append(
            f"Planning snapshot(s) present for {planning_without_orch_state} product(s) without "
            "runs/orchestration/latest/<product>.json — refresh orchestration state to expose planning influence."
        )

    return {
        "schema": "argus.doctor_strategy_planning.v1",
        "strategy": {
            "files_present": st_pres,
            "readable_ok": st_read,
            "malformed_or_schema_mismatch": st_bad,
        },
        "planning": {
            "files_present": pl_pres,
            "readable_ok": pl_read,
            "malformed_or_schema_mismatch": pl_bad,
        },
        "orchestration": {
            "planning_snapshots_missing_orchestration_state": planning_without_orch_state,
        },
        "portfolio_priorities": {
            "path_repo": "runs/orchestration/latest/portfolio_priorities.json",
            "present": pp_path.is_file(),
            "readable_ok": pp_ok,
            "recommended_product_id": pp_raw.get("recommended_product_id") if pp_raw else None,
        },
        "notes": notes,
    }
