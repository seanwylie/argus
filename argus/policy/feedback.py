"""
Operator policy outcome feedback — correlate effective policy thresholds with portfolio outcomes over time.

Deterministic: reads stamped ``portfolio/outcomes/*.json``, current ``evaluate_portfolio_outcomes``, and optional
stamped ``portfolio/cycle/*.json`` for cycle recommendations. No network; no auto-tuning.
"""

from __future__ import annotations

import copy
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.policy.operator_policy import load_operator_policy
from argus.portfolio.artifact_index import (
    list_timestamped_portfolio_json_files,
    stamp_run_id_from_path,
)
from argus.portfolio.history import load_latest_n_artifacts
from argus.portfolio.outcomes import (
    PORTFOLIO_OUTCOMES_SCHEMA,
    evaluate_portfolio_outcomes,
    portfolio_outcomes_dir,
)
from argus.products.inventory import build_inventory

OPERATOR_POLICY_FEEDBACK_SCHEMA = "argus.operator_policy_feedback.v1"

PORTFOLIO_CYCLE_SCHEMA = "argus.portfolio_cycle.v1"


def policy_feedback_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "policy" / "feedback"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _policy_snapshot_compact(pol: dict[str, Any]) -> dict[str, Any]:
    """Small stable slice for correlation (not full policy dump)."""
    q = pol.get("quiescence") or {}
    inv = pol.get("intervention") or {}
    return {
        "schema": pol.get("schema"),
        "confidence_low_threshold": pol.get("confidence", {}).get("low_threshold"),
        "quiescence": {
            "debt_delta_material": q.get("debt_delta_material"),
            "confidence_delta_material": q.get("confidence_delta_material"),
            "rank_shift_material": q.get("rank_shift_material"),
            "score_delta_material": q.get("score_delta_material"),
        },
        "intervention": {
            "progression_runs_window": inv.get("progression_runs_window"),
            "delta_reports_window": inv.get("delta_reports_window"),
            "stagnation_min_delta_reports": inv.get("stagnation_min_delta_reports"),
        },
    }


def _rates_from_per_product(per: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(per)
    if n == 0:
        return {
            "products_n": 0,
            "readiness_improvement_rate": None,
            "understanding_debt_reduction_rate": None,
            "blocked_to_unblocked_rate": None,
            "intervention_resolution_rate": None,
            "progression_success_proxy_rate": None,
            "queue_stability_rate": None,
            "queue_churn_rate": None,
            "no_movement_frequency": None,
            "positive_share": None,
            "negative_share": None,
        }

    def frac(pred: Callable[[dict[str, Any]], bool]) -> float:
        return sum(1 for p in per if pred(p)) / n

    prog_rates: list[float] = []
    for p in per:
        pb = p.get("progression_blocked") or {}
        tot = int(pb.get("total_runs") or 0)
        if tot <= 0:
            continue
        blk = int(pb.get("blocked_count") or 0)
        prog_rates.append(1.0 - blk / tot)

    return {
        "products_n": n,
        "readiness_improvement_rate": frac(lambda p: p.get("readiness_trajectory") == "improved"),
        "understanding_debt_reduction_rate": frac(lambda p: p.get("understanding_debt_trajectory") == "decreased"),
        "blocked_to_unblocked_rate": frac(lambda p: p.get("blocked_pattern") == "cleared"),
        "intervention_resolution_rate": frac(lambda p: p.get("intervention_pattern") == "resolved"),
        "progression_success_proxy_rate": sum(prog_rates) / len(prog_rates) if prog_rates else None,
        "queue_stability_rate": frac(lambda p: p.get("queue_rank_trajectory") in ("flat", "improved")),
        "queue_churn_rate": frac(lambda p: p.get("queue_rank_trajectory") in ("worsened",)),
        "no_movement_frequency": frac(lambda p: p.get("overall_trajectory") == "no_meaningful_movement"),
        "positive_share": frac(lambda p: p.get("overall_trajectory") == "positive"),
        "negative_share": frac(lambda p: p.get("overall_trajectory") == "negative"),
    }


def _compact_policy_from_thresholds_used(tu: dict[str, Any]) -> dict[str, Any]:
    return {
        "quiescence": {
            "debt_delta_material": tu.get("debt_delta_material"),
            "confidence_delta_material": tu.get("confidence_delta_material"),
        }
    }


def _debt_materiality_from_point(point: dict[str, Any]) -> float | None:
    ps = point.get("policy_snapshot") or {}
    tu = ps.get("thresholds_used") or {}
    v = tu.get("debt_delta_material")
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _summarize_payload_rates(payload: dict[str, Any]) -> dict[str, Any]:
    per = [p for p in (payload.get("per_product_outcomes") or []) if isinstance(p, dict)]
    rates = _rates_from_per_product(per)
    summ = payload.get("portfolio_outcome_summary") or {}
    n = int(summ.get("products_evaluated") or 0) or rates["products_n"]
    pos = int(summ.get("positive_count") or 0)
    neg = int(summ.get("negative_count") or 0)
    flat = int(summ.get("no_meaningful_movement_count") or 0)
    rates["portfolio_positive_ratio"] = pos / n if n else None
    rates["portfolio_negative_ratio"] = neg / n if n else None
    rates["portfolio_flat_ratio"] = flat / n if n else None
    return rates


def _load_stamped_outcomes(repo_root: Path, *, limit: int) -> list[tuple[str, dict[str, Any]]]:
    d = portfolio_outcomes_dir(repo_root)
    files = list_timestamped_portfolio_json_files(d)[: max(0, limit)]
    out: list[tuple[str, dict[str, Any]]] = []
    for p in files:
        rid = stamp_run_id_from_path(p)
        raw = _load_json(p)
        if raw is not None and str(raw.get("schema") or "") == PORTFOLIO_OUTCOMES_SCHEMA:
            out.append((rid, raw))
    return out


def _cycle_recommendations(repo_root: Path, *, limit: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rid, payload in load_latest_n_artifacts(repo_root, "cycle", limit=limit):
        if str(payload.get("schema") or "") != PORTFOLIO_CYCLE_SCHEMA:
            continue
        summ = payload.get("summary") or {}
        rows.append(
            {
                "run_id": rid,
                "overall_operator_recommendation": str(summ.get("overall_operator_recommendation") or ""),
                "rationale_codes": list(summ.get("overall_rationale_codes") or []),
            }
        )
    return rows


def _trend_summaries(
    metrics_over_time: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(metrics_over_time) < 2:
        return {
            "series_length": len(metrics_over_time),
            "note": "insufficient_points_for_segment_trends",
        }
    first = metrics_over_time[0]
    last = metrics_over_time[-1]
    r0 = first.get("rates") or {}
    r1 = last.get("rates") or {}

    def _delta(key: str) -> dict[str, Any]:
        a, b = r0.get(key), r1.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return {"from": a, "to": b, "delta": round(b - a, 6)}
        return {"from": a, "to": b, "delta": None}

    return {
        "series_length": len(metrics_over_time),
        "first_run_id": first.get("run_id"),
        "last_run_id": last.get("run_id"),
        "readiness_improvement_rate": _delta("readiness_improvement_rate"),
        "understanding_debt_reduction_rate": _delta("understanding_debt_reduction_rate"),
        "no_movement_frequency": _delta("no_movement_frequency"),
        "portfolio_positive_ratio": _delta("portfolio_positive_ratio"),
    }


def _effectiveness_indicators(
    latest_rates: dict[str, Any],
    *,
    products_n: int,
) -> dict[str, Any]:
    pos = latest_rates.get("positive_share")
    neg = latest_rates.get("negative_share")
    flat = latest_rates.get("no_movement_frequency")
    health = None
    if isinstance(pos, (int, float)) and isinstance(neg, (int, float)):
        health = max(0.0, min(100.0, 50.0 + 50.0 * (float(pos) - float(neg))))
    return {
        "products_evaluated": products_n,
        "outcome_health_score_0_100": health,
        "interpretation": (
            "healthy_mixed"
            if health is not None and health >= 55
            else ("stressed" if health is not None and health < 40 else "neutral_or_unknown")
        ),
        "stagnation_pressure": flat,
    }


def _suspicious_correlations(metrics_over_time: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Heuristic flags when threshold moves align with worse headline outcomes."""
    out: list[dict[str, Any]] = []
    if len(metrics_over_time) < 2:
        return out
    for i in range(len(metrics_over_time) - 1):
        a, b = metrics_over_time[i], metrics_over_time[i + 1]
        dda = _debt_materiality_from_point(a)
        ddb = _debt_materiality_from_point(b)
        if dda is None or ddb is None:
            continue
        ra = a.get("rates") or {}
        rb = b.get("rates") or {}
        if ddb > dda * 1.05:
            pr_a = ra.get("portfolio_positive_ratio")
            pr_b = rb.get("portfolio_positive_ratio")
            if isinstance(pr_a, (int, float)) and isinstance(pr_b, (int, float)) and pr_b < pr_a - 0.05:
                out.append(
                    {
                        "code": "feedback.suspicious.higher_debt_materiality_vs_fewer_positive_outcomes",
                        "detail": "debt_delta_material increased between snapshots while positive_ratio fell",
                        "from_run_id": a.get("run_id"),
                        "to_run_id": b.get("run_id"),
                    }
                )
        if ddb < dda * 0.95:
            flat_a = ra.get("no_movement_frequency")
            flat_b = rb.get("no_movement_frequency")
            if (
                isinstance(flat_a, (int, float))
                and isinstance(flat_b, (int, float))
                and flat_b > flat_a + 0.08
            ):
                out.append(
                    {
                        "code": "feedback.suspicious.lower_debt_materiality_vs_more_no_movement",
                        "detail": "debt_delta_material decreased while no_movement_frequency rose",
                        "from_run_id": a.get("run_id"),
                        "to_run_id": b.get("run_id"),
                    }
                )
    return out


def _tuning_opportunities(
    latest_rates: dict[str, Any],
    policy_snap: dict[str, Any],
    *,
    suspicious: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Suggestions only — never applied automatically."""
    tips: list[dict[str, Any]] = []
    flat = latest_rates.get("no_movement_frequency")
    debt_m = (policy_snap.get("quiescence") or {}).get("debt_delta_material")
    if isinstance(flat, (int, float)) and flat > 0.45:
        tips.append(
            {
                "code": "feedback.tune.consider_narrowing_quiescence_materiality",
                "rationale": "High share of products show no meaningful movement; smaller debt deltas may surface progress.",
                "safe_to_try": True,
            }
        )
    if isinstance(debt_m, (int, float)) and debt_m > 0.12:
        tips.append(
            {
                "code": "feedback.tune.review_debt_delta_material",
                "rationale": "Large debt_delta_material can label stagnation as flat; confirm this matches operator intent.",
                "safe_to_try": True,
            }
        )
    for s in suspicious:
        if s.get("code") == "feedback.suspicious.higher_debt_materiality_vs_fewer_positive_outcomes":
            tips.append(
                {
                    "code": "feedback.tune.revisit_threshold_after_correlation",
                    "rationale": "Outcome mix shifted after materiality increased; consider policy experiment before changing config.",
                    "safe_to_try": True,
                }
            )
    return tips


def evaluate_operator_policy_feedback(
    repo_root: Path,
    *,
    limit_history: int = 50,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    pol = load_operator_policy(root)
    policy_snapshot = _policy_snapshot_compact(pol)

    current = evaluate_portfolio_outcomes(root, limit_history=lim)
    current_rates = _summarize_payload_rates(current)
    current_point = {
        "run_id": str(current.get("run_id") or run_id),
        "source": "live_evaluation",
        "evaluated_at_utc": current.get("evaluated_at_utc"),
        "policy_snapshot": {
            "thresholds_used": copy.deepcopy(current.get("thresholds_used") or {}),
            "effective_policy_compact": policy_snapshot,
        },
        "rates": current_rates,
    }

    stamped = _load_stamped_outcomes(root, limit=lim)
    historical_points: list[dict[str, Any]] = []
    for rid, payload in sorted(stamped, key=lambda x: x[0]):
        rates = _summarize_payload_rates(payload)
        tu = payload.get("thresholds_used") or {}
        if not isinstance(tu, dict):
            tu = {}
        historical_points.append(
            {
                "run_id": rid,
                "source": "stamped_portfolio_outcomes",
                "evaluated_at_utc": payload.get("evaluated_at_utc"),
                "policy_snapshot": {
                    "thresholds_used": copy.deepcopy(tu),
                    "effective_policy_compact": _compact_policy_from_thresholds_used(tu),
                },
                "rates": rates,
            }
        )

    # Prefer distinct run_ids: merge stamped + current if current run_id not in stamped
    by_rid: dict[str, dict[str, Any]] = {p["run_id"]: p for p in historical_points}
    if current_point["run_id"] not in by_rid:
        by_rid[current_point["run_id"]] = current_point
    else:
        by_rid[current_point["run_id"]] = current_point

    metrics_over_time = sorted(by_rid.values(), key=lambda x: str(x.get("run_id") or ""))

    cycles = _cycle_recommendations(root, limit=min(lim, 20))

    suspicious = _suspicious_correlations(metrics_over_time)
    latest_rates = current_rates
    products_n = int((current.get("portfolio_outcome_summary") or {}).get("products_evaluated") or 0)

    effectiveness = _effectiveness_indicators(latest_rates, products_n=products_n)
    tuning = _tuning_opportunities(latest_rates, policy_snapshot, suspicious=suspicious)

    sparse = len(metrics_over_time) < 2

    inv = build_inventory(root)
    portfolio_pids = sorted(inv.valid.keys())
    return {
        "schema": OPERATOR_POLICY_FEEDBACK_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "portfolio_mission_provenance": build_portfolio_mission_provenance(root, portfolio_pids),
        "inputs": {
            "limit_history": lim,
            "stamped_outcomes_loaded": len(stamped),
            "cycle_snapshots_considered": len(cycles),
        },
        "current_effective_policy_compact": policy_snapshot,
        "metrics_over_time": metrics_over_time,
        "trend_summaries": _trend_summaries(metrics_over_time),
        "policy_effectiveness_indicators": effectiveness,
        "suspicious_correlations": suspicious,
        "potential_tuning_opportunities": tuning,
        "cycle_operator_recommendations": cycles,
        "sparse_history_warning": sparse,
        "notes": [
            "Correlations are heuristic, not causal. Use argus policy experiment before changing operator_policy.yaml.",
            "Stamped portfolio outcomes carry thresholds_used from each evaluation time; full policy is not snapshotted per run unless you version config separately.",
        ],
    }


def render_operator_policy_feedback_markdown(payload: dict[str, Any]) -> str:
    eff = payload.get("policy_effectiveness_indicators") or {}
    trends = payload.get("trend_summaries") or {}
    lines = [
        "# Operator policy feedback",
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
                "> **Sparse history:** Few stamped `portfolio/outcomes` snapshots — trends are limited. "
                "Run `argus portfolio outcomes` on a steady cadence to build a time series.",
                "",
            ]
        )

    lines.extend(
        [
            "## What improved under current policy",
            "",
        ]
    )
    ts = trends.get("readiness_improvement_rate") or {}
    if ts.get("delta") is not None and isinstance(ts["delta"], (int, float)) and ts["delta"] > 0:
        lines.append(
            f"- Readiness improvement rate moved **up** ({ts.get('from')} → {ts.get('to')}) across saved outcome evaluations."
        )
    else:
        lines.append("- See latest `rates` in JSON for readiness_improvement_rate and portfolio_positive_ratio.")
    lines.extend(["", "## What regressed or stagnated", ""])
    tsf = trends.get("no_movement_frequency") or {}
    if tsf.get("delta") is not None and isinstance(tsf.get("delta"), (int, float)) and tsf["delta"] > 0:
        lines.append(f"- **No-movement** share increased ({tsf.get('from')} → {tsf.get('to')}).")
    else:
        lines.append(
            f"- Latest no_movement_frequency: `{eff.get('stagnation_pressure')}` "
            f"(see per-period `metrics_over_time`)."
        )

    lines.extend(
        [
            "",
            "## Where policy may be too strict or too lenient",
            "",
        ]
    )
    for s in payload.get("suspicious_correlations") or []:
        if isinstance(s, dict):
            lines.append(f"- `{s.get('code')}` — {s.get('detail')}")
    if not payload.get("suspicious_correlations"):
        lines.append("— No heuristic threshold/outcome correlations flagged (or insufficient series).")
    lines.extend(["", "## Safe tuning opportunities (not applied)", ""])
    for t in payload.get("potential_tuning_opportunities") or []:
        if isinstance(t, dict):
            lines.append(f"- **`{t.get('code')}`** — {t.get('rationale')} (safe_to_try={t.get('safe_to_try')})")
    lines.extend(
        [
            "",
            "## Effectiveness snapshot",
            "",
            f"- **Outcome health (0–100):** `{eff.get('outcome_health_score_0_100')}` — `{eff.get('interpretation')}`",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_operator_policy_feedback_artifacts(
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
    d = policy_feedback_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_operator_policy_feedback_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_operator_policy_feedback(
    repo_root: Path,
    *,
    limit_history: int = 50,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_operator_policy_feedback(repo_root, limit_history=limit_history)
    if write_artifacts:
        write_operator_policy_feedback_artifacts(repo_root, payload)
    return payload
