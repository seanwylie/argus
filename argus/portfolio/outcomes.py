"""
Portfolio outcomes — did recent cycles actually improve product and portfolio state?

Deterministic comparison of per-product fingerprints across stamped delta reports and quiescence
baselines (same spine as :mod:`argus.portfolio.history`), plus progression outcomes and intervention
flags. Grounded in existing artifacts only (no pipeline execution).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.policy.operator_policy import load_operator_policy
from argus.portfolio.cycle import portfolio_cycle_dir
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA, portfolio_delta_report_dir
from argus.portfolio.history import DEFAULT_LIMIT_HISTORY, load_latest_n_artifacts
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.outcomes_mission import interpret_product_outcome_mission
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA, portfolio_quiescence_dir

PORTFOLIO_OUTCOMES_SCHEMA = "argus.portfolio_outcomes.v1"

_TIER_RANK: dict[str, int] = {
    "unprofiled": 0,
    "import_incomplete": 1,
    "observe_gap": 2,
    "interpret_gap": 3,
    "advance_ready": 4,
}

_BLOCKED_OUTCOMES = frozenset({"blocked_waiting", "blocked_approval"})


def portfolio_outcomes_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "outcomes"


def load_canonical_portfolio_outcomes(repo_root: Path) -> dict[str, Any] | None:
    """
    Read only ``runs/portfolio/outcomes/latest.json`` when schema matches.

    Does **not** call :func:`evaluate_portfolio_outcomes` — avoids split-brain when the durable
    artifact is missing.
    """
    raw = _load_json(portfolio_outcomes_dir(Path(repo_root).resolve()) / "latest.json")
    if raw and str(raw.get("schema") or "") == PORTFOLIO_OUTCOMES_SCHEMA:
        return raw
    return None


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _runs_sorted_asc(runs: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    return sorted(runs, key=lambda x: x[0])


def _safe_float(x: object) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _safe_int(x: object) -> int | None:
    if x is None:
        return None
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def _tier_rank(tier: object) -> int | None:
    t = str(tier or "").strip()
    if not t:
        return None
    return _TIER_RANK.get(t)


def _schema_ok(payload: dict[str, Any], schema: str) -> bool:
    return str(payload.get("schema") or "") == schema


def _baseline_per_product_delta(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not _schema_ok(payload, PORTFOLIO_DELTA_REPORT_SCHEMA):
        return {}
    bl = payload.get("baseline_for_next_run")
    if not isinstance(bl, dict):
        return {}
    pp = bl.get("per_product")
    if not isinstance(pp, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in pp.items():
        if isinstance(v, dict):
            out[str(k)] = dict(v)
    return out


def _baseline_per_product_quiescence(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not _schema_ok(payload, PORTFOLIO_QUIESCENCE_SCHEMA):
        return {}
    bl = payload.get("baseline_for_next_run")
    if not isinstance(bl, dict):
        return {}
    pp = bl.get("per_product")
    if not isinstance(pp, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for k, v in pp.items():
        if isinstance(v, dict):
            out[str(k)] = dict(v)
    return out


def _merge_snapshots(
    deltas: list[tuple[str, dict[str, Any]]],
    quiescence_runs: list[tuple[str, dict[str, Any]]],
    *,
    limit_points: int,
) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """
    Chronological (run_id ascending) list of (run_id, row) per product.
    Delta rows win over quiescence for the same run_id.
    """
    by_pid: dict[str, list[tuple[str, dict[str, Any]]]] = {}

    def _append(pid: str, rid: str, row: dict[str, Any]) -> None:
        by_pid.setdefault(pid, []).append((rid, dict(row)))

    for rid, payload in _runs_sorted_asc(list(deltas)):
        for pid, row in _baseline_per_product_delta(payload).items():
            _append(pid, rid, row)

    for rid, payload in _runs_sorted_asc(list(quiescence_runs)):
        for pid, row in _baseline_per_product_quiescence(payload).items():
            existing_rids = {r for r, _ in by_pid.get(pid, [])}
            if rid in existing_rids:
                continue
            _append(pid, rid, row)

    for pid in list(by_pid.keys()):
        by_pid[pid].sort(key=lambda x: x[0])
        if limit_points > 0 and len(by_pid[pid]) > limit_points:
            by_pid[pid] = by_pid[pid][-limit_points:]
    return by_pid


def _progression_blocked_ratio(
    progressions: list[tuple[str, dict[str, Any]]],
    pid: str,
) -> tuple[int, int, int]:
    """Return (blocked_count, total_count, consecutive_blocked_prefix) for *pid*."""
    outcomes: list[str] = []
    for _rid, payload in _runs_sorted_asc(list(progressions)):
        if not _schema_ok(payload, PORTFOLIO_PROGRESSION_SCHEMA):
            continue
        for row in payload.get("products") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("product_id") or "").strip() != pid:
                continue
            outcomes.append(str(row.get("outcome") or ""))
    if not outcomes:
        return 0, 0, 0
    blk = sum(1 for o in outcomes if o in _BLOCKED_OUTCOMES)
    n = 0
    for o in outcomes:
        if o in _BLOCKED_OUTCOMES:
            n += 1
        else:
            break
    return blk, len(outcomes), n


def _orchestration_blocked(orch: object) -> bool:
    s = str(orch or "").lower()
    return "blocked_waiting" in s or "blocked_approval" in s or s in (
        "blocked_waiting_input",
        "blocked_waiting_approval",
    )


def _import_bad(fps: object, gt: object) -> bool:
    f = str(fps or "").lower()
    g = str(gt or "").lower()
    if f in ("partial", "failed", "skipped"):
        return True
    if g == "failed":
        return True
    return False


def _intervention_trajectory(
    interventions: list[tuple[str, dict[str, Any]]],
    pid: str,
) -> tuple[str, list[str]]:
    cats: list[str | None] = []
    for _rid, payload in _runs_sorted_asc(list(interventions)):
        if not _schema_ok(payload, PORTFOLIO_INTERVENTION_SCHEMA):
            continue
        cat: str | None = None
        for row in payload.get("flagged_products") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("product_id") or "").strip() != pid:
                continue
            cat = str(row.get("intervention_category") or "") or None
            break
        cats.append(cat)

    if len(cats) < 2:
        return "unknown", ["outcomes.intervention_insufficient_samples"]

    first, last = cats[0], cats[-1]
    if first is None and last is None:
        return "clear", ["outcomes.intervention_clear_throughout"]
    if first is not None and last is None:
        return "resolved", ["outcomes.intervention_resolved"]
    if first is None and last is not None:
        return "newly_flagged", ["outcomes.intervention_newly_flagged"]
    if first == last:
        return "repeated", ["outcomes.intervention_category_repeated"]
    return "changed", ["outcomes.intervention_category_changed"]


def _classify_product(
    pid: str,
    snaps: list[tuple[str, dict[str, Any]]],
    *,
    progressions: list[tuple[str, dict[str, Any]]],
    interventions: list[tuple[str, dict[str, Any]]],
    debt_delta_material: float,
    confidence_delta_material: float,
) -> dict[str, Any]:
    reason_codes: list[str] = []
    if len(snaps) < 2:
        reason_codes.append("outcomes.insufficient_snapshot_history")
        int_pat0, int_rc = _intervention_trajectory(interventions, pid)
        blk_cnt, prog_tot, blk_prefix = _progression_blocked_ratio(progressions, pid)
        reason_codes.extend(int_rc)
        return {
            "product_id": pid,
            "readiness_trajectory": "unknown",
            "understanding_debt_trajectory": "unknown",
            "queue_rank_trajectory": "unknown",
            "next_action_pattern": "unknown",
            "blocked_pattern": "unknown",
            "import_health_trajectory": "unknown",
            "decision_confidence_trajectory": "unknown",
            "intervention_pattern": int_pat0,
            "progression_blocked": {
                "blocked_count": blk_cnt,
                "total_runs": prog_tot,
                "consecutive_blocked_prefix": blk_prefix,
            },
            "overall_trajectory": "no_meaningful_movement",
            "snapshot_run_span": {"first": None, "last": None, "count": len(snaps)},
            "reason_codes": sorted(set(reason_codes)),
        }

    first_row, last_row = snaps[0][1], snaps[-1][1]
    first_rid, last_rid = snaps[0][0], snaps[-1][0]

    tr0 = _tier_rank(first_row.get("readiness_tier"))
    tr1 = _tier_rank(last_row.get("readiness_tier"))
    readiness = "unknown"
    if tr0 is not None and tr1 is not None:
        if tr1 > tr0:
            readiness = "improved"
            reason_codes.append("outcomes.readiness_improved")
        elif tr1 < tr0:
            readiness = "regressed"
            reason_codes.append("outcomes.readiness_regressed")
        else:
            readiness = "unchanged"
            reason_codes.append("outcomes.readiness_unchanged")

    d0 = _safe_float(first_row.get("understanding_debt"))
    d1 = _safe_float(last_row.get("understanding_debt"))
    debt_tr = "unknown"
    if d0 is not None and d1 is not None:
        delta = d1 - d0
        if delta <= -debt_delta_material:
            debt_tr = "decreased"
            reason_codes.append("outcomes.debt_decreased")
        elif delta >= debt_delta_material:
            debt_tr = "increased"
            reason_codes.append("outcomes.debt_increased")
        else:
            debt_tr = "flat"
            reason_codes.append("outcomes.debt_flat")

    r0 = _safe_int(first_row.get("queue_rank"))
    r1 = _safe_int(last_row.get("queue_rank"))
    rank_tr = "unknown"
    if r0 is not None and r1 is not None:
        if r1 < r0:
            rank_tr = "improved"
            reason_codes.append("outcomes.queue_rank_improved")
        elif r1 > r0:
            rank_tr = "worsened"
            reason_codes.append("outcomes.queue_rank_worsened")
        else:
            rank_tr = "flat"
            reason_codes.append("outcomes.queue_rank_flat")

    na0 = str(first_row.get("next_action") or "").strip().lower()
    na1 = str(last_row.get("next_action") or "").strip().lower()
    na0e = na0 in ("", "none")
    na1e = na1 in ("", "none")
    next_pat = "unknown"
    if na0e and na1e:
        next_pat = "repeated"
        reason_codes.append("outcomes.next_action_still_none")
    elif not na0e and na1e:
        next_pat = "resolved"
        reason_codes.append("outcomes.next_action_cleared_to_none")
    elif na0 == na1 and not na0e:
        next_pat = "repeated"
        reason_codes.append("outcomes.next_action_unchanged")
    else:
        next_pat = "changed"
        reason_codes.append("outcomes.next_action_changed")

    o0, o1 = first_row.get("orchestration_status"), last_row.get("orchestration_status")
    blk0, blk1 = _orchestration_blocked(o0), _orchestration_blocked(o1)
    blocked_pat = "unknown"
    if not blk0 and not blk1:
        blocked_pat = "never_blocked"
        reason_codes.append("outcomes.blocked_never")
    elif blk0 and not blk1:
        blocked_pat = "cleared"
        reason_codes.append("outcomes.blocked_cleared")
    elif blk0 and blk1:
        blocked_pat = "persisted"
        reason_codes.append("outcomes.blocked_persisted")
    elif not blk0 and blk1:
        blocked_pat = "newly_blocked"
        reason_codes.append("outcomes.blocked_new")

    fps0, fps1 = first_row.get("first_pass_status"), last_row.get("first_pass_status")
    gt0, gt1 = first_row.get("gating_tier"), last_row.get("gating_tier")
    imp_tr = "unknown"
    bad0, bad1 = _import_bad(fps0, gt0), _import_bad(fps1, gt1)
    if bad0 and not bad1:
        imp_tr = "recovered"
        reason_codes.append("outcomes.import_recovered")
    elif bad0 and bad1:
        imp_tr = "stayed_bad"
        reason_codes.append("outcomes.import_stayed_bad")
    elif not bad0 and not bad1:
        imp_tr = "unchanged_ok"
        reason_codes.append("outcomes.import_ok")
    elif not bad0 and bad1:
        imp_tr = "regressed"
        reason_codes.append("outcomes.import_regressed")

    c0 = _safe_float(first_row.get("top_decision_confidence"))
    c1 = _safe_float(last_row.get("top_decision_confidence"))
    conf_tr = "unknown"
    if c0 is not None and c1 is not None:
        if c1 - c0 >= confidence_delta_material:
            conf_tr = "improved"
            reason_codes.append("outcomes.confidence_improved")
        elif c0 - c1 >= confidence_delta_material:
            conf_tr = "worsened"
            reason_codes.append("outcomes.confidence_worsened")
        else:
            conf_tr = "flat"
            reason_codes.append("outcomes.confidence_flat")
    elif c0 is None and c1 is not None:
        conf_tr = "improved"
        reason_codes.append("outcomes.confidence_emerged")
    elif c0 is not None and c1 is None:
        conf_tr = "worsened"
        reason_codes.append("outcomes.confidence_lost")

    int_pat, int_codes = _intervention_trajectory(interventions, pid)
    reason_codes.extend(int_codes)

    blk_cnt, prog_tot, blk_prefix = _progression_blocked_ratio(progressions, pid)
    if prog_tot >= 3 and blk_cnt >= max(2, prog_tot * 2 // 3):
        reason_codes.append("outcomes.progression_repeated_blocked")
    if blk_prefix >= 2:
        reason_codes.append("outcomes.progression_blocked_streak")

    pos = sum(
        1
        for x in (
            readiness == "improved",
            debt_tr == "decreased",
            rank_tr == "improved",
            next_pat == "resolved",
            blocked_pat == "cleared",
            imp_tr == "recovered",
            conf_tr == "improved",
            int_pat == "resolved",
        )
        if x
    )
    neg = sum(
        1
        for x in (
            readiness == "regressed",
            debt_tr == "increased",
            rank_tr == "worsened",
            blocked_pat == "persisted",
            blocked_pat == "newly_blocked",
            imp_tr == "stayed_bad",
            imp_tr == "regressed",
            conf_tr == "worsened",
            int_pat == "repeated",
            int_pat == "newly_flagged",
        )
        if x
    )

    if pos > neg and pos >= 1:
        overall = "positive"
        reason_codes.append("outcomes.overall_positive")
    elif neg > pos and neg >= 1:
        overall = "negative"
        reason_codes.append("outcomes.overall_negative")
    elif pos == 0 and neg == 0:
        overall = "no_meaningful_movement"
        reason_codes.append("outcomes.overall_flat")
    else:
        overall = "mixed"
        reason_codes.append("outcomes.overall_mixed")

    return {
        "product_id": pid,
        "readiness_trajectory": readiness,
        "understanding_debt_trajectory": debt_tr,
        "queue_rank_trajectory": rank_tr,
        "next_action_pattern": next_pat,
        "blocked_pattern": blocked_pat,
        "import_health_trajectory": imp_tr,
        "decision_confidence_trajectory": conf_tr,
        "intervention_pattern": int_pat,
        "progression_blocked": {
            "blocked_count": blk_cnt,
            "total_runs": prog_tot,
            "consecutive_blocked_prefix": blk_prefix,
        },
        "overall_trajectory": overall,
        "snapshot_run_span": {"first": first_rid, "last": last_rid, "count": len(snaps)},
        "reason_codes": sorted(set(reason_codes)),
    }


def evaluate_portfolio_outcomes(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    # Materiality thresholds: repository-level policy (portfolio-wide comparison; not per-product mission).
    pol = load_operator_policy(root)
    qm = pol["quiescence"]
    ddm = float(qm["debt_delta_material"])
    cdm = float(qm["confidence_delta_material"])

    lim = max(1, int(limit_history))
    deltas = load_latest_n_artifacts(root, "delta_report", limit=lim)
    quiescence_runs = load_latest_n_artifacts(root, "quiescence", limit=lim)
    progressions = load_latest_n_artifacts(root, "progression", limit=lim)
    interventions = load_latest_n_artifacts(root, "intervention", limit=lim)
    cycles = load_latest_n_artifacts(root, "cycle", limit=lim)

    snapshots = _merge_snapshots(deltas, quiescence_runs, limit_points=lim)

    per_product: list[dict[str, Any]] = []
    all_codes: list[str] = []

    for pid in sorted(snapshots.keys()):
        row = _classify_product(
            pid,
            snapshots[pid],
            progressions=progressions,
            interventions=interventions,
            debt_delta_material=ddm,
            confidence_delta_material=cdm,
        )
        row["mission_interpretation"] = interpret_product_outcome_mission(root, pid, row)
        per_product.append(row)
        all_codes.extend(row.get("reason_codes") or [])
        all_codes.extend(row["mission_interpretation"].get("interpretation_reason_codes") or [])

    positive = [p["product_id"] for p in per_product if p.get("overall_trajectory") == "positive"]
    negative = [p["product_id"] for p in per_product if p.get("overall_trajectory") == "negative"]
    flat = [p["product_id"] for p in per_product if p.get("overall_trajectory") == "no_meaningful_movement"]

    latest_cycle_rec: str | None = None
    if cycles:
        c0 = cycles[0][1]
        latest_cycle_rec = str((c0.get("summary") or {}).get("overall_operator_recommendation") or "") or None

    if not deltas and not quiescence_runs:
        all_codes.append("outcomes.no_delta_or_quiescence_baseline")

    outcome_pids = [str(p.get("product_id") or "") for p in per_product if p.get("product_id")]
    align_counts = {"positive": 0, "neutral": 0, "negative": 0}
    for p in per_product:
        mi = p.get("mission_interpretation") if isinstance(p, dict) else None
        if isinstance(mi, dict):
            al = str(mi.get("mission_alignment") or "neutral")
            if al in align_counts:
                align_counts[al] += 1
    return {
        "schema": PORTFOLIO_OUTCOMES_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "portfolio_mission_provenance": build_portfolio_mission_provenance(root, outcome_pids),
        "mission_interpretation_note": (
            "Per-product mission_interpretation uses argus.portfolio_outcome_mission_interpretation.v1; "
            "additive to trajectories — no change to raw outcome classification."
        ),
        "mission_alignment_summary": align_counts,
        "inputs": {
            "limit_history": lim,
            "artifact_paths": {
                "delta_report": str(portfolio_delta_report_dir(root)),
                "quiescence": str(portfolio_quiescence_dir(root)),
                "progression": str(portfolio_progression_dir(root)),
                "intervention": str(portfolio_intervention_dir(root)),
                "cycle": str(portfolio_cycle_dir(root)),
            },
            "loaded_counts": {
                "delta_report": len(deltas),
                "quiescence": len(quiescence_runs),
                "progression": len(progressions),
                "intervention": len(interventions),
                "cycle": len(cycles),
            },
        },
        "thresholds_used": {
            "debt_delta_material": ddm,
            "confidence_delta_material": cdm,
        },
        "per_product_outcomes": per_product,
        "portfolio_outcome_summary": {
            "products_evaluated": len(per_product),
            "positive_count": len(positive),
            "negative_count": len(negative),
            "no_meaningful_movement_count": len(flat),
            "mixed_count": sum(1 for p in per_product if p.get("overall_trajectory") == "mixed"),
            "latest_cycle_operator_recommendation": latest_cycle_rec,
        },
        "outcome_reason_codes": sorted(set(all_codes)),
        "products_with_positive_trajectory": sorted(positive),
        "products_with_negative_trajectory": sorted(negative),
        "products_with_no_meaningful_movement": sorted(flat),
    }


def render_portfolio_outcomes_markdown(payload: dict[str, Any]) -> str:
    summ = payload.get("portfolio_outcome_summary") or {}
    lines = [
        "# Portfolio outcomes",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    mas = payload.get("mission_alignment_summary") or {}
    if mas:
        lines.extend(
            [
                "## Mission-aware interpretation",
                "",
                str(payload.get("mission_interpretation_note") or ""),
                "",
                f"- **Mission alignment (portfolio):** positive `{mas.get('positive', 0)}` · "
                f"neutral `{mas.get('neutral', 0)}` · negative `{mas.get('negative', 0)}`",
                "",
            ]
        )
    lines.extend(
        [
            "## Summary",
            "",
            f"- **Products evaluated:** {summ.get('products_evaluated')}",
            f"- **Positive trajectory:** {summ.get('positive_count')} — "
            f"{', '.join(f'`{p}`' for p in (payload.get('products_with_positive_trajectory') or [])) or '—'}",
            f"- **Negative trajectory:** {summ.get('negative_count')} — "
            f"{', '.join(f'`{p}`' for p in (payload.get('products_with_negative_trajectory') or [])) or '—'}",
            f"- **No meaningful movement:** {summ.get('no_meaningful_movement_count')} — "
            f"{', '.join(f'`{p}`' for p in (payload.get('products_with_no_meaningful_movement') or [])) or '—'}",
            f"- **Mixed:** {summ.get('mixed_count')}",
            f"- **Latest cycle recommendation:** `{summ.get('latest_cycle_operator_recommendation')}`",
            "",
            "## Per-product",
            "",
            "| Product | Overall | Readiness | Debt | Rank | Next action | Blocked | Import | Confidence | Intervention |",
            "|---------|---------|-----------|------|------|-------------|---------|--------|------------|--------------|",
        ]
    )
    for row in payload.get("per_product_outcomes") or []:
        if not isinstance(row, dict):
            continue
        pid = row.get("product_id")
        lines.append(
            f"| `{pid}` | `{row.get('overall_trajectory')}` | `{row.get('readiness_trajectory')}` | "
            f"`{row.get('understanding_debt_trajectory')}` | `{row.get('queue_rank_trajectory')}` | "
            f"`{row.get('next_action_pattern')}` | `{row.get('blocked_pattern')}` | "
            f"`{row.get('import_health_trajectory')}` | `{row.get('decision_confidence_trajectory')}` | "
            f"`{row.get('intervention_pattern')}` |"
        )
    lines.extend(["", "## Per-product mission interpretation", ""])
    for row in payload.get("per_product_outcomes") or []:
        if not isinstance(row, dict):
            continue
        mi = row.get("mission_interpretation")
        if not isinstance(mi, dict):
            continue
        pid = row.get("product_id")
        lines.append(f"### `{pid}`")
        lines.append("")
        lines.append(f"- **Objective:** `{mi.get('mission_objective')}` · **alignment:** `{mi.get('mission_alignment')}` "
                     f"(score `{mi.get('mission_alignment_score')}`)")
        lines.append(f"- **Driver support:** {', '.join(f'`{d}`' for d in (mi.get('driver_support_signals') or [])) or '—'}")
        gr = mi.get("guardrail_risk_signals") or []
        if gr:
            for g in gr:
                if isinstance(g, dict):
                    lines.append(
                        f"- **Guardrail `{g.get('guardrail')}`:** "
                        + ", ".join(f"`{c}`" for c in (g.get("risk_codes") or []))
                    )
        else:
            lines.append("- **Guardrail risks:** —")
        lines.append(f"- **Summary:** {mi.get('outcome_quality_summary')}")
        lines.append("")
    lines.extend(["", "## Reason codes", ""])
    for c in payload.get("outcome_reason_codes") or []:
        lines.append(f"- `{c}`")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_outcomes_artifacts(
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
    d = portfolio_outcomes_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_outcomes_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_outcomes(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_outcomes(repo_root, limit_history=limit_history)
    if write_artifacts:
        write_portfolio_outcomes_artifacts(repo_root, payload)
    return payload
