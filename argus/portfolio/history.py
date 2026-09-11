"""
Portfolio artifact history spine — list, load, align, and summarize trends across stamped runs.

Does not replace per-module evaluation logic; centralizes filesystem archaeology and trend views.
"""

from __future__ import annotations

import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.policy.operator_policy import load_operator_policy
from argus.portfolio.artifact_index import (
    list_timestamped_portfolio_json_files,
    stamp_run_id_from_path,
)
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA, portfolio_delta_report_dir
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, operator_queue_output_dir
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir
from argus.portfolio.quiescence import PORTFOLIO_QUIESCENCE_SCHEMA, portfolio_quiescence_dir

PORTFOLIO_HISTORY_SCHEMA = "argus.portfolio_history.v1"
PORTFOLIO_CYCLE_SCHEMA = "argus.portfolio_cycle.v1"

DEFAULT_LIMIT_HISTORY = 50

_TIER_RANK: dict[str, int] = {
    "unprofiled": 0,
    "import_incomplete": 1,
    "observe_gap": 2,
    "interpret_gap": 3,
    "advance_ready": 4,
}

_BLOCKED_OUTCOMES = frozenset({"blocked_waiting", "blocked_approval"})


def portfolio_history_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "history"


def portfolio_cycle_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "cycle"


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


def _safe_float(x: str | float | int | None) -> float | None:
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


def _load_operator_queue_latest(repo_root: Path) -> dict[str, Any] | None:
    p = operator_queue_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == OPERATOR_QUEUE_SCHEMA:
        return raw
    return None


def _queue_synthetic_run_id(queue_payload: dict[str, Any]) -> str:
    g = queue_payload.get("generated_at_utc")
    if isinstance(g, str) and g.strip():
        s = re.sub(r"[^\dA-Za-z]+", "", g)[:24]
        if len(s) >= 8:
            return f"queue:{s}"
    return "queue:latest"


def artifact_counts(repo_root: Path) -> dict[str, Any]:
    """Counts of timestamped JSON files per portfolio artifact type (queue is latest-only)."""
    root = repo_root.resolve()
    oq = operator_queue_output_dir(root) / "latest.json"
    return {
        "operator_queue": {"timestamped": 0, "latest_present": oq.is_file()},
        "progression": len(list_timestamped_portfolio_json_files(portfolio_progression_dir(root))),
        "quiescence": len(list_timestamped_portfolio_json_files(portfolio_quiescence_dir(root))),
        "delta_report": len(list_timestamped_portfolio_json_files(portfolio_delta_report_dir(root))),
        "intervention": len(list_timestamped_portfolio_json_files(portfolio_intervention_dir(root))),
        "cycle": len(list_timestamped_portfolio_json_files(portfolio_cycle_dir(root))),
    }


def latest_stamp_per_type(repo_root: Path) -> dict[str, str | None]:
    """Most recent run-id stamp (filename stem) per type; queue uses synthetic id from latest.json."""
    root = repo_root.resolve()
    out: dict[str, str | None] = {}
    q = _load_operator_queue_latest(root)
    out["operator_queue"] = _queue_synthetic_run_id(q) if q else None
    for key, d in (
        ("progression", portfolio_progression_dir(root)),
        ("quiescence", portfolio_quiescence_dir(root)),
        ("delta_report", portfolio_delta_report_dir(root)),
        ("intervention", portfolio_intervention_dir(root)),
        ("cycle", portfolio_cycle_dir(root)),
    ):
        files = list_timestamped_portfolio_json_files(d)
        out[key] = stamp_run_id_from_path(files[0]) if files else None
    return out


def load_latest_n_artifacts(
    repo_root: Path,
    artifact_type: str,
    *,
    limit: int,
) -> list[tuple[str, dict[str, Any]]]:
    """
    Load up to ``limit`` newest stamped JSON payloads for ``artifact_type``.

    ``artifact_type``: ``progression`` | ``quiescence`` | ``delta_report`` | ``intervention`` | ``cycle`` | ``operator_queue``

    For ``operator_queue``, returns at most one tuple from ``latest.json`` (run_id is synthetic).
    """
    root = repo_root.resolve()
    if limit <= 0:
        return []
    if artifact_type == "operator_queue":
        q = _load_operator_queue_latest(root)
        if not q:
            return []
        return [(_queue_synthetic_run_id(q), q)]

    dir_map = {
        "progression": portfolio_progression_dir,
        "quiescence": portfolio_quiescence_dir,
        "delta_report": portfolio_delta_report_dir,
        "intervention": portfolio_intervention_dir,
        "cycle": portfolio_cycle_dir,
    }
    fn = dir_map.get(artifact_type)
    if fn is None:
        raise ValueError(f"unknown artifact_type: {artifact_type!r}")

    files = list_timestamped_portfolio_json_files(fn(root))[:limit]
    out: list[tuple[str, dict[str, Any]]] = []
    for p in files:
        rid = stamp_run_id_from_path(p)
        raw = _load_json(p)
        if raw is not None:
            out.append((rid, raw))
    return out


def _schema_ok(payload: dict[str, Any], schema: str) -> bool:
    return str(payload.get("schema") or "") == schema


def _per_product_from_fp_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "queue_rank": _safe_int(row.get("queue_rank")),
        "priority_score": _safe_float(row.get("priority_score")),
        "readiness_tier": row.get("readiness_tier"),
        "next_action": row.get("next_action"),
        "understanding_debt": _safe_float(row.get("understanding_debt")),
    }


def _extract_delta_per_product(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not _schema_ok(payload, PORTFOLIO_DELTA_REPORT_SCHEMA):
        return {}
    bl = payload.get("baseline_for_next_run")
    if not isinstance(bl, dict):
        return {}
    pp = bl.get("per_product")
    if not isinstance(pp, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for pid, row in pp.items():
        if isinstance(row, dict):
            out[str(pid)] = _per_product_from_fp_row(row)
    return out


def _extract_quiescence_per_product(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not _schema_ok(payload, PORTFOLIO_QUIESCENCE_SCHEMA):
        return {}
    bl = payload.get("baseline_for_next_run")
    if not isinstance(bl, dict):
        return {}
    pp = bl.get("per_product")
    if not isinstance(pp, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for pid, row in pp.items():
        if isinstance(row, dict):
            out[str(pid)] = _per_product_from_fp_row(row)
    return out


def _extract_progression_outcomes(payload: dict[str, Any]) -> dict[str, str]:
    if not _schema_ok(payload, PORTFOLIO_PROGRESSION_SCHEMA):
        return {}
    out: dict[str, str] = {}
    for row in payload.get("products") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").strip()
        if pid:
            out[pid] = str(row.get("outcome") or "")
    return out


def _extract_intervention_flags(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not _schema_ok(payload, PORTFOLIO_INTERVENTION_SCHEMA):
        return []
    rows: list[dict[str, Any]] = []
    for row in payload.get("flagged_products") or []:
        if isinstance(row, dict) and row.get("product_id"):
            rows.append(
                {
                    "product_id": str(row.get("product_id")),
                    "intervention_category": row.get("intervention_category"),
                    "severity": row.get("severity"),
                    "detection_reason_codes": row.get("detection_reason_codes") or [],
                }
            )
    return rows


def _nearest_stamp_le(stamps: list[str], target: str) -> str | None:
    """Pick the greatest stamp among ``stamps`` that is ``<= target`` lexicographically (same as time for UTC ids)."""
    le = [s for s in stamps if s <= target]
    return max(le) if le else None


def align_artifacts_by_cycle(
    repo_root: Path,
    *,
    limit_cycles: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    For each recent stamped cycle JSON, link known stage run_ids from the cycle payload and infer quiescence
    when missing by choosing the largest stamped quiescence ``<= cycle_run_id``.
    """
    root = repo_root.resolve()
    notes: list[str] = []
    cyc_files = list_timestamped_portfolio_json_files(portfolio_cycle_dir(root))[:limit_cycles]
    q_stamps = [stamp_run_id_from_path(p) for p in list_timestamped_portfolio_json_files(portfolio_quiescence_dir(root))]

    aligned: list[dict[str, Any]] = []
    for p in cyc_files:
        rid = stamp_run_id_from_path(p)
        raw = _load_json(p)
        if raw is None or not _schema_ok(raw, PORTFOLIO_CYCLE_SCHEMA):
            notes.append(f"cycle.skip_invalid:{rid}")
            continue
        summ = raw.get("summary") or {}
        prog = summ.get("progression") or {}
        delta = summ.get("material_deltas") or {}
        inv = summ.get("intervention") or {}
        pr = prog.get("run_id") if isinstance(prog, dict) else None
        dr = delta.get("run_id") if isinstance(delta, dict) else None
        ir = inv.get("run_id") if isinstance(inv, dict) else None
        qr = _nearest_stamp_le(q_stamps, rid)

        aligned.append(
            {
                "cycle_run_id": rid,
                "cycle_generated_at_utc": raw.get("generated_at_utc"),
                "linked_progression_run_id": pr,
                "linked_delta_run_id": dr,
                "linked_intervention_run_id": ir,
                "linked_quiescence_run_id_inferred": qr,
                "alignment_note": None,
            }
        )

    if not cyc_files:
        notes.append("alignment.no_cycle_artifacts: using per-type timelines only")

    return aligned, notes


def _merge_series(
    *,
    deltas: list[tuple[str, dict[str, Any]]],
    quiescence_runs: list[tuple[str, dict[str, Any]]],
    progressions: list[tuple[str, dict[str, Any]]],
    interventions: list[tuple[str, dict[str, Any]]],
    limit_points: int,
    debt_delta_material: float,
    score_delta_material: float,
) -> dict[str, Any]:
    """Build per-product chronological slices and trend summaries."""
    product_ids: set[str] = set()

    rank_by_pid: dict[str, list[dict[str, Any]]] = {}
    tier_by_pid: dict[str, list[dict[str, Any]]] = {}
    na_by_pid: dict[str, list[dict[str, Any]]] = {}
    debt_by_pid: dict[str, list[dict[str, Any]]] = {}
    score_by_pid: dict[str, list[dict[str, Any]]] = {}

    def _add_point(
        store: dict[str, list[dict[str, Any]]],
        pid: str,
        run_id: str,
        key: str,
        val: object,
    ) -> None:
        product_ids.add(pid)
        store.setdefault(pid, []).append({"run_id": run_id, key: val})

    # Prefer delta snapshots first (more stable per-product table), then quiescence for extra coverage.
    for rid, payload in _runs_sorted_asc(list(deltas)):
        for pid, row in _extract_delta_per_product(payload).items():
            _add_point(rank_by_pid, pid, rid, "queue_rank", row.get("queue_rank"))
            _add_point(tier_by_pid, pid, rid, "readiness_tier", row.get("readiness_tier"))
            _add_point(na_by_pid, pid, rid, "next_action", row.get("next_action"))
            _add_point(debt_by_pid, pid, rid, "understanding_debt", row.get("understanding_debt"))
            _add_point(score_by_pid, pid, rid, "priority_score", row.get("priority_score"))

    for rid, payload in _runs_sorted_asc(list(quiescence_runs)):
        for pid, row in _extract_quiescence_per_product(payload).items():
            # Fill gaps: only add if this run_id not already present for pid (delta wins)
            existing_ranks = {x["run_id"] for x in rank_by_pid.get(pid, [])}
            if rid not in existing_ranks:
                _add_point(rank_by_pid, pid, rid, "queue_rank", row.get("queue_rank"))
                _add_point(tier_by_pid, pid, rid, "readiness_tier", row.get("readiness_tier"))
                _add_point(na_by_pid, pid, rid, "next_action", row.get("next_action"))
                _add_point(debt_by_pid, pid, rid, "understanding_debt", row.get("understanding_debt"))
                _add_point(score_by_pid, pid, rid, "priority_score", row.get("priority_score"))

    prog_hist: dict[str, list[dict[str, Any]]] = {}
    for rid, payload in _runs_sorted_asc(list(progressions)):
        for pid, oc in _extract_progression_outcomes(payload).items():
            product_ids.add(pid)
            prog_hist.setdefault(pid, []).append({"run_id": rid, "outcome": oc})

    inv_hist: dict[str, list[dict[str, Any]]] = {}
    for rid, payload in _runs_sorted_asc(list(interventions)):
        for row in _extract_intervention_flags(payload):
            pid = row["product_id"]
            product_ids.add(pid)
            inv_hist.setdefault(pid, []).append({"run_id": rid, **{k: v for k, v in row.items() if k != "product_id"}})

    def _sort_by_run(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(points, key=lambda x: str(x.get("run_id") or ""))

    for store in (rank_by_pid, tier_by_pid, na_by_pid, debt_by_pid, score_by_pid):
        for pid in list(store.keys()):
            store[pid] = _sort_by_run(store[pid])
    for pid in list(prog_hist.keys()):
        prog_hist[pid] = _sort_by_run(prog_hist[pid])
    for pid in list(inv_hist.keys()):
        inv_hist[pid] = _sort_by_run(inv_hist[pid])

    def _trim(lst: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        if not lst:
            return []
        return lst[-limit_points:]

    per_product: dict[str, Any] = {}
    for pid in sorted(product_ids):
        per_product[pid] = {
            "queue_rank_history": _trim(rank_by_pid.get(pid)),
            "readiness_tier_history": _trim(tier_by_pid.get(pid)),
            "next_action_history": _trim(na_by_pid.get(pid)),
            "understanding_debt_history": _trim(debt_by_pid.get(pid)),
            "priority_score_history": _trim(score_by_pid.get(pid)),
            "progression_outcome_history": _trim(prog_hist.get(pid)),
            "intervention_history": _trim(inv_hist.get(pid)),
        }

    trends = _compute_trend_summaries(
        per_product,
        rank_by_pid=rank_by_pid,
        tier_by_pid=tier_by_pid,
        na_by_pid=na_by_pid,
        debt_by_pid=debt_by_pid,
        score_by_pid=score_by_pid,
        prog_hist=prog_hist,
        debt_delta_material=debt_delta_material,
        score_delta_material=score_delta_material,
    )

    return {"per_product": per_product, "trend_summaries": trends}


def _compute_trend_summaries(
    per_product: dict[str, Any],
    *,
    rank_by_pid: dict[str, list[dict[str, Any]]],
    tier_by_pid: dict[str, list[dict[str, Any]]],
    na_by_pid: dict[str, list[dict[str, Any]]],
    debt_by_pid: dict[str, list[dict[str, Any]]],
    score_by_pid: dict[str, list[dict[str, Any]]],
    prog_hist: dict[str, list[dict[str, Any]]],
    debt_delta_material: float,
    score_delta_material: float,
) -> dict[str, list[dict[str, Any]]]:
    rising: list[dict[str, Any]] = []
    cooling: list[dict[str, Any]] = []
    chronic_blocked: list[dict[str, Any]] = []
    improving: list[dict[str, Any]] = []
    thrashing: list[dict[str, Any]] = []

    for pid, row in per_product.items():
        ranks = rank_by_pid.get(pid) or []
        scores = score_by_pid.get(pid) or []
        tiers = tier_by_pid.get(pid) or []
        nas = na_by_pid.get(pid) or []
        debts = debt_by_pid.get(pid) or []
        prows = prog_hist.get(pid) or []

        if len(ranks) >= 2:
            r0 = _safe_int(ranks[0].get("queue_rank"))
            r1 = _safe_int(ranks[-1].get("queue_rank"))
            if r0 is not None and r1 is not None:
                # Lower queue_rank number = higher priority.
                if r1 < r0:
                    rising.append(
                        {"product_id": pid, "signal": "queue_rank_priority_up", "from_rank": r0, "to_rank": r1}
                    )
                elif r1 > r0:
                    cooling.append(
                        {"product_id": pid, "signal": "queue_rank_priority_down", "from_rank": r0, "to_rank": r1}
                    )

        if len(scores) >= 2:
            s0 = _safe_float(scores[0].get("priority_score"))
            s1 = _safe_float(scores[-1].get("priority_score"))
            if s0 is not None and s1 is not None:
                if s1 - s0 >= score_delta_material and not any(x["product_id"] == pid for x in rising):
                    rising.append({"product_id": pid, "signal": "priority_score_up", "delta": round(s1 - s0, 2)})
                elif s0 - s1 >= score_delta_material and not any(x["product_id"] == pid for x in cooling):
                    cooling.append({"product_id": pid, "signal": "priority_score_down", "delta": round(s0 - s1, 2)})

        if len(prows) >= 3:
            outcomes = [str(x.get("outcome") or "") for x in prows]
            blk = sum(1 for o in outcomes if o in _BLOCKED_OUTCOMES)
            if blk >= max(2, len(outcomes) * 2 // 3):
                chronic_blocked.append(
                    {"product_id": pid, "blocked_ratio": round(blk / len(outcomes), 2), "samples": len(outcomes)}
                )

        if len(tiers) >= 2:
            tr0 = _tier_rank(tiers[0].get("readiness_tier"))
            tr1 = _tier_rank(tiers[-1].get("readiness_tier"))
            d0 = _safe_float(debts[0].get("understanding_debt")) if debts else None
            d1 = _safe_float(debts[-1].get("understanding_debt")) if debts else None
            tier_ok = tr0 is not None and tr1 is not None and tr1 > tr0
            debt_ok = (
                d0 is not None
                and d1 is not None
                and (d0 - d1) >= debt_delta_material * 2
            )
            if tier_ok or debt_ok:
                improving.append(
                    {
                        "product_id": pid,
                        "signal": "tier_or_debt_improved",
                        "tier_delta": (tr1 - tr0) if tr0 is not None and tr1 is not None else None,
                        "debt_delta": (d0 - d1) if d0 is not None and d1 is not None else None,
                    }
                )

        if len(tiers) >= 2:
            tr0 = _tier_rank(tiers[0].get("readiness_tier"))
            tr1 = _tier_rank(tiers[-1].get("readiness_tier"))
            d0 = _safe_float(debts[0].get("understanding_debt")) if debts else None
            d1 = _safe_float(debts[-1].get("understanding_debt")) if debts else None
            tier_bad = tr0 is not None and tr1 is not None and tr1 < tr0
            debt_bad = (
                d1 is not None
                and d0 is not None
                and (d1 - d0) >= debt_delta_material * 2
            )
            if tier_bad or debt_bad:
                if not any(x["product_id"] == pid for x in improving):
                    cooling.append(
                        {
                            "product_id": pid,
                            "signal": "tier_or_debt_regressed",
                            "tier_delta": (tr1 - tr0) if tr0 is not None and tr1 is not None else None,
                            "debt_delta": (d1 - d0) if d0 is not None and d1 is not None else None,
                        }
                    )

        if len(nas) >= 4:
            vals = [str(x.get("next_action") or "") for x in nas]
            cleaned = [v for v in vals if v and v.lower() not in ("none", "")]
            if len(cleaned) >= 4 and len(set(cleaned)) == 2:
                if all(cleaned[i] != cleaned[i + 1] for i in range(len(cleaned) - 1)):
                    thrashing.append({"product_id": pid, "signal": "next_action_alternation", "pattern": cleaned[-4:]})

        if len(tiers) >= 4:
            trs = [_tier_rank(x.get("readiness_tier")) for x in tiers]
            trs = [t for t in trs if t is not None]
            if len(trs) >= 4 and max(trs) - min(trs) >= 2:
                thrashing.append({"product_id": pid, "signal": "readiness_tier_volatility", "spread": max(trs) - min(trs)})

    def _dedupe(key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[str] = set()
        out: list[dict[str, Any]] = []
        for r in rows:
            pid = str(r.get("product_id") or "")
            if pid and pid not in seen:
                seen.add(pid)
                out.append(r)
        return sorted(out, key=lambda x: str(x.get("product_id")))

    return {
        "rising_priority": _dedupe("r", rising),
        "cooling_down": _dedupe("c", cooling),
        "chronically_blocked": chronic_blocked,
        "steadily_improving": _dedupe("i", improving),
        "thrashing_or_oscillating": _dedupe("t", thrashing),
    }


def evaluate_portfolio_history(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    counts = artifact_counts(root)
    latest_ts = latest_stamp_per_type(root)

    deltas = load_latest_n_artifacts(root, "delta_report", limit=limit_history)
    quiescence_runs = load_latest_n_artifacts(root, "quiescence", limit=limit_history)
    progressions = load_latest_n_artifacts(root, "progression", limit=limit_history)
    interventions = load_latest_n_artifacts(root, "intervention", limit=limit_history)
    cycles = load_latest_n_artifacts(root, "cycle", limit=limit_history)

    aligned, align_notes = align_artifacts_by_cycle(root, limit_cycles=limit_history)

    qm = load_operator_policy(root)["quiescence"]
    ddm = float(qm["debt_delta_material"])
    sdm = float(qm["score_delta_material"])
    merged = _merge_series(
        deltas=deltas,
        quiescence_runs=quiescence_runs,
        progressions=progressions,
        interventions=interventions,
        limit_points=limit_history,
        debt_delta_material=ddm,
        score_delta_material=sdm,
    )

    return {
        "schema": PORTFOLIO_HISTORY_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "inputs": {"limit_history": limit_history},
        "artifact_counts": counts,
        "latest_timestamps": latest_ts,
        "alignment": {
            "cycles": aligned,
            "notes": align_notes,
        },
        "loaded_artifact_counts": {
            "delta_report": len(deltas),
            "quiescence": len(quiescence_runs),
            "progression": len(progressions),
            "intervention": len(interventions),
            "cycle": len(cycles),
            "operator_queue": 1 if _load_operator_queue_latest(root) else 0,
        },
        "per_product_trends": merged["per_product"],
        "trend_summaries": merged["trend_summaries"],
    }


def render_portfolio_history_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio history",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**limit_history:** {payload.get('inputs', {}).get('limit_history')}",
        "",
        "## Artifact inventory",
        "",
        f"**Counts:** `{payload.get('artifact_counts')}`",
        f"**Latest stamps:** `{payload.get('latest_timestamps')}`",
        "",
        "## Trend summaries",
        "",
    ]
    ts = payload.get("trend_summaries") or {}
    for title, key in (
        ("Rising priority (rank ↑ / score ↑)", "rising_priority"),
        ("Cooling down (rank ↓ / score ↓ / regressions)", "cooling_down"),
        ("Chronically blocked (progression)", "chronically_blocked"),
        ("Steadily improving (tier / debt)", "steadily_improving"),
        ("Thrashing / oscillating", "thrashing_or_oscillating"),
    ):
        lines.append(f"### {title}")
        lines.append("")
        rows = ts.get(key) or []
        if not rows:
            lines.append("—")
        else:
            for r in rows[:48]:
                lines.append(f"- `{r.get('product_id')}` — {r}")
        lines.append("")

    notes = (payload.get("alignment") or {}).get("notes") or []
    if notes:
        lines.extend(["## Alignment notes", ""])
        for n in notes:
            lines.append(f"- {n}")
        lines.append("")

    lines.append("See `per_product_trends` in JSON for full slices.")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_history_artifacts(
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
    d = portfolio_history_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_history_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_history(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_history(repo_root, limit_history=limit_history)
    if write_artifacts:
        write_portfolio_history_artifacts(repo_root, payload)
    return payload
