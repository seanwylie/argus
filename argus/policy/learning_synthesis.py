"""
Mission-conditioned operator learning synthesis — cautious, associative lessons from local artifacts.

Deterministic; read-only. Does not tune or mutate operator policy.
"""

from __future__ import annotations

import json
import shutil
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.mission.experiment import MISSION_EXPERIMENT_SCHEMA, mission_experiment_output_dir
from argus.policy.effectiveness import evaluate_operator_policy_effectiveness
from argus.policy.experiment import OPERATOR_POLICY_EXPERIMENT_SCHEMA, policy_experiment_output_dir
from argus.policy.feedback import evaluate_operator_policy_feedback
from argus.policy.recommendations import evaluate_operator_policy_recommendations
from argus.portfolio.patterns import evaluate_portfolio_patterns

OPERATOR_LEARNING_SYNTHESIS_SCHEMA = "argus.operator_learning_synthesis.v1"


def learning_synthesis_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "policy" / "learning_synthesis"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _safe_rate(x: object) -> float | None:
    if x is None:
        return None
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return None


def build_by_objective_lessons(effectiveness: dict[str, Any]) -> list[dict[str, Any]]:
    cur = effectiveness.get("current") or {}
    bo = cur.get("by_objective") or {}
    out: list[dict[str, Any]] = []
    for obj, stats in sorted(bo.items(), key=lambda x: x[0]):
        if not isinstance(stats, dict):
            continue
        n = int(stats.get("products_n") or 0)
        ir = _safe_rate(stats.get("improvement_rate"))
        sr = _safe_rate(stats.get("stagnation_rate"))
        nr = _safe_rate(stats.get("negative_rate"))
        lesson = (
            f"Under mission objective `{obj}` (n={n}), observed improvement_rate={ir}, "
            f"stagnation_rate={sr}, negative_rate={nr} — associative only."
        )
        out.append(
            {
                "mission_objective": obj,
                "products_n": n,
                "improvement_rate": ir,
                "stagnation_rate": sr,
                "negative_rate": nr,
                "mixed_rate": _safe_rate(stats.get("mixed_rate")),
                "lesson_summary": lesson,
                "caveat": "not_causal",
            }
        )
    return out


def build_by_driver_lessons(effectiveness: dict[str, Any]) -> list[dict[str, Any]]:
    cur = effectiveness.get("current") or {}
    by_d = cur.get("by_driver") or {}
    freq = cur.get("driver_support_frequency_by_id") or {}
    out: list[dict[str, Any]] = []
    for drv, stats in sorted(by_d.items(), key=lambda x: x[0]):
        if not isinstance(stats, dict):
            continue
        n = int(stats.get("products_n") or 0)
        ir = _safe_rate(stats.get("improvement_rate"))
        dsr = _safe_rate(stats.get("driver_support_any_rate"))
        fd = freq.get(drv) if isinstance(freq, dict) else None
        fsr = _safe_rate(fd.get("driver_support_signal_rate")) if isinstance(fd, dict) else None
        correlates_support = fsr is not None and fsr >= 0.5 and ir is not None and ir >= 0.25
        lesson = (
            f"Driver segment `{drv}`: n={n}, driver_support_any_rate={dsr}, "
            f"support_signal_rate_among_tagged={fsr}, improvement_rate={ir}."
        )
        out.append(
            {
                "driver_segment": drv,
                "products_n": n,
                "driver_support_any_rate": dsr,
                "support_signal_rate_among_tagged": fsr,
                "improvement_rate": ir,
                "lesson_summary": lesson,
                "correlates_support_signals": correlates_support,
                "caveat": "not_causal",
            }
        )
    return out


def build_by_guardrail_lessons(effectiveness: dict[str, Any]) -> list[dict[str, Any]]:
    cur = effectiveness.get("current") or {}
    by_g = cur.get("by_guardrail") or {}
    freq = cur.get("guardrail_risk_frequency_by_id") or {}
    out: list[dict[str, Any]] = []
    for gr, stats in sorted(by_g.items(), key=lambda x: x[0]):
        if not isinstance(stats, dict):
            continue
        n = int(stats.get("products_n") or 0)
        nr = _safe_rate(stats.get("negative_rate"))
        grisk = _safe_rate(stats.get("guardrail_risk_any_rate"))
        fd = freq.get(gr) if isinstance(freq, dict) else None
        grr = _safe_rate(fd.get("guardrail_risk_signal_rate")) if isinstance(fd, dict) else None
        pressure = grr is not None and grr >= 0.5 and nr is not None and nr >= 0.25
        lesson = (
            f"Guardrail segment `{gr}`: n={n}, guardrail_risk_any_rate={grisk}, "
            f"risk_signal_rate_among_tagged={grr}, negative_trajectory_rate={nr}."
        )
        out.append(
            {
                "guardrail_segment": gr,
                "products_n": n,
                "guardrail_risk_any_rate": grisk,
                "risk_signal_rate_among_tagged": grr,
                "negative_rate": nr,
                "lesson_summary": lesson,
                "correlates_pressure_or_stagnation": pressure,
                "caveat": "not_causal",
            }
        )
    return out


def build_repeated_policy_tuning_signals(recommendations: dict[str, Any]) -> list[dict[str, Any]]:
    recs = [r for r in (recommendations.get("recommendations") or []) if isinstance(r, dict)]
    areas: Counter[str] = Counter()
    for r in recs:
        a = str(r.get("affected_policy_area") or "").strip()
        if a:
            areas[a] += 1
    out: list[dict[str, Any]] = []
    for area, cnt in sorted(areas.items(), key=lambda x: (-x[1], x[0])):
        out.append(
            {
                "affected_policy_area": area,
                "recommendation_count": cnt,
                "note": "Repeated heuristic proposals — review before any config change.",
            }
        )
    return out


def build_mission_experiment_takeaways(repo_root: Path) -> dict[str, Any]:
    p = mission_experiment_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if not raw or str(raw.get("schema") or "") != MISSION_EXPERIMENT_SCHEMA:
        return {
            "artifact_present": False,
            "takeaways": [],
            "variant_kind": None,
            "compared_profiles": [],
        }
    takeaways: list[str] = []
    for x in raw.get("behavior_change_summary") or []:
        if isinstance(x, str) and x.strip():
            takeaways.append(x.strip())
    qdiff = raw.get("queue_top_slice_order_deltas_vs_reference") or []
    for row in qdiff:
        if not isinstance(row, dict):
            continue
        if row.get("top_slice_order_equal") is False:
            profs = row.get("profiles") or []
            takeaways.append(
                f"Mission experiment: queue top-slice order differed between profiles {profs} "
                f"(read-only comparison)."
            )
    rdiff = raw.get("recommendation_deltas_vs_reference") or []
    for row in rdiff:
        if not isinstance(row, dict):
            continue
        o1, o2 = row.get("only_in_first") or [], row.get("only_in_second") or []
        if o1 or o2:
            takeaways.append(
                f"Mission experiment: policy recommendation ids differed vs reference "
                f"(only_in_first={len(o1)}, only_in_second={len(o2)})."
            )
    vk = raw.get("experiment_variant_kind")
    if vk == "compositions" and takeaways:
        takeaways.append(
            "Structured mission compositions (v2) produced measurable read-only differences in operator layers."
        )
    return {
        "artifact_present": True,
        "evaluated_at_utc": raw.get("evaluated_at_utc"),
        "run_id": raw.get("run_id"),
        "variant_kind": vk,
        "compared_profiles": list(raw.get("compared_profiles") or []),
        "takeaways": sorted(set(takeaways)),
    }


def build_policy_experiment_takeaway(repo_root: Path) -> dict[str, Any]:
    p = policy_experiment_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if not raw or str(raw.get("schema") or "") != OPERATOR_POLICY_EXPERIMENT_SCHEMA:
        return {"artifact_present": False, "summary_lines": []}
    lines: list[str] = []
    for row in raw.get("per_profile") or []:
        if not isinstance(row, dict):
            continue
        pid = row.get("profile_id")
        q = row.get("quiescence_recommendation")
        lines.append(f"profile `{pid}`: quiescence_recommendation={q!r}")
    return {
        "artifact_present": True,
        "run_id": raw.get("run_id"),
        "summary_lines": sorted(lines)[:24],
    }


def build_systemic_learning_patterns(
    effectiveness: dict[str, Any],
    patterns: dict[str, Any],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for p in effectiveness.get("notable_patterns") or []:
        if isinstance(p, str) and p.strip():
            out.append({"source": "operator_policy_effectiveness", "pattern": p.strip()})
    for p in patterns.get("detected_patterns") or []:
        if not isinstance(p, dict):
            continue
        out.append(
            {
                "source": "portfolio_patterns",
                "pattern_id": p.get("pattern_id"),
                "title": p.get("title"),
                "severity": p.get("severity"),
                "recommended_systemic_action": p.get("recommended_systemic_action"),
            }
        )
    return out


def build_sparse_signal_warnings(
    effectiveness: dict[str, Any],
    feedback: dict[str, Any],
    recommendations: dict[str, Any],
) -> list[str]:
    w: list[str] = []
    for c in effectiveness.get("caveats") or []:
        if isinstance(c, str) and c.strip():
            w.append(c.strip())
    if feedback.get("sparse_history_warning"):
        w.append("policy_feedback: sparse_history_warning")
    m = feedback.get("metrics_over_time") or []
    if isinstance(m, list) and len(m) < 2:
        w.append("policy_feedback: fewer than two stamped outcome metric points")
    if recommendations.get("sparse_signal_warning"):
        w.append("policy_recommendations: sparse_signal_warning")
    for c in recommendations.get("conflicting_signals") or []:
        if isinstance(c, dict):
            code = c.get("code")
            detail = c.get("detail")
            w.append(f"conflicting_signal:{code}:{detail}")
    return sorted(set(w))


def build_top_lessons_so_far(
    *,
    by_obj: list[dict[str, Any]],
    by_drv: list[dict[str, Any]],
    by_gr: list[dict[str, Any]],
    repeated: list[dict[str, Any]],
    mission_exp: dict[str, Any],
    systemic: list[dict[str, Any]],
    sparse: list[str],
) -> list[str]:
    top: list[str] = []
    for row in by_obj[:6]:
        top.append(str(row.get("lesson_summary") or ""))
    for row in by_drv:
        if row.get("correlates_support_signals"):
            top.append(str(row.get("lesson_summary") or ""))
    for row in by_gr:
        if row.get("correlates_pressure_or_stagnation"):
            top.append(str(row.get("lesson_summary") or ""))
    for r in repeated[:5]:
        top.append(
            f"Policy area `{r.get('affected_policy_area')}` appears in "
            f"{r.get('recommendation_count')} recommendation(s)."
        )
    for t in mission_exp.get("takeaways") or []:
        top.append(str(t))
    for s in systemic[:8]:
        if isinstance(s, dict) and s.get("pattern"):
            top.append(str(s["pattern"]))
        elif isinstance(s, dict) and s.get("title"):
            top.append(f"Systemic pattern: {s.get('title')}")
    if sparse:
        top.append(
            f"Signal quality: {len(sparse)} sparse/conflict warnings — interpret cautiously."
        )
    # Dedupe, stable order
    seen: set[str] = set()
    out: list[str] = []
    for x in top:
        t = str(x).strip()
        if not t or t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out[:24]


def evaluate_operator_learning_synthesis(
    repo_root: Path,
    *,
    limit_history: int = 50,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    effectiveness = evaluate_operator_policy_effectiveness(root, limit_history=lim)
    feedback = evaluate_operator_policy_feedback(root, limit_history=lim)
    patterns = evaluate_portfolio_patterns(root, limit_history=lim, products_dir=products_dir)
    recommendations = evaluate_operator_policy_recommendations(
        root,
        limit_history=lim,
        products_dir=products_dir,
    )
    mission_exp = build_mission_experiment_takeaways(root)
    policy_exp = build_policy_experiment_takeaway(root)

    by_obj = build_by_objective_lessons(effectiveness)
    by_drv = build_by_driver_lessons(effectiveness)
    by_gr = build_by_guardrail_lessons(effectiveness)
    repeated = build_repeated_policy_tuning_signals(recommendations)
    systemic = build_systemic_learning_patterns(effectiveness, patterns)
    sparse = build_sparse_signal_warnings(effectiveness, feedback, recommendations)
    top = build_top_lessons_so_far(
        by_obj=by_obj,
        by_drv=by_drv,
        by_gr=by_gr,
        repeated=repeated,
        mission_exp=mission_exp,
        systemic=systemic,
        sparse=sparse,
    )

    return {
        "schema": OPERATOR_LEARNING_SYNTHESIS_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "notes": [
            "Associative synthesis from stamped artifacts — not causal inference.",
            "Does not modify config/operator_policy.yaml or runtime policy.",
            "Prefer mission experiments and human review before acting on repeated recommendation areas.",
        ],
        "inputs": {
            "limit_history": lim,
            "products_dir": str(products_dir) if products_dir is not None else None,
            "sources": [
                "evaluate_portfolio_outcomes (via effectiveness, feedback, patterns, recommendations)",
                "argus.operator_policy_effectiveness.v1",
                "argus.operator_policy_feedback.v1",
                "argus.portfolio_patterns.v1",
                "argus.operator_policy_recommendations.v1",
                "runs/mission/experiments/latest.json (optional)",
                "runs/policy/experiments/latest.json (optional)",
            ],
        },
        "source_snapshot": {
            "effectiveness_run_id": effectiveness.get("run_id"),
            "feedback_run_id": feedback.get("run_id"),
            "patterns_run_id": patterns.get("run_id"),
            "recommendations_run_id": recommendations.get("run_id"),
        },
        "by_objective_lessons": by_obj,
        "by_driver_lessons": by_drv,
        "by_guardrail_lessons": by_gr,
        "repeated_policy_tuning_signals": repeated,
        "mission_experiment_takeaways": mission_exp,
        "operator_policy_experiment_context": policy_exp,
        "systemic_learning_patterns": systemic,
        "sparse_signal_warnings": sparse,
        "top_lessons_so_far": top,
    }


def render_operator_learning_synthesis_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Operator learning synthesis (mission-conditioned)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    for n in payload.get("notes") or []:
        lines.extend([f"> {n}", ""])
    lines.extend(["## Top lessons (cautious)", ""])
    for t in payload.get("top_lessons_so_far") or []:
        lines.append(f"- {t}")
    lines.extend(["", "## By mission objective", ""])
    for row in payload.get("by_objective_lessons") or []:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('mission_objective')}` — {row.get('lesson_summary')}")
    lines.extend(["", "## Mission experiment takeaways", ""])
    me = payload.get("mission_experiment_takeaways") or {}
    if not me.get("artifact_present"):
        lines.append("— *No `runs/mission/experiments/latest.json` or wrong schema.*")
    else:
        for t in me.get("takeaways") or []:
            lines.append(f"- {t}")
    lines.extend(["", "## Sparse / conflicting signals", ""])
    for w in payload.get("sparse_signal_warnings") or []:
        lines.append(f"- `{w}`")
    lines.append("")
    return "\n".join(lines)


def write_operator_learning_synthesis_artifacts(
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
    d = learning_synthesis_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_operator_learning_synthesis_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_operator_learning_synthesis(
    repo_root: Path,
    *,
    limit_history: int = 50,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_operator_learning_synthesis(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
    )
    if write_artifacts:
        write_operator_learning_synthesis_artifacts(repo_root, payload)
    return payload


__all__ = [
    "OPERATOR_LEARNING_SYNTHESIS_SCHEMA",
    "build_by_driver_lessons",
    "build_by_guardrail_lessons",
    "build_by_objective_lessons",
    "build_mission_experiment_takeaways",
    "build_repeated_policy_tuning_signals",
    "build_sparse_signal_warnings",
    "build_systemic_learning_patterns",
    "build_top_lessons_so_far",
    "evaluate_operator_learning_synthesis",
    "learning_synthesis_output_dir",
    "render_operator_learning_synthesis_markdown",
    "run_operator_learning_synthesis",
    "write_operator_learning_synthesis_artifacts",
]
