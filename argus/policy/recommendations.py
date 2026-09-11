"""
Operator policy recommendations — human-reviewable adjustment proposals from feedback, outcomes, patterns, and intervention trends.

Deterministic heuristics only; never mutates ``config/operator_policy.yaml`` or runtime policy.
"""

from __future__ import annotations

import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.mission import load_mission_registry
from argus.mission.provenance import (
    build_portfolio_mission_provenance,
    portfolio_mission_markdown_lines_from_payload,
)
from argus.policy.effectiveness import evaluate_operator_policy_effectiveness
from argus.policy.feedback import evaluate_operator_policy_feedback
from argus.policy.operator_policy import load_operator_policy
from argus.portfolio.artifact_index import (
    list_timestamped_portfolio_json_files,
    stamp_run_id_from_path,
)
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.patterns import (
    DEFAULT_LIMIT_HISTORY,
    INTERVENTION_HISTORY_FILES,
    evaluate_portfolio_patterns,
)
from argus.products.inventory import build_inventory

OPERATOR_POLICY_RECOMMENDATIONS_SCHEMA = "argus.operator_policy_recommendations.v1"


def policy_recommendations_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "policy" / "recommendations"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _intervention_flag_counts(repo_root: Path, *, limit: int) -> list[tuple[str, int]]:
    d = portfolio_intervention_dir(repo_root)
    files = list_timestamped_portfolio_json_files(d)[: max(0, limit)]
    out: list[tuple[str, int]] = []
    for p in files:
        rid = stamp_run_id_from_path(p)
        raw = _load_json(p)
        if raw is None or str(raw.get("schema") or "") != PORTFOLIO_INTERVENTION_SCHEMA:
            continue
        fp = raw.get("flagged_products") or []
        n = len(fp) if isinstance(fp, list) else 0
        out.append((rid, n))
    return out


def compute_intervention_trend(repo_root: Path, *, limit: int = INTERVENTION_HISTORY_FILES) -> dict[str, Any]:
    """
    Compare average flagged counts in older vs more recent stamped intervention artifacts (chronological list).
    """
    rows = _intervention_flag_counts(repo_root, limit=limit)
    if len(rows) < 4:
        return {
            "series_length": len(rows),
            "trend": "unknown",
            "recent_avg": None,
            "older_avg": None,
            "note": "insufficient_intervention_history",
        }
    counts = [n for _rid, n in rows]
    mid = len(counts) // 2
    older = counts[:mid]
    recent = counts[mid:]
    oa = sum(older) / len(older)
    ra = sum(recent) / len(recent)
    if ra > oa * 1.15:
        tr = "rising"
    elif ra < oa * 0.85:
        tr = "falling"
    else:
        tr = "flat"
    return {
        "series_length": len(rows),
        "trend": tr,
        "recent_avg": round(ra, 4),
        "older_avg": round(oa, 4),
        "first_run_id": rows[0][0],
        "last_run_id": rows[-1][0],
    }


def _latest_rates(feedback: dict[str, Any]) -> dict[str, Any]:
    m = feedback.get("metrics_over_time") or []
    if not m:
        return {}
    return dict(m[-1].get("rates") or {})


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _round_policy_num(x: float) -> float:
    if math.isnan(x) or math.isinf(x):
        return x
    return round(float(x), 4)


def _strength_from_confidence(conf: object) -> str:
    if not isinstance(conf, (int, float)):
        return "low"
    c = float(conf)
    if c >= 0.55:
        return "high"
    if c >= 0.38:
        return "moderate"
    return "low"


def _annotate_recommendation(
    rec: dict[str, Any],
    *,
    mission_scope: str,
    mission_scope_detail: str | None = None,
    applicable_mission_profiles: list[str] | None = None,
    sparse_signal_warning: bool = False,
) -> dict[str, Any]:
    """Add mission-aware fields (additive; preserves existing keys)."""
    out = dict(rec)
    out["mission_scope"] = mission_scope
    if mission_scope_detail:
        out["mission_scope_detail"] = mission_scope_detail
    out["applicable_mission_profiles"] = list(applicable_mission_profiles or [])
    out["recommendation_strength"] = _strength_from_confidence(out.get("confidence"))
    out["sparse_signal_warning"] = bool(sparse_signal_warning)
    return out


def _registry_has_profile(repo_root: Path, profile_id: str) -> bool:
    reg = load_mission_registry(repo_root)
    profs = reg.get("profiles") or {}
    return isinstance(profs, dict) and profile_id in profs


def _profiles_for_segment(repo_root: Path, segment_key: str) -> list[str]:
    """Map objective/driver/guardrail id to registry profile ids when ids align."""
    sk = str(segment_key).strip().lower()
    if not sk or sk.startswith("("):
        return []
    if _registry_has_profile(repo_root, sk):
        return [sk]
    return []


def _mission_aware_recommendations(
    pol: dict[str, Any],
    repo_root: Path | None,
    effectiveness: dict[str, Any] | None,
    *,
    weak_signal: bool,
    products_n: int,
    signal_factor: float,
) -> list[dict[str, Any]]:
    if not effectiveness or repo_root is None:
        return []
    cur = effectiveness.get("current") or {}
    if not isinstance(cur, dict):
        return []
    by_obj = cur.get("by_objective") or {}
    by_drv = cur.get("by_driver") or {}
    by_rp = cur.get("by_risk_posture") or {}
    gr_freq = cur.get("guardrail_risk_frequency_by_id") or {}
    drv_freq = cur.get("driver_support_frequency_by_id") or {}
    caveats = list(effectiveness.get("caveats") or [])
    sparse_eff = any("sparse" in str(c).lower() for c in caveats)

    q = pol.get("quiescence") or {}
    conf = pol.get("confidence") or {}
    inv = pol.get("intervention") or {}

    debt_m = float(q.get("debt_delta_material") or 0.08)
    conf_lt = float(conf.get("low_threshold") or 0.45)
    conf_dm = float(q.get("confidence_delta_material") or 0.1)
    pr_w = int(inv.get("progression_runs_window") or 8)

    out: list[dict[str, Any]] = []

    # Objective: revenue — elevated negative trajectory share
    rev = by_obj.get("revenue") if isinstance(by_obj.get("revenue"), dict) else {}
    rn = int(rev.get("products_n") or 0)
    if rn >= 2:
        nr = rev.get("negative_rate")
        if isinstance(nr, (int, float)) and nr >= 0.35:
            suggested = _round_policy_num(_clamp(conf_lt + 0.02, 0.35, 0.72))
            sw = weak_signal or rn < 3 or sparse_eff
            out.append(
                _annotate_recommendation(
                    {
                        "recommendation_id": "rec.mission.objective.revenue.review_confidence_when_negative_mix",
                        "affected_policy_area": "confidence.low_threshold",
                        "current_value": conf_lt,
                        "suggested_value": suggested,
                        "rationale": (
                            "Mission effectiveness: products with objective=revenue show a high negative trajectory share; "
                            "a slightly stricter low-confidence bar aligns operator advancement with revenue risk posture."
                        ),
                        "expected_effect": "Revenue-mission products may linger in interpret/caution until evidence firms.",
                        "confidence": _round_policy_num(0.41 * signal_factor),
                        "safe_to_try": False,
                    },
                    mission_scope="objective",
                    mission_scope_detail="revenue",
                    applicable_mission_profiles=_profiles_for_segment(repo_root, "revenue"),
                    sparse_signal_warning=sw,
                )
            )

    # Objective: education — stagnation cluster
    edu = by_obj.get("education") if isinstance(by_obj.get("education"), dict) else {}
    en = int(edu.get("products_n") or 0)
    if en >= 2:
        stg = edu.get("stagnation_rate")
        if isinstance(stg, (int, float)) and stg >= 0.4:
            suggested = _round_policy_num(_clamp(conf_dm * 0.92, 0.05, 0.2))
            out.append(
                _annotate_recommendation(
                    {
                        "recommendation_id": "rec.mission.objective.education.tighten_confidence_delta_when_stagnant",
                        "affected_policy_area": "quiescence.confidence_delta_material",
                        "current_value": conf_dm,
                        "suggested_value": suggested,
                        "rationale": (
                            "Mission effectiveness: education-objective products show elevated stagnation (no meaningful movement); "
                            "smaller confidence deltas count as material so learning/clarity moves surface in portfolio views."
                        ),
                        "expected_effect": "More confidence shifts register as material for education-mission rows.",
                        "confidence": _round_policy_num(0.44 * signal_factor),
                        "safe_to_try": False,
                    },
                    mission_scope="objective",
                    mission_scope_detail="education",
                    applicable_mission_profiles=_profiles_for_segment(repo_root, "education"),
                    sparse_signal_warning=weak_signal or en < 3 or sparse_eff,
                )
            )

    # Guardrail pressure — frequent mission guardrail risk codes among products listing a guardrail
    if isinstance(gr_freq, dict):
        for gid, stats in sorted(gr_freq.items()):
            if not isinstance(stats, dict):
                continue
            rate = stats.get("guardrail_risk_signal_rate")
            gn = int(stats.get("products_with_guardrail_n") or 0)
            if gn < 1 or not isinstance(rate, (int, float)) or rate < 0.45:
                continue
            suggested = min(pr_w + 1, 24)
            profs = _profiles_for_segment(repo_root, gid)
            gid_key = "".join(c if c.isalnum() or c in "._-" else "_" for c in str(gid))
            out.append(
                _annotate_recommendation(
                    {
                        "recommendation_id": f"rec.mission.guardrail.{gid_key}.widen_intervention_window_when_risk_elevated",
                        "affected_policy_area": "intervention.progression_runs_window",
                        "current_value": pr_w,
                        "suggested_value": suggested,
                        "rationale": (
                            f"Guardrail `{gid}` shows elevated guardrail-risk frequency in effectiveness data; a slightly longer "
                            "progression window reduces oscillation before intervention escalation for affected products."
                        ),
                        "expected_effect": "More stable evidence spans before chronic-stuck signals for this guardrail cohort.",
                        "confidence": _round_policy_num(0.4 * signal_factor),
                        "safe_to_try": False,
                    },
                    mission_scope="guardrail",
                    mission_scope_detail=gid,
                    applicable_mission_profiles=profs if profs else [gid],
                    sparse_signal_warning=gn < 2 or sparse_eff,
                )
            )

    # Driver support gap — education driver cohort with low driver_support_signal_rate
    d_edu = by_drv.get("education") if isinstance(by_drv.get("education"), dict) else {}
    dsn = int(d_edu.get("products_n") or 0)
    dsf = drv_freq.get("education") if isinstance(drv_freq.get("education"), dict) else {}
    sup = dsf.get("driver_support_signal_rate")
    if dsn >= 2 and isinstance(sup, (int, float)) and sup < 0.35:
        suggested = _round_policy_num(_clamp(debt_m * 0.93, 0.03, 0.25))
        out.append(
            _annotate_recommendation(
                {
                    "recommendation_id": "rec.mission.driver.education.lower_debt_delta_when_support_signals_sparse",
                    "affected_policy_area": "quiescence.debt_delta_material",
                    "current_value": debt_m,
                    "suggested_value": suggested,
                    "rationale": (
                        "Driver-level effectiveness: products declaring the education driver show sparse driver_support_signals vs peers; "
                        "a slightly lower debt delta materiality can surface incremental learning/clarity debt reductions."
                    ),
                    "expected_effect": "Smaller debt moves may read as material for education-driver cohorts.",
                    "confidence": _round_policy_num(0.39 * signal_factor),
                    "safe_to_try": True,
                },
                mission_scope="driver",
                mission_scope_detail="education",
                applicable_mission_profiles=_profiles_for_segment(repo_root, "education"),
                sparse_signal_warning=weak_signal or sparse_eff,
            )
        )

    # Risk posture — conservative vs moderate stagnation contrast
    cons = by_rp.get("conservative") if isinstance(by_rp.get("conservative"), dict) else {}
    mod = by_rp.get("moderate") if isinstance(by_rp.get("moderate"), dict) else {}
    cn = int(cons.get("products_n") or 0)
    mn = int(mod.get("products_n") or 0)
    if cn >= 2 and mn >= 2:
        cs = cons.get("stagnation_rate")
        ms = mod.get("stagnation_rate")
        if isinstance(cs, (int, float)) and isinstance(ms, (int, float)) and (cs - ms) >= 0.12:
            out.append(
                _annotate_recommendation(
                    {
                        "recommendation_id": "rec.mission.risk_posture.conservative_vs_moderate_stagnation_review",
                        "affected_policy_area": "process",
                        "current_value": None,
                        "suggested_value": "argus policy experiment --profile config/operator_policy.yaml",
                        "rationale": (
                            "Risk-posture segments diverge: conservative-tagged products show materially higher stagnation than moderate; "
                            "compare mission-mapped policy profiles read-only before changing global quiescence defaults."
                        ),
                        "expected_effect": "Mission-shaped policy experiments clarify whether conservative posture needs softer materiality.",
                        "confidence": _round_policy_num(0.48 * signal_factor),
                        "safe_to_try": True,
                    },
                    mission_scope="risk_posture",
                    mission_scope_detail="conservative_vs_moderate",
                    applicable_mission_profiles=["education", "revenue", "engagement"],
                    sparse_signal_warning=sparse_eff,
                )
            )

    # Mixed objectives — effectiveness notable_patterns already surfaced divergence; add meta when present
    for pat in effectiveness.get("notable_patterns") or []:
        ps = str(pat)
        if "improvement_rate higher for objective=" in ps and "than objective=" in ps:
            out.append(
                _annotate_recommendation(
                    {
                        "recommendation_id": "rec.mission.mixed.objective_gap_review_via_experiment",
                        "affected_policy_area": "process",
                        "current_value": None,
                        "suggested_value": "argus policy effectiveness && argus policy experiment",
                        "rationale": (
                            "Effectiveness snapshot reports a large improvement-rate gap between objectives; review "
                            "mission-mapped operator_policy profiles rather than a single global tweak."
                        ),
                        "expected_effect": "Per-objective policy experiments isolate levers for each mission cohort.",
                        "confidence": _round_policy_num(0.52 * signal_factor),
                        "safe_to_try": True,
                    },
                    mission_scope="mixed",
                    mission_scope_detail="objective_gap",
                    applicable_mission_profiles=[],
                    sparse_signal_warning=sparse_eff,
                )
            )
            break

    return out


def _detect_conflicting_signals(feedback: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    trends = feedback.get("trend_summaries") or {}
    ri = trends.get("readiness_improvement_rate") or {}
    sus = feedback.get("suspicious_correlations") or []
    d = ri.get("delta")
    if sus and isinstance(d, (int, float)) and d > 0:
        out.append(
            {
                "code": "rec.conflict.improving_readiness_vs_suspicious_correlation",
                "detail": (
                    "Readiness improvement rate increased across saved outcome snapshots, but at least one "
                    "heuristic correlation flagged threshold/outcome tension. Validate with `argus policy experiment`."
                ),
            }
        )
    return out


def _observed_inefficiencies(
    feedback: dict[str, Any],
    patterns: dict[str, Any],
    intervention_trend: dict[str, Any],
    effectiveness: dict[str, Any] | None = None,
) -> list[str]:
    lines: list[str] = []
    if feedback.get("sparse_history_warning"):
        lines.append(
            "Sparse stamped portfolio outcomes history: trend-based recommendations carry limited statistical weight."
        )
    eff = feedback.get("policy_effectiveness_indicators") or {}
    if str(eff.get("interpretation") or "") == "stressed":
        lines.append(
            "Outcome health looks stressed: a large share of products may be negative or the positive/negative mix is skewed."
        )
    flat = eff.get("stagnation_pressure")
    if isinstance(flat, (int, float)) and flat > 0.4:
        lines.append(
            f"High no-movement share ({flat:.2f}): portfolio may look artificially flat under current materiality thresholds."
        )
    for p in patterns.get("detected_patterns") or []:
        if not isinstance(p, dict):
            continue
        if p.get("severity") == "high":
            lines.append(f"Systemic pattern flagged: {p.get('title') or p.get('pattern_id')}")
    if intervention_trend.get("trend") == "rising":
        lines.append(
            "Intervention load (flagged products per stamped run) is rising compared with older runs."
        )
    if effectiveness:
        for c in effectiveness.get("caveats") or []:
            lines.append(f"Mission effectiveness caveat: {c}")
        for n in effectiveness.get("notable_patterns") or []:
            lines.append(f"Mission effectiveness pattern (associative): {n}")
    return lines


def _build_recommendations(
    pol: dict[str, Any],
    feedback: dict[str, Any],
    patterns: dict[str, Any],
    intervention_trend: dict[str, Any],
    *,
    effectiveness: dict[str, Any] | None = None,
    repo_root: Path | None = None,
) -> list[dict[str, Any]]:
    recs: list[dict[str, Any]] = []
    q = pol.get("quiescence") or {}
    conf = pol.get("confidence") or {}
    qs = pol.get("queue_scoring") or {}
    inv = pol.get("intervention") or {}

    debt_m = float(q.get("debt_delta_material") or 0.08)
    conf_lt = float(conf.get("low_threshold") or 0.45)
    conf_dm = float(q.get("confidence_delta_material") or 0.1)
    rank_sm = int(q.get("rank_shift_material") or 2)
    debt_scale = float(qs.get("debt_scale") or 45.0)
    pr_w = int(inv.get("progression_runs_window") or 8)
    stag_min = int(inv.get("stagnation_min_delta_reports") or 3)

    eff = feedback.get("policy_effectiveness_indicators") or {}
    products_n = int(eff.get("products_evaluated") or 0)
    interpretation = str(eff.get("interpretation") or "")
    rates = _latest_rates(feedback)
    flat = rates.get("no_movement_frequency")
    if flat is None:
        flat = eff.get("stagnation_pressure")
    pos_ratio = rates.get("portfolio_positive_ratio")

    weak_signal = products_n < 3
    signal_factor = 0.35 if weak_signal else (0.55 if products_n < 8 else 0.72)

    trends = feedback.get("trend_summaries") or {}
    ri_d = trends.get("readiness_improvement_rate") or {}
    nm_d = trends.get("no_movement_frequency") or {}
    ri_delta = ri_d.get("delta")
    nm_delta = nm_d.get("delta")

    # Quiescence: stagnation → slightly lower debt_delta_material (surfaces more change as material)
    if isinstance(flat, (int, float)) and flat > 0.42:
        suggested = _round_policy_num(_clamp(debt_m * 0.88, 0.03, 0.25))
        recs.append(
            {
                "recommendation_id": "rec.quiescence.debt_delta_material.decrease_for_stagnation",
                "affected_policy_area": "quiescence.debt_delta_material",
                "current_value": debt_m,
                "suggested_value": suggested,
                "rationale": (
                    "High share of products classified as no meaningful movement; a slightly lower debt delta threshold "
                    "can make incremental debt reductions read as material in quiescence/delta."
                ),
                "expected_effect": "More products may appear 'changed' between cycles; less false quiescence.",
                "confidence": _round_policy_num(0.45 + 0.35 * signal_factor),
                "safe_to_try": True,
            }
        )

    # Quiescence: improving trend, flat not worsening → small increase in debt_delta_material (less churn)
    if (
        isinstance(ri_delta, (int, float))
        and ri_delta > 0.03
        and (not isinstance(nm_delta, (int, float)) or nm_delta <= 0.02)
        and isinstance(flat, (int, float))
        and flat < 0.35
    ):
        suggested = _round_policy_num(_clamp(debt_m * 1.08, 0.04, 0.2))
        recs.append(
            {
                "recommendation_id": "rec.quiescence.debt_delta_material.slight_increase_when_improving",
                "affected_policy_area": "quiescence.debt_delta_material",
                "current_value": debt_m,
                "suggested_value": suggested,
                "rationale": (
                    "Readiness improvement trend is positive and no-movement share is not worsening; a modest increase "
                    "reduces noise from tiny debt flicker."
                ),
                "expected_effect": "Fewer immaterial flip-flops flagged as material change.",
                "confidence": _round_policy_num(0.5 * signal_factor),
                "safe_to_try": True,
            }
        )

    # Confidence: stressed portfolio → slightly stricter low_threshold
    if interpretation == "stressed" and products_n >= 3:
        suggested = _round_policy_num(_clamp(conf_lt + 0.03, 0.35, 0.72))
        recs.append(
            {
                "recommendation_id": "rec.confidence.low_threshold.tighten_under_stress",
                "affected_policy_area": "confidence.low_threshold",
                "current_value": conf_lt,
                "suggested_value": suggested,
                "rationale": (
                    "Outcome mix under current policy looks stressed; raising the low-confidence bar slightly can "
                    "reduce advancement on thin evidence (review with portfolio priorities)."
                ),
                "expected_effect": "More products may stay in caution/interpret gaps until confidence rises.",
                "confidence": _round_policy_num(0.38 * signal_factor),
                "safe_to_try": False,
            }
        )

    # Quiescence: confidence_delta_material tweak when pos_ratio very low
    if isinstance(pos_ratio, (int, float)) and pos_ratio < 0.2 and products_n >= 4:
        suggested = _round_policy_num(_clamp(conf_dm * 0.9, 0.05, 0.2))
        recs.append(
            {
                "recommendation_id": "rec.quiescence.confidence_delta_material.tighten_when_negative_mix",
                "affected_policy_area": "quiescence.confidence_delta_material",
                "current_value": conf_dm,
                "suggested_value": suggested,
                "rationale": "Very low positive outcome share: smaller confidence deltas count as material to surface confidence moves.",
                "expected_effect": "More confidence shifts may register as portfolio-material.",
                "confidence": _round_policy_num(0.42 * signal_factor),
                "safe_to_try": False,
            }
        )

    # Queue: high-severity cross-product patterns → modest debt_scale bump
    high_pat = [
        p
        for p in (patterns.get("detected_patterns") or [])
        if isinstance(p, dict) and p.get("severity") == "high"
    ]
    if high_pat:
        suggested = _round_policy_num(_clamp(debt_scale * 1.06, 20.0, 120.0))
        recs.append(
            {
                "recommendation_id": "rec.queue_scoring.debt_scale.increase_under_systemic_pressure",
                "affected_policy_area": "queue_scoring.debt_scale",
                "current_value": debt_scale,
                "suggested_value": suggested,
                "rationale": (
                    f"One or more high-severity systemic patterns detected ({len(high_pat)}); slightly increasing debt "
                    "scale weights understanding debt in queue ranking."
                ),
                "expected_effect": "Higher-debt products rise in the operator queue relative to peers.",
                "confidence": _round_policy_num(0.4 * signal_factor),
                "safe_to_try": False,
            }
        )

    # Intervention: rising flagged trend → widen progression window slightly (fewer chronic false positives)
    if intervention_trend.get("trend") == "rising" and products_n >= 4:
        suggested = min(pr_w + 1, 24)
        recs.append(
            {
                "recommendation_id": "rec.intervention.progression_runs_window.widen_when_load_rises",
                "affected_policy_area": "intervention.progression_runs_window",
                "current_value": pr_w,
                "suggested_value": suggested,
                "rationale": (
                    "Flagged-product counts are rising across recent intervention stamps; a slightly longer window "
                    "reduces chronic oscillation noise before escalation."
                ),
                "expected_effect": "Fewer products flagged as chronically stuck without longer evidence spans.",
                "confidence": _round_policy_num(0.43 * signal_factor),
                "safe_to_try": False,
            }
        )

    # Intervention: falling load → optional sensitivity (lower stagnation threshold)
    if intervention_trend.get("trend") == "falling" and products_n >= 4:
        suggested = max(2, stag_min - 1)
        recs.append(
            {
                "recommendation_id": "rec.intervention.stagnation_min_delta_reports.decrease_when_quiet",
                "affected_policy_area": "intervention.stagnation_min_delta_reports",
                "current_value": stag_min,
                "suggested_value": suggested,
                "rationale": (
                    "Intervention load is falling; you can afford slightly higher sensitivity to stagnation signals."
                ),
                "expected_effect": "Earlier stagnation hints when delta history repeats.",
                "confidence": _round_policy_num(0.48 * signal_factor),
                "safe_to_try": True,
            }
        )

    # Rank shift (integer): only when stagnation extreme
    if isinstance(flat, (int, float)) and flat > 0.55:
        suggested = min(rank_sm + 1, 8)
        recs.append(
            {
                "recommendation_id": "rec.quiescence.rank_shift_material.increase_under_extreme_flat",
                "affected_policy_area": "quiescence.rank_shift_material",
                "current_value": rank_sm,
                "suggested_value": suggested,
                "rationale": "Extreme flat outcomes: require larger queue rank movement before treating rank change as material.",
                "expected_effect": "Less rank noise in quiescence classification.",
                "confidence": _round_policy_num(0.4 * signal_factor),
                "safe_to_try": True,
            }
        )

    suspicious = feedback.get("suspicious_correlations") or []
    if suspicious:
        recs.append(
            {
                "recommendation_id": "rec.meta.run_policy_experiment_before_yaml_change",
                "affected_policy_area": "process",
                "current_value": None,
                "suggested_value": "argus policy experiment --profile config/operator_policy.yaml",
                "rationale": (
                    "Heuristic correlations between threshold snapshots and outcome mix were flagged; compare profiles "
                    "read-only before editing operator_policy.yaml."
                ),
                "expected_effect": "Predictable queue/quiescence/intervention diffs across profiles without persisting change.",
                "confidence": 0.75,
                "safe_to_try": True,
            }
        )

    conflicts = _detect_conflicting_signals(feedback)
    if conflicts:
        recs.append(
            {
                "recommendation_id": "rec.meta.low_confidence_review_conflicting_signals",
                "affected_policy_area": "process",
                "current_value": None,
                "suggested_value": "manual_review",
                "rationale": conflicts[0].get("detail", "Conflicting signals between trends and correlation heuristics."),
                "expected_effect": "Human judgment before applying numeric tweaks.",
                "confidence": 0.35,
                "safe_to_try": True,
            }
        )

    recs.extend(
        _mission_aware_recommendations(
            pol,
            repo_root,
            effectiveness,
            weak_signal=weak_signal,
            products_n=products_n,
            signal_factor=signal_factor,
        )
    )

    if weak_signal:
        for r in recs:
            if isinstance(r.get("confidence"), (int, float)):
                r["confidence"] = _round_policy_num(min(0.55, float(r["confidence"]) * 0.75))

    # De-duplicate by recommendation_id (keep first)
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for r in recs:
        rid = str(r.get("recommendation_id") or "")
        if rid in seen:
            continue
        seen.add(rid)
        deduped.append(r)

    finalized: list[dict[str, Any]] = []
    for r in deduped:
        row = dict(r)
        if "mission_scope" not in row:
            row = _annotate_recommendation(
                row,
                mission_scope="portfolio_wide",
                sparse_signal_warning=weak_signal,
            )
        row["recommendation_strength"] = _strength_from_confidence(row.get("confidence"))
        row["sparse_signal_warning"] = bool(row.get("sparse_signal_warning") or weak_signal)
        finalized.append(row)
    return finalized


def evaluate_operator_policy_recommendations(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    pol = load_operator_policy(root)
    feedback = evaluate_operator_policy_feedback(root, limit_history=lim)
    effectiveness = evaluate_operator_policy_effectiveness(root, limit_history=lim)
    patterns = evaluate_portfolio_patterns(root, limit_history=lim, products_dir=products_dir)
    iv_trend = compute_intervention_trend(root, limit=INTERVENTION_HISTORY_FILES)

    conflicts = _detect_conflicting_signals(feedback)
    ineff = _observed_inefficiencies(feedback, patterns, iv_trend, effectiveness=effectiveness)
    recs = _build_recommendations(
        pol,
        feedback,
        patterns,
        iv_trend,
        effectiveness=effectiveness,
        repo_root=root,
    )

    inv = build_inventory(root, products_dir=products_dir)
    portfolio_pids = sorted(inv.valid.keys())
    sparse_warn = bool(feedback.get("sparse_history_warning")) or any(
        "sparse" in str(c).lower() for c in (effectiveness.get("caveats") or [])
    )
    return {
        "schema": OPERATOR_POLICY_RECOMMENDATIONS_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "portfolio_mission_provenance": build_portfolio_mission_provenance(root, portfolio_pids),
        "inputs": {
            "limit_history": lim,
            "products_dir": str(products_dir) if products_dir is not None else None,
            "sources": [
                "argus.operator_policy_feedback.v1",
                "argus.operator_policy_effectiveness.v1",
                "argus.portfolio_outcomes (via feedback, effectiveness & patterns)",
                "argus.portfolio_patterns.v1",
                "intervention stamped history (local)",
            ],
        },
        "sparse_signal_warning": sparse_warn,
        "source_snapshot": {
            "policy_feedback_run_id": feedback.get("run_id"),
            "policy_effectiveness_run_id": effectiveness.get("run_id"),
            "portfolio_patterns_run_id": patterns.get("run_id"),
            "intervention_trend": iv_trend,
        },
        "recommendations": recs,
        "observed_inefficiencies": ineff,
        "conflicting_signals": conflicts,
        "notes": [
            "Recommendations are heuristic proposals only; they do not modify operator policy.",
            "Mission-scoped rows use `argus policy effectiveness` segments plus mission interpretation — associative, not causal.",
            "Prefer `argus policy experiment` before changing config/operator_policy.yaml.",
        ],
    }


def _rec_md_lines(r: dict[str, Any]) -> list[str]:
    ms = r.get("mission_scope") or "portfolio_wide"
    msd = r.get("mission_scope_detail")
    profs = r.get("applicable_mission_profiles") or []
    strength = r.get("recommendation_strength") or "—"
    sp = r.get("sparse_signal_warning")
    scope_bits = [f"`{ms}`"]
    if msd:
        scope_bits.append(f"detail=`{msd}`")
    if profs:
        scope_bits.append(f"profiles={', '.join(f'`{p}`' for p in profs)}")
    head = f"- **`{r.get('recommendation_id')}`** (`{r.get('affected_policy_area')}`) — scope: {' · '.join(scope_bits)}"
    if sp:
        head += " — **sparse signal**"
    return [
        head,
        f"  - **Strength:** `{strength}`",
        f"  - **Current → suggested:** `{r.get('current_value')}` → `{r.get('suggested_value')}`",
        f"  - **Rationale:** {r.get('rationale')}",
        f"  - **Expected effect:** {r.get('expected_effect')} — confidence `{r.get('confidence')}`",
    ]


def render_operator_policy_recommendations_markdown(payload: dict[str, Any]) -> str:
    recs = [r for r in (payload.get("recommendations") or []) if isinstance(r, dict)]
    safe = [r for r in recs if r.get("safe_to_try") is True]
    risky = [r for r in recs if r.get("safe_to_try") is False]

    lines = [
        "# Operator policy recommendations",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    lines.extend(portfolio_mission_markdown_lines_from_payload(payload))
    if payload.get("sparse_signal_warning"):
        lines.extend(
            [
                "> **Sparse signal:** Stamped outcomes history and/or mission effectiveness caveats suggest limited "
                "statistical weight — treat mission-scoped rows as advisory.",
                "",
            ]
        )
    lines.extend(
        [
            "## Safe adjustments",
            "",
        ]
    )
    if not safe:
        lines.append("— *None — see risky/process items or observed inefficiencies below.*")
    else:
        for r in safe:
            lines.extend(_rec_md_lines(r))
            lines.append("")

    lines.extend(["", "## High-impact but risky adjustments", ""])
    if not risky:
        lines.append("— *None generated from current signals.*")
    else:
        for r in risky:
            lines.extend(_rec_md_lines(r))
            lines.append("")

    lines.extend(["", "## Observed inefficiencies", ""])
    obs = payload.get("observed_inefficiencies") or []
    if not obs:
        lines.append("— *No extra inefficiency bullets beyond headline metrics.*")
    else:
        for o in obs:
            lines.append(f"- {o}")

    cs = payload.get("conflicting_signals") or []
    if cs:
        lines.extend(["", "## Conflicting signals", ""])
        for c in cs:
            if isinstance(c, dict):
                lines.append(f"- **`{c.get('code')}`** — {c.get('detail')}")

    lines.extend(["", "## Notes", ""])
    for n in payload.get("notes") or []:
        lines.append(f"- {n}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_operator_policy_recommendations_artifacts(
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
    d = policy_recommendations_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_operator_policy_recommendations_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_operator_policy_recommendations(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_operator_policy_recommendations(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
    )
    if write_artifacts:
        write_operator_policy_recommendations_artifacts(repo_root, payload)
    return payload
