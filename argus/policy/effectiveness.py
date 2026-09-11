"""
Mission-segmented operator policy effectiveness (associative, deterministic).

Reads portfolio outcomes (including mission_interpretation), effective policy snapshot, stamped
outcomes history, and portfolio artifact counts. Does not mutate policy or tune thresholds.
"""

from __future__ import annotations

import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from argus.core.serialize import dumps_json
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.policy.feedback import _load_stamped_outcomes, _policy_snapshot_compact
from argus.policy.operator_policy import load_operator_policy, policy_effective_output_dir
from argus.portfolio.history import artifact_counts, latest_stamp_per_type
from argus.portfolio.outcomes import (
    PORTFOLIO_OUTCOMES_SCHEMA,
    evaluate_portfolio_outcomes,
    portfolio_outcomes_dir,
)

OPERATOR_POLICY_EFFECTIVENESS_SCHEMA = "argus.operator_policy_effectiveness.v1"


def policy_effectiveness_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "policy" / "effectiveness"


def _norm_obj(mi: dict[str, Any] | None) -> str:
    if not isinstance(mi, dict):
        return "unknown"
    o = mi.get("mission_objective")
    s = str(o).strip().lower() if o is not None else ""
    return s if s else "unknown"


def _norm_risk(mi: dict[str, Any] | None) -> str:
    if not isinstance(mi, dict):
        return "unknown"
    r = mi.get("effective_risk_posture")
    s = str(r).strip().lower() if r is not None else ""
    return s if s else "unknown"


def _driver_list(mi: dict[str, Any] | None) -> list[str]:
    if not isinstance(mi, dict):
        return []
    out: list[str] = []
    for x in mi.get("mission_drivers") or []:
        t = str(x).strip().lower()
        if t:
            out.append(t)
    return sorted(set(out))


def _guardrail_list(mi: dict[str, Any] | None) -> list[str]:
    if not isinstance(mi, dict):
        return []
    out: list[str] = []
    for x in mi.get("mission_guardrails") or []:
        t = str(x).strip().lower()
        if t:
            out.append(t)
    return sorted(set(out))


def _guardrail_risk_any(mi: dict[str, Any] | None) -> bool:
    if not isinstance(mi, dict):
        return False
    gr = mi.get("guardrail_risk_signals") or []
    if not isinstance(gr, list):
        return False
    for g in gr:
        if isinstance(g, dict) and (g.get("risk_codes") or []):
            return True
    return False


def _driver_support_any(mi: dict[str, Any] | None) -> bool:
    if not isinstance(mi, dict):
        return False
    ds = mi.get("driver_support_signals") or []
    return isinstance(ds, list) and len(ds) > 0


def _segment_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    if n == 0:
        return {
            "products_n": 0,
            "improvement_rate": None,
            "stagnation_rate": None,
            "negative_rate": None,
            "mixed_rate": None,
            "guardrail_risk_any_rate": None,
            "driver_support_any_rate": None,
            "mission_alignment_positive_rate": None,
            "mission_alignment_negative_rate": None,
            "blocked_cleared_rate": None,
            "blocked_strain_rate": None,
            "intervention_resolved_rate": None,
            "intervention_strain_rate": None,
        }

    def frac(pred: Callable[[dict[str, Any]], bool]) -> float:
        return sum(1 for r in rows if pred(r)) / n

    mi_rows = [r.get("mission_interpretation") for r in rows]
    mi_ok = [m for m in mi_rows if isinstance(m, dict)]

    def mi_frac(pred: Callable[[dict[str, Any]], bool]) -> float:
        return sum(1 for m in mi_ok if pred(m)) / max(len(mi_ok), 1) if mi_ok else 0.0

    blk_strain = frac(
        lambda r: str(r.get("blocked_pattern") or "") in ("persisted", "newly_blocked")
    )
    int_strain = frac(
        lambda r: str(r.get("intervention_pattern") or "") in ("repeated", "newly_flagged")
    )

    return {
        "products_n": n,
        "improvement_rate": frac(lambda r: r.get("overall_trajectory") == "positive"),
        "stagnation_rate": frac(lambda r: r.get("overall_trajectory") == "no_meaningful_movement"),
        "negative_rate": frac(lambda r: r.get("overall_trajectory") == "negative"),
        "mixed_rate": frac(lambda r: r.get("overall_trajectory") == "mixed"),
        "guardrail_risk_any_rate": frac(lambda r: _guardrail_risk_any(r.get("mission_interpretation"))),
        "driver_support_any_rate": frac(lambda r: _driver_support_any(r.get("mission_interpretation"))),
        "mission_alignment_positive_rate": mi_frac(lambda m: str(m.get("mission_alignment")) == "positive"),
        "mission_alignment_negative_rate": mi_frac(lambda m: str(m.get("mission_alignment")) == "negative"),
        "blocked_cleared_rate": frac(lambda r: str(r.get("blocked_pattern") or "") == "cleared"),
        "blocked_strain_rate": blk_strain,
        "intervention_resolved_rate": frac(lambda r: str(r.get("intervention_pattern") or "") == "resolved"),
        "intervention_strain_rate": int_strain,
    }


def _bucket_by(
    rows: list[dict[str, Any]],
    keys_fn: Callable[[dict[str, Any]], list[str]],
) -> dict[str, list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        for k in keys_fn(r) or ["unknown"]:
            buckets.setdefault(k, []).append(r)
    return dict(sorted(buckets.items(), key=lambda x: x[0]))


def _objective_keys(r: dict[str, Any]) -> list[str]:
    mi = r.get("mission_interpretation")
    return [_norm_obj(mi if isinstance(mi, dict) else None)]


def _risk_keys(r: dict[str, Any]) -> list[str]:
    mi = r.get("mission_interpretation")
    return [_norm_risk(mi if isinstance(mi, dict) else None)]


def _driver_keys(r: dict[str, Any]) -> list[str]:
    mi = r.get("mission_interpretation")
    d = _driver_list(mi if isinstance(mi, dict) else None)
    return d if d else ["(no_driver_list)"]


def _guardrail_keys(r: dict[str, Any]) -> list[str]:
    mi = r.get("mission_interpretation")
    g = _guardrail_list(mi if isinstance(mi, dict) else None)
    return g if g else ["(no_guardrail_list)"]


def _reason_code_contrast(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pos = [r for r in rows if r.get("overall_trajectory") == "positive"]
    neg = [r for r in rows if r.get("overall_trajectory") == "negative"]
    if not pos or not neg:
        return {
            "positive_n": len(pos),
            "negative_n": len(neg),
            "more_frequent_with_positive_trajectory": [],
            "more_frequent_with_negative_trajectory": [],
            "note": "need_at_least_one_positive_and_one_negative_product_for_contrast",
        }

    def freq(rows2: list[dict[str, Any]]) -> Counter[str]:
        c: Counter[str] = Counter()
        for r in rows2:
            for code in r.get("reason_codes") or []:
                if isinstance(code, str) and code.strip():
                    c[code.strip()] += 1
        return c

    cp, cn = freq(pos), freq(neg)
    np_, nn = len(pos), len(neg)
    all_codes = sorted(set(cp.keys()) | set(cn.keys()))
    scored: list[tuple[str, float, float, float]] = []
    for code in all_codes:
        fp, fn = cp[code] / np_, cn[code] / nn
        scored.append((code, fp, fn, fp - fn))
    scored.sort(key=lambda x: (-x[3], x[0]))
    top_pos = [
        {"code": t[0], "share_among_positive": round(t[1], 4), "share_among_negative": round(t[2], 4)}
        for t in scored[:8]
        if t[3] > 0
    ]
    scored.sort(key=lambda x: (x[3], x[0]))
    top_neg = [
        {"code": t[0], "share_among_positive": round(t[1], 4), "share_among_negative": round(t[2], 4)}
        for t in scored[:8]
        if t[3] < 0
    ]
    return {
        "positive_n": np_,
        "negative_n": nn,
        "more_frequent_with_positive_trajectory": top_pos,
        "more_frequent_with_negative_trajectory": top_neg,
        "note": "associative_only_not_causal",
    }


def _per_guardrail_risk_frequency(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per guardrail id: how often risk_codes fire among products that list that guardrail."""
    by_g: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        mi = r.get("mission_interpretation")
        if not isinstance(mi, dict):
            continue
        for gid in _guardrail_list(mi):
            by_g.setdefault(gid, []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for gid in sorted(by_g.keys()):
        sub = by_g[gid]
        n = len(sub)
        with_risk = sum(
            1
            for r in sub
            if _guardrail_risk_any(r.get("mission_interpretation") if isinstance(r.get("mission_interpretation"), dict) else None)
        )
        out[gid] = {
            "products_with_guardrail_n": n,
            "guardrail_risk_signal_rate": (with_risk / n) if n else None,
        }
    return out


def _per_driver_support_frequency(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_d: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        mi = r.get("mission_interpretation")
        if not isinstance(mi, dict):
            continue
        for did in _driver_list(mi):
            by_d.setdefault(did, []).append(r)
    out: dict[str, dict[str, Any]] = {}
    for did in sorted(by_d.keys()):
        sub = by_d[did]
        n = len(sub)
        with_sup = sum(
            1
            for r in sub
            if _driver_support_any(r.get("mission_interpretation") if isinstance(r.get("mission_interpretation"), dict) else None)
        )
        out[did] = {
            "products_with_driver_n": n,
            "driver_support_signal_rate": (with_sup / n) if n else None,
        }
    return out


def _notable_patterns(
    by_objective: dict[str, dict[str, Any]],
    by_risk: dict[str, dict[str, Any]],
    per_gr: dict[str, dict[str, Any]],
    products_n: int,
) -> list[str]:
    patterns: list[str] = []
    objs = [o for o, d in by_objective.items() if int(d.get("products_n") or 0) >= 2]
    for i, a in enumerate(objs):
        for b in objs[i + 1 :]:
            sa, sb = by_objective[a], by_objective[b]
            ra, rb = sa.get("improvement_rate"), sb.get("improvement_rate")
            if isinstance(ra, (int, float)) and isinstance(rb, (int, float)) and abs(ra - rb) >= 0.2:
                hi_o, lo_o, hi_v, lo_v = (a, b, ra, rb) if ra >= rb else (b, a, rb, ra)
                patterns.append(
                    f"descriptive: improvement_rate higher for objective={hi_o} ({hi_v:.2f}) than objective={lo_o} ({lo_v:.2f}); associative not causal"
                )
    risks = [r for r, d in by_risk.items() if int(d.get("products_n") or 0) >= 2]
    for i, a in enumerate(risks):
        for b in risks[i + 1 :]:
            sa, sb = by_risk[a], by_risk[b]
            ra, rb = sa.get("stagnation_rate"), sb.get("stagnation_rate")
            if isinstance(ra, (int, float)) and isinstance(rb, (int, float)) and abs(ra - rb) >= 0.2:
                hi_o, lo_o, hi_v, lo_v = (a, b, ra, rb) if ra >= rb else (b, a, rb, ra)
                patterns.append(
                    f"descriptive: stagnation_rate higher for risk_posture={hi_o} ({hi_v:.2f}) than risk_posture={lo_o} ({lo_v:.2f}); associative not causal"
                )
    for gid, gd in per_gr.items():
        rate = gd.get("guardrail_risk_signal_rate")
        if isinstance(rate, (int, float)) and rate >= 0.5 and int(gd.get("products_with_guardrail_n") or 0) >= 1:
            patterns.append(
                f"descriptive: guardrail={gid} shows elevated guardrail_risk_signal_rate ({rate:.2f}) in window; review trajectories"
            )
    if products_n < 3:
        patterns.append("caveat: portfolio has fewer than three evaluated products — segment rates are noisy")
    return sorted(set(patterns))


def _caveats(
    *,
    products_n: int,
    stamped_n: int,
    by_objective: dict[str, dict[str, Any]],
) -> list[str]:
    w: list[str] = []
    if products_n < 2:
        w.append("sparse_products: fewer than two products in outcomes window")
    if stamped_n < 2:
        w.append("sparse_stamped_outcomes: fewer than two stamped portfolio/outcomes snapshots for series context")
    for o, d in by_objective.items():
        if int(d.get("products_n") or 0) == 1:
            w.append(f"sparse_segment: objective={o} has only one product")
    return sorted(set(w))


def _aggregate_outcomes_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = [p for p in (payload.get("per_product_outcomes") or []) if isinstance(p, dict)]
    by_obj = {k: _segment_metrics(v) for k, v in _bucket_by(rows, _objective_keys).items()}
    by_risk = {k: _segment_metrics(v) for k, v in _bucket_by(rows, _risk_keys).items()}
    by_drv = {k: _segment_metrics(v) for k, v in _bucket_by(rows, _driver_keys).items()}
    by_gr_seg = {k: _segment_metrics(v) for k, v in _bucket_by(rows, _guardrail_keys).items()}
    per_gr = _per_guardrail_risk_frequency(rows)
    per_drv_freq = _per_driver_support_frequency(rows)
    return {
        "by_objective": by_obj,
        "by_risk_posture": by_risk,
        "by_driver": by_drv,
        "by_guardrail": by_gr_seg,
        "guardrail_risk_frequency_by_id": per_gr,
        "driver_support_frequency_by_id": per_drv_freq,
        "trajectory_reason_code_contrast": _reason_code_contrast(rows),
    }


def evaluate_operator_policy_effectiveness(
    repo_root: Path,
    *,
    limit_history: int = 50,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    pol = load_operator_policy(root)
    policy_compact = _policy_snapshot_compact(pol)
    mi = pol.get("mission_integration") if isinstance(pol.get("mission_integration"), dict) else {}
    policy_areas = mi.get("policy_areas_touched")

    eff_path = policy_effective_output_dir(root) / "operator_policy_effective.json"
    eff_present = eff_path.is_file()

    current = evaluate_portfolio_outcomes(root, limit_history=lim)
    cur_agg = _aggregate_outcomes_payload(current)
    rows = [p for p in (current.get("per_product_outcomes") or []) if isinstance(p, dict)]
    products_n = len(rows)

    stamped = _load_stamped_outcomes(root, limit=lim)
    stamped_series: list[dict[str, Any]] = []
    for rid, payload in sorted(stamped, key=lambda x: x[0]):
        if str(payload.get("schema") or "") != PORTFOLIO_OUTCOMES_SCHEMA:
            continue
        agg = _aggregate_outcomes_payload(payload)
        bo = agg.get("by_objective") or {}
        stamped_series.append(
            {
                "run_id": rid,
                "evaluated_at_utc": payload.get("evaluated_at_utc"),
                "by_objective_improvement_rate": {
                    k: (v or {}).get("improvement_rate") for k, v in bo.items() if isinstance(v, dict)
                },
            }
        )

    by_obj = cur_agg.get("by_objective") or {}
    by_risk = cur_agg.get("by_risk_posture") or {}
    per_gr = cur_agg.get("guardrail_risk_frequency_by_id") or {}
    if not isinstance(per_gr, dict):
        per_gr = {}

    notable = _notable_patterns(by_obj, by_risk, per_gr, products_n)
    caveats = _caveats(products_n=products_n, stamped_n=len(stamped), by_objective=by_obj)

    return {
        "schema": OPERATOR_POLICY_EFFECTIVENESS_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "notes": [
            "Associations describe co-occurrence of mission segments with outcome metrics under the current policy snapshot — not causal claims.",
            "No automatic tuning or policy mutation is performed.",
        ],
        "inputs": {
            "limit_history": lim,
            "outcomes_run_id": str(current.get("run_id") or ""),
            "stamped_outcomes_loaded": len(stamped),
            "operator_policy_effective_json_present": eff_present,
            "portfolio_artifact_counts": artifact_counts(root),
            "latest_artifact_stamps": latest_stamp_per_type(root),
            "portfolio_outcomes_dir": str(portfolio_outcomes_dir(root)),
        },
        "effective_policy_compact": policy_compact,
        "policy_areas_touched_globally": policy_areas,
        "outcomes_evaluated_at_utc": current.get("evaluated_at_utc"),
        "portfolio_mission_provenance": current.get("portfolio_mission_provenance")
        or build_portfolio_mission_provenance(
            root,
            [str(r.get("product_id") or "").strip() for r in rows if str(r.get("product_id") or "").strip()],
        ),
        "current": {
            "summary": current.get("portfolio_outcome_summary"),
            "thresholds_used": current.get("thresholds_used"),
            **cur_agg,
        },
        "stamped_outcomes_series": stamped_series,
        "notable_patterns": notable,
        "caveats": caveats,
    }


def render_operator_policy_effectiveness_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Operator policy effectiveness (mission-segmented)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    for n in payload.get("notes") or []:
        lines.extend([f"> {n}", ""])
    if payload.get("caveats"):
        lines.append("## Caveats")
        lines.append("")
        for c in payload.get("caveats") or []:
            lines.append(f"- `{c}`")
        lines.append("")
    cur = payload.get("current") or {}
    bo = cur.get("by_objective") or {}
    if bo:
        lines.extend(["## By objective", ""])
        for obj, stats in sorted(bo.items(), key=lambda x: x[0]):
            if not isinstance(stats, dict):
                continue
            lines.append(f"### `{obj}`")
            lines.append("")
            lines.append(
                f"- **products:** {stats.get('products_n')} · **improvement_rate:** {stats.get('improvement_rate')} · "
                f"**stagnation_rate:** {stats.get('stagnation_rate')} · **negative_rate:** {stats.get('negative_rate')}"
            )
            lines.append(
                f"- **guardrail_risk_any_rate:** {stats.get('guardrail_risk_any_rate')} · "
                f"**driver_support_any_rate:** {stats.get('driver_support_any_rate')}"
            )
            lines.append(
                f"- **blocked_cleared_rate:** {stats.get('blocked_cleared_rate')} · "
                f"**intervention_strain_rate:** {stats.get('intervention_strain_rate')}"
            )
            lines.append("")
    br = cur.get("by_risk_posture") or {}
    if br:
        lines.extend(["## By risk posture", ""])
        for k, stats in sorted(br.items(), key=lambda x: x[0]):
            if not isinstance(stats, dict):
                continue
            lines.append(
                f"- **`{k}`** — n={stats.get('products_n')} · improvement {stats.get('improvement_rate')} · "
                f"stagnation {stats.get('stagnation_rate')}"
            )
        lines.append("")
    npat = payload.get("notable_patterns") or []
    if npat:
        lines.extend(["## Notable patterns (descriptive)", ""])
        for p in npat:
            lines.append(f"- {p}")
        lines.append("")
    ts = payload.get("stamped_outcomes_series") or []
    if ts:
        lines.extend(["## Stamped outcomes series (by objective improvement_rate)", ""])
        for row in ts[:12]:
            if isinstance(row, dict):
                lines.append(f"- `{row.get('run_id')}`: {row.get('by_objective_improvement_rate')}")
        if len(ts) > 12:
            lines.append(f"- … ({len(ts) - 12} more snapshots in JSON)")
        lines.append("")
    lines.extend(["## Raw JSON", "", "Use `--json` or `latest.json` for full `by_driver`, `by_guardrail`, and contrasts.", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_operator_policy_effectiveness_artifacts(
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
    d = policy_effectiveness_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_operator_policy_effectiveness_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_operator_policy_effectiveness(
    repo_root: Path,
    *,
    limit_history: int = 50,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_operator_policy_effectiveness(repo_root, limit_history=limit_history)
    if write_artifacts:
        write_operator_policy_effectiveness_artifacts(repo_root, payload)
    return payload
