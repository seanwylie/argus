"""
Canonical operator policy (``argus.operator_policy.v1``): tunable thresholds and weights.

Defaults match pre-policy hardcoded constants. Optional override: ``config/operator_policy.yaml``.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

from argus.core.serialize import dumps_json
from argus.mission.mission import (
    load_mission_by_id,
    mission_experiment_cache_token,
    resolve_global_mission,
    resolve_product_mission,
)
from argus.policy.mission_mapping import (
    apply_mission_profile_to_operator_policy,
    apply_structured_mission_to_operator_policy,
)

OPERATOR_POLICY_SCHEMA = "argus.operator_policy.v1"

# Cached effective policy: (root, product_id, experiment_token) -> (mtime, mission fingerprint) -> merged policy
_POLICY_CACHE: dict[
    tuple[str, str | None, tuple[Any, ...]], tuple[tuple[float, tuple[Any, ...]], dict[str, Any]]
] = {}


def default_operator_policy() -> dict[str, Any]:
    """Frozen default policy (matches historical Argus behavior before this module)."""
    return {
        "schema": OPERATOR_POLICY_SCHEMA,
        "confidence": {
            "low_threshold": 0.45,
        },
        "readiness": {
            "debt_increments": {
                "no_import_state": 0.28,
                "first_pass_pending": 0.22,
                "first_pass_failed": 0.35,
                "first_pass_partial": 0.28,
                "first_pass_skipped": 0.2,
                "first_pass_unknown": 0.15,
                "signals_absent_or_stale": 0.18,
                "spine_incomplete": 0.15,
                "audit_gap_or_stub": 0.1,
                "temporal_freshness_stale": 0.08,
                "decision_confidence_low": 0.07,
                "advisor_conflict": 0.05,
                "import_waiting": 0.1,
                "waiting_per_item_unit": 0.02,
                "waiting_debt_cap": 0.12,
            },
            "gate_debt_caution": 0.55,
            "policy_hint_debt_stabilize": 0.45,
        },
        "queue_scoring": {
            "weights_version": "1",
            "tier_points": {
                "unprofiled": 50.0,
                "import_incomplete": 58.0,
                "observe_gap": 48.0,
                "interpret_gap": 38.0,
                "advance_ready": 22.0,
            },
            "tier_unknown_points": 35.0,
            "debt_scale": 45.0,
            "first_pass_points": {
                "failed": 28.0,
                "partial": 22.0,
                "skipped": 16.0,
                "pending": 18.0,
                "success": 0.0,
            },
            "first_pass_missing_points": 12.0,
            "points_signals_stale_bundle": 22.0,
            "points_signals_phase_absent": 20.0,
            "points_flag_signals_collection_stale": 18.0,
            "points_flag_signals_refresh": 18.0,
            "points_flag_temporal_stale": 14.0,
            "points_waiting_inputs": 32.0,
            "points_blocked_waiting_status": 36.0,
            "points_blockers": 24.0,
            "points_low_decision_confidence": 18.0,
            "points_missing_decision_confidence": 10.0,
            "family_points": {
                "refinement": 26.0,
                "observability": 20.0,
                "governance": 18.0,
                "generation_chain": 14.0,
                "experiments": 12.0,
                "none": 8.0,
            },
            "family_none_non_advance_extra": 8.0,
            "lifecycle_points": {
                "idea": 4.0,
                "build": 6.0,
                "validate": 6.0,
                "grow": 8.0,
                "maintain": 8.0,
                "decline": 2.0,
                "kill": 2.0,
            },
            "lifecycle_unknown_points": 4.0,
            "points_no_operator_input": 30.0,
        },
        "quiescence": {
            "debt_delta_material": 0.08,
            "confidence_delta_material": 0.10,
            "rank_shift_material": 2,
            "score_delta_material": 15.0,
        },
        "intervention": {
            "progression_runs_window": 8,
            "delta_reports_window": 6,
            "min_consecutive_blocked_outcomes": 2,
            "min_total_blocked_outcomes": 3,
            "oscillation_min_runs": 4,
            "stagnation_min_delta_reports": 3,
            "top_queue_rank_cutoff": 3,
            "high_priority_score": 75.0,
            "chronic_min_runs_spanned": 4,
            "emerging_max_runs_spanned": 2,
        },
        "cycle": {
            "benign_intervention_categories": ["safe_to_ignore", "continue_monitoring"],
            "delta_inspect_paths": ["review_blockers", "review_regressions", "establish_baseline"],
        },
        "next_action_policy": {
            "note": "Precedence rules are implemented in argus.orchestrator.next_action_policy.resolve_next_action; no numeric tuning here.",
        },
    }


def _deep_merge(base: Any, override: Any) -> Any:
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            if k in out and isinstance(out[k], dict) and isinstance(v, dict):
                out[k] = _deep_merge(out[k], v)
            else:
                out[k] = copy.deepcopy(v)
        return out
    return copy.deepcopy(override)


def validate_operator_policy(p: dict[str, Any]) -> None:
    if p.get("schema") != OPERATOR_POLICY_SCHEMA:
        raise ValueError(f"operator policy schema must be {OPERATOR_POLICY_SCHEMA!r}")
    conf = p.get("confidence") or {}
    lt = conf.get("low_threshold")
    if not isinstance(lt, (int, float)) or not (0.0 < float(lt) < 1.0):
        raise ValueError("confidence.low_threshold must be a float in (0,1)")

    r = p.get("readiness") or {}
    di = r.get("debt_increments") or {}
    for k, v in di.items():
        if not isinstance(v, (int, float)) or float(v) < 0 or float(v) > 1.0:
            raise ValueError(f"readiness.debt_increments.{k} must be a number in [0,1]")
    for key in ("gate_debt_caution", "policy_hint_debt_stabilize"):
        x = r.get(key)
        if not isinstance(x, (int, float)) or not (0.0 <= float(x) <= 1.0):
            raise ValueError(f"readiness.{key} must be a float in [0,1]")

    qs = p.get("queue_scoring") or {}
    wv = qs.get("weights_version")
    if not isinstance(wv, str) or not wv.strip():
        raise ValueError("queue_scoring.weights_version must be a non-empty string")
    for name in (
        "debt_scale",
        "tier_unknown_points",
        "first_pass_missing_points",
        "points_no_operator_input",
    ):
        x = qs.get(name)
        if not isinstance(x, (int, float)) or float(x) < 0:
            raise ValueError(f"queue_scoring.{name} must be a non-negative number")
    tp = qs.get("tier_points") or {}
    if not isinstance(tp, dict) or not tp:
        raise ValueError("queue_scoring.tier_points must be a non-empty object")

    qn = p.get("quiescence") or {}
    for key in ("debt_delta_material", "confidence_delta_material", "score_delta_material"):
        x = qn.get(key)
        if not isinstance(x, (int, float)) or float(x) <= 0:
            raise ValueError(f"quiescence.{key} must be a positive float")
    rs = qn.get("rank_shift_material")
    if not isinstance(rs, int) or rs < 1:
        raise ValueError("quiescence.rank_shift_material must be an int >= 1")

    inv = p.get("intervention") or {}
    for key in (
        "progression_runs_window",
        "delta_reports_window",
        "min_consecutive_blocked_outcomes",
        "min_total_blocked_outcomes",
        "oscillation_min_runs",
        "stagnation_min_delta_reports",
        "top_queue_rank_cutoff",
        "chronic_min_runs_spanned",
        "emerging_max_runs_spanned",
    ):
        x = inv.get(key)
        if not isinstance(x, int) or x < 1:
            raise ValueError(f"intervention.{key} must be an int >= 1")
    hps = inv.get("high_priority_score")
    if not isinstance(hps, (int, float)) or float(hps) <= 0:
        raise ValueError("intervention.high_priority_score must be a positive number")

    cyc = p.get("cycle") or {}
    bic = cyc.get("benign_intervention_categories")
    if not isinstance(bic, list) or not all(isinstance(x, str) and x.strip() for x in bic):
        raise ValueError("cycle.benign_intervention_categories must be a list of non-empty strings")
    dip = cyc.get("delta_inspect_paths")
    if not isinstance(dip, list) or not dip:
        raise ValueError("cycle.delta_inspect_paths must be a non-empty list of strings")


def _mission_fingerprint(repo_root: Path, product_id: str | None = None) -> tuple[Any, ...]:
    """Invalidate policy cache when mission resolution inputs change."""
    root = repo_root.resolve()
    env = os.environ.get("ARGUS_MISSION_ID") or ""
    mreg = root / "config" / "mission_profiles.yaml"
    cur = root / "runs" / "mission" / "current.json"
    mt_reg = mreg.stat().st_mtime if mreg.is_file() else -1.0
    mt_cur = cur.stat().st_mtime if cur.is_file() else -1.0
    base: tuple[Any, ...] = (env, mt_reg, mt_cur)
    if not product_id:
        return base
    py = root / "products" / product_id / "product.yaml"
    mt_py = py.stat().st_mtime if py.is_file() else -1.0
    return base + (str(product_id), mt_py)


def _merge_yaml_only(repo_root: Path) -> dict[str, Any]:
    """Defaults merged with ``config/operator_policy.yaml`` (no mission layer)."""
    root = repo_root.resolve()
    cfg = root / "config" / "operator_policy.yaml"
    base = default_operator_policy()
    if cfg.is_file():
        try:
            raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as e:
            raise ValueError(f"Failed to read {cfg}: {e}") from e
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError(f"{cfg} must parse to a mapping at the top level")
        merged = _deep_merge(base, raw)
        validate_operator_policy(merged)
        return merged
    validate_operator_policy(base)
    return copy.deepcopy(base)


def load_operator_policy(repo_root: Path, product_id: str | None = None) -> dict[str, Any]:
    """
    Load effective operator policy: defaults merged with ``config/operator_policy.yaml`` when present,
    then bounded mission adjustments from :mod:`argus.policy.mission_mapping` (see ``mission_integration``).

    When ``product_id`` is set, uses :func:`argus.mission.mission.resolve_product_mission` only —
    **global mission does not merge into policy** when the product declares ``mission_id`` in
    ``product.yaml``. When the product has no ``mission_id``, repository fallback (env →
    ``current.json`` → default) applies via the same resolver.

    When ``product_id`` is omitted, uses :func:`argus.mission.mission.resolve_global_mission` for
    portfolio-wide consumers (materiality baselines, stamped policy dumps) with no per-product scope.

    Cached per (repo root, product_id) until operator policy file mtime or mission resolution inputs change.
    Truth-producing layers (signals, audit, findings) do not call this module.
    """
    root = repo_root.resolve()
    cfg = root / "config" / "operator_policy.yaml"
    mtime = cfg.stat().st_mtime if cfg.is_file() else -1.0
    mf = _mission_fingerprint(root, product_id)
    tok = mission_experiment_cache_token()
    cache_key = (str(root), str(product_id) if product_id else None, tok)
    hit = _POLICY_CACHE.get(cache_key)
    if hit is not None and hit[0] == (mtime, mf):
        return copy.deepcopy(hit[1])

    merged = _merge_yaml_only(root)
    if product_id:
        eff = resolve_product_mission(root, str(product_id).strip())
        sm = eff.get("structured_mission")
        if isinstance(sm, dict) and sm.get("objective"):
            obj = load_mission_by_id(root, str(sm["objective"]))
            drivers = [load_mission_by_id(root, str(x)) for x in (sm.get("drivers") or [])]
            guardrails = [load_mission_by_id(root, str(x)) for x in (sm.get("guardrails") or [])]
            rp = str(
                sm.get("effective_risk_posture") or sm.get("risk_posture") or obj.get("risk_posture") or "moderate"
            ).strip().lower()
            pol, _block = apply_structured_mission_to_operator_policy(
                merged,
                objective_profile=obj,
                driver_profiles=drivers,
                guardrail_profiles=guardrails,
                resolved_mission_id=str(eff.get("resolved_mission_id") or ""),
                resolution_chain=list(eff.get("resolution_chain") or []),
                effective_risk_posture=rp,
            )
        else:
            mission_prof = eff.get("mission") or {}
            pol, _block = apply_mission_profile_to_operator_policy(
                merged,
                mission_profile=mission_prof if isinstance(mission_prof, dict) else {},
                resolved_mission_id=str(eff.get("resolved_mission_id") or ""),
                resolution_chain=list(eff.get("resolution_chain") or []),
            )
    else:
        eff = resolve_global_mission(root)
        mission_prof = eff.get("mission") or {}
        pol, _block = apply_mission_profile_to_operator_policy(
            merged,
            mission_profile=mission_prof if isinstance(mission_prof, dict) else {},
            resolved_mission_id=str(eff.get("resolved_mission_id") or ""),
            resolution_chain=list(eff.get("resolution_chain") or []),
        )
    mi = pol.get("mission_integration")
    if isinstance(mi, dict):
        mi["product_id"] = str(product_id).strip() if product_id else None
        rs = eff.get("resolution_scope")
        if rs:
            mi["resolution_scope"] = rs
    validate_operator_policy(pol)
    _POLICY_CACHE[cache_key] = ((mtime, mf), pol)
    return copy.deepcopy(pol)


def clear_operator_policy_cache() -> None:
    """Test helper: invalidate cached policies."""
    _POLICY_CACHE.clear()


def policy_effective_output_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "policy"


def write_operator_policy_effective_artifact(repo_root: Path) -> Path:
    """Write ``runs/policy/operator_policy_effective.json`` with the merged effective policy."""
    root = repo_root.resolve()
    pol = load_operator_policy(root)
    d = policy_effective_output_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "operator_policy_effective.json"
    p.write_text(dumps_json(pol) + "\n", encoding="utf-8")
    return p


def render_operator_policy_markdown(pol: dict[str, Any]) -> str:
    mi = pol.get("mission_integration") or {}
    lines = [
        "# Effective operator policy",
        "",
        f"**Schema:** `{pol.get('schema')}`",
        "",
        "This file reflects defaults merged with `config/operator_policy.yaml` when present, "
        "then bounded adjustments from the active mission profile (see `mission_integration` in JSON).",
        "",
        "## Mission context",
        "",
        f"- **resolved_mission_id:** `{mi.get('resolved_mission_id')}`",
        f"- **risk_posture:** `{mi.get('risk_posture')}`",
        f"- **adjustments count:** {len(mi.get('adjustments_applied') or [])}",
        f"- **policy areas touched:** `{mi.get('policy_areas_touched')}`",
        "",
        "## Confidence",
        "",
        f"- **low_threshold:** `{pol.get('confidence', {}).get('low_threshold')}`",
        "",
        "## Quiescence materiality",
        "",
    ]
    qn = pol.get("quiescence") or {}
    lines.append(f"- **debt_delta_material:** `{qn.get('debt_delta_material')}`")
    lines.append(f"- **confidence_delta_material:** `{qn.get('confidence_delta_material')}`")
    lines.append(f"- **rank_shift_material:** `{qn.get('rank_shift_material')}`")
    lines.append(f"- **score_delta_material:** `{qn.get('score_delta_material')}`")
    lines.extend(["", "## Intervention windows", ""])
    inv = pol.get("intervention") or {}
    for k in sorted(inv.keys()):
        lines.append(f"- **{k}:** `{inv.get(k)}`")
    lines.extend(["", "## Cycle synthesis", ""])
    cyc = pol.get("cycle") or {}
    lines.append(
        f"- **benign_intervention_categories:** {cyc.get('benign_intervention_categories')}"
    )
    lines.append("")
    return "\n".join(lines)


def write_operator_policy_effective_markdown(repo_root: Path) -> Path:
    pol = load_operator_policy(repo_root)
    d = policy_effective_output_dir(repo_root.resolve())
    d.mkdir(parents=True, exist_ok=True)
    p = d / "operator_policy_effective.md"
    p.write_text(render_operator_policy_markdown(pol), encoding="utf-8")
    return p
