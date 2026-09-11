"""
Bounded mission → operator policy adjustments.

Truth-producing layers (signals, audit, findings) do not use this module; only policy consumers
see the merged :func:`argus.policy.operator_policy.load_operator_policy` output.
"""

from __future__ import annotations

import copy
from typing import Any

OPERATOR_POLICY_MISSION_CONTEXT_SCHEMA = "argus.operator_policy_mission_context.v1"

# Structured product mission: objective at full strength; drivers lighter; guardrails constraint-only.
DRIVER_OVERLAY_STRENGTH = 0.45


def _record(
    adjustments: list[dict[str, Any]],
    *,
    area: str,
    field: str,
    old: Any,
    new: Any,
    reason: str,
) -> None:
    if old == new:
        return
    adjustments.append(
        {
            "area": area,
            "field": field,
            "from": old,
            "to": new,
            "reason": reason,
        }
    )


def _clamp_float(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def _apply_education_mission(
    p: dict[str, Any], adjustments: list[dict[str, Any]], *, strength: float = 1.0
) -> None:
    s = max(0.0, min(1.0, float(strength)))
    if s <= 0.0:
        return
    tag = f"mission:education[strength={s}]"
    conf = p.setdefault("confidence", {})
    old_lt = float(conf.get("low_threshold") or 0.45)
    target_lt = _clamp_float(old_lt + 0.02, 0.35, 0.58)
    new_lt = old_lt + (target_lt - old_lt) * s
    conf["low_threshold"] = new_lt
    _record(adjustments, area="confidence", field="low_threshold", old=old_lt, new=new_lt, reason=tag)

    qs = p.setdefault("queue_scoring", {})
    fam = qs.setdefault("family_points", {})
    for key, factor in (("experiments", 0.88), ("governance", 1.06)):
        old = float(fam.get(key) or 0.0)
        target = round(old * factor, 4)
        new = old + (target - old) * s
        fam[key] = new
        _record(
            adjustments,
            area="queue_scoring.family_points",
            field=key,
            old=old,
            new=new,
            reason=f"{tag}_experimentation_balance",
        )

    qn = p.setdefault("quiescence", {})
    old_d = float(qn.get("debt_delta_material") or 0.08)
    target_d = round(old_d * 1.04, 4)
    new_d = old_d + (target_d - old_d) * s
    qn["debt_delta_material"] = new_d
    _record(adjustments, area="quiescence", field="debt_delta_material", old=old_d, new=new_d, reason=f"{tag}_materiality")

    inv = p.setdefault("intervention", {})
    old_s = int(inv.get("stagnation_min_delta_reports") or 3)
    target_s = max(2, old_s - 1)
    new_s = int(round(old_s + (target_s - old_s) * s))
    if new_s != old_s:
        inv["stagnation_min_delta_reports"] = new_s
        _record(
            adjustments,
            area="intervention",
            field="stagnation_min_delta_reports",
            old=old_s,
            new=new_s,
            reason=f"{tag}_intervention_sensitivity",
        )


def _apply_engagement_mission(
    p: dict[str, Any], adjustments: list[dict[str, Any]], *, strength: float = 1.0
) -> None:
    s = max(0.0, min(1.0, float(strength)))
    if s <= 0.0:
        return
    tag = f"mission:engagement[strength={s}]"
    qs = p.setdefault("queue_scoring", {})
    fam = qs.setdefault("family_points", {})
    old_e = float(fam.get("experiments") or 0.0)
    target_e = round(old_e * 1.06, 4)
    new_e = old_e + (target_e - old_e) * s
    fam["experiments"] = new_e
    _record(
        adjustments,
        area="queue_scoring.family_points",
        field="experiments",
        old=old_e,
        new=new_e,
        reason=f"{tag}_experimentation_appetite",
    )

    lc = qs.setdefault("lifecycle_points", {})
    old_g = float(lc.get("grow") or 0.0)
    target_g = round(old_g * 1.05, 4)
    new_g = old_g + (target_g - old_g) * s
    lc["grow"] = new_g
    _record(
        adjustments,
        area="queue_scoring.lifecycle_points",
        field="grow",
        old=old_g,
        new=new_g,
        reason=f"{tag}_growth_focus",
    )

    inv = p.setdefault("intervention", {})
    old_w = int(inv.get("progression_runs_window") or 8)
    target_w = min(24, old_w + 1)
    new_w = int(round(old_w + (target_w - old_w) * s))
    if new_w != old_w:
        inv["progression_runs_window"] = new_w
        _record(
            adjustments,
            area="intervention",
            field="progression_runs_window",
            old=old_w,
            new=new_w,
            reason=f"{tag}_chronic_noise_buffer",
        )

    qn = p.setdefault("quiescence", {})
    old_c = float(qn.get("confidence_delta_material") or 0.1)
    target_c = max(0.05, round(old_c * 0.96, 4))
    new_c = old_c + (target_c - old_c) * s
    qn["confidence_delta_material"] = max(0.05, new_c)
    _record(
        adjustments,
        area="quiescence",
        field="confidence_delta_material",
        old=old_c,
        new=qn["confidence_delta_material"],
        reason=f"{tag}_confidence_materiality",
    )


def _apply_risk_posture(p: dict[str, Any], rp: str, adjustments: list[dict[str, Any]]) -> None:
    if rp == "moderate":
        return
    if rp == "conservative":
        conf = p.setdefault("confidence", {})
        old_lt = float(conf.get("low_threshold") or 0.45)
        new_lt = _clamp_float(old_lt + 0.015, 0.35, 0.58)
        conf["low_threshold"] = new_lt
        _record(
            adjustments,
            area="confidence",
            field="low_threshold",
            old=old_lt,
            new=new_lt,
            reason="risk_posture:conservative",
        )

        qn = p.setdefault("quiescence", {})
        old_dd = float(qn.get("debt_delta_material") or 0.08)
        new_dd = round(old_dd * 1.025, 4)
        qn["debt_delta_material"] = new_dd
        _record(
            adjustments,
            area="quiescence",
            field="debt_delta_material",
            old=old_dd,
            new=new_dd,
            reason="risk_posture:conservative",
        )

        inv = p.setdefault("intervention", {})
        old_s = int(inv.get("stagnation_min_delta_reports") or 3)
        new_s = max(2, old_s - 1)
        inv["stagnation_min_delta_reports"] = new_s
        _record(
            adjustments,
            area="intervention",
            field="stagnation_min_delta_reports",
            old=old_s,
            new=new_s,
            reason="risk_posture:conservative_intervention",
        )

        rd = p.setdefault("readiness", {})
        old_g = float(rd.get("gate_debt_caution") or 0.55)
        new_g = _clamp_float(old_g + 0.015, 0.0, 0.95)
        rd["gate_debt_caution"] = new_g
        _record(
            adjustments,
            area="readiness",
            field="gate_debt_caution",
            old=old_g,
            new=new_g,
            reason="risk_posture:conservative",
        )
        return

    if rp == "aggressive":
        conf = p.setdefault("confidence", {})
        old_lt = float(conf.get("low_threshold") or 0.45)
        new_lt = _clamp_float(old_lt - 0.015, 0.35, 0.58)
        conf["low_threshold"] = new_lt
        _record(
            adjustments,
            area="confidence",
            field="low_threshold",
            old=old_lt,
            new=new_lt,
            reason="risk_posture:aggressive",
        )

        qn = p.setdefault("quiescence", {})
        old_dd = float(qn.get("debt_delta_material") or 0.08)
        new_dd = round(old_dd * 0.975, 4)
        qn["debt_delta_material"] = max(0.03, new_dd)
        _record(
            adjustments,
            area="quiescence",
            field="debt_delta_material",
            old=old_dd,
            new=new_dd,
            reason="risk_posture:aggressive",
        )

        inv = p.setdefault("intervention", {})
        old_s = int(inv.get("stagnation_min_delta_reports") or 3)
        new_s = min(8, old_s + 1)
        inv["stagnation_min_delta_reports"] = new_s
        _record(
            adjustments,
            area="intervention",
            field="stagnation_min_delta_reports",
            old=old_s,
            new=new_s,
            reason="risk_posture:aggressive",
        )

        rd = p.setdefault("readiness", {})
        old_g = float(rd.get("gate_debt_caution") or 0.55)
        new_g = _clamp_float(old_g - 0.015, 0.35, 0.95)
        rd["gate_debt_caution"] = new_g
        _record(
            adjustments,
            area="readiness",
            field="gate_debt_caution",
            old=old_g,
            new=new_g,
            reason="risk_posture:aggressive",
        )


def _apply_mission_id_effect(
    p: dict[str, Any],
    mid: str,
    adjustments: list[dict[str, Any]],
    *,
    strength: float,
    reason_prefix: str,
) -> None:
    m = str(mid).strip()
    if m == "education":
        _apply_education_mission(p, adjustments, strength=strength)
    elif m == "engagement":
        _apply_engagement_mission(p, adjustments, strength=strength)
    elif m == "revenue":
        return
    else:
        adjustments.append(
            {
                "area": "mission",
                "field": "profile",
                "from": None,
                "to": m,
                "reason": f"{reason_prefix}:custom_profile_no_numeric_overlay",
            }
        )


def _apply_guardrail_profile(p: dict[str, Any], mid: str, adjustments: list[dict[str, Any]]) -> None:
    """Constraint-oriented overlays (small deterministic nudges)."""
    m = str(mid).strip()
    if m == "education":
        conf = p.setdefault("confidence", {})
        old_lt = float(conf.get("low_threshold") or 0.45)
        new_lt = _clamp_float(old_lt + 0.012, 0.35, 0.58)
        conf["low_threshold"] = new_lt
        _record(
            adjustments,
            area="confidence",
            field="low_threshold",
            old=old_lt,
            new=new_lt,
            reason="guardrail:education",
        )
        rd = p.setdefault("readiness", {})
        old_g = float(rd.get("gate_debt_caution") or 0.55)
        new_g = _clamp_float(old_g + 0.018, 0.0, 0.95)
        rd["gate_debt_caution"] = new_g
        _record(
            adjustments,
            area="readiness",
            field="gate_debt_caution",
            old=old_g,
            new=new_g,
            reason="guardrail:education",
        )
    elif m == "engagement":
        qn = p.setdefault("quiescence", {})
        old_c = float(qn.get("confidence_delta_material") or 0.1)
        new_c = min(0.25, round(old_c * 1.06, 4))
        qn["confidence_delta_material"] = new_c
        _record(
            adjustments,
            area="quiescence",
            field="confidence_delta_material",
            old=old_c,
            new=new_c,
            reason="guardrail:engagement",
        )
    elif m == "revenue":
        rd = p.setdefault("readiness", {})
        old_g = float(rd.get("gate_debt_caution") or 0.55)
        new_g = _clamp_float(old_g + 0.012, 0.0, 0.95)
        rd["gate_debt_caution"] = new_g
        _record(
            adjustments,
            area="readiness",
            field="gate_debt_caution",
            old=old_g,
            new=new_g,
            reason="guardrail:revenue",
        )


def apply_structured_mission_to_operator_policy(
    base_policy: dict[str, Any],
    *,
    objective_profile: dict[str, Any],
    driver_profiles: list[dict[str, Any]],
    guardrail_profiles: list[dict[str, Any]],
    resolved_mission_id: str,
    resolution_chain: list[str],
    effective_risk_posture: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Apply objective (full), drivers (lighter overlays), guardrails (constraints), then risk posture.

    ``driver_profiles`` / ``guardrail_profiles`` are loaded registry profile dicts (``load_mission_by_id``).
    """
    p = copy.deepcopy(base_policy)
    adjustments: list[dict[str, Any]] = []

    obj_id = str(objective_profile.get("id") or resolved_mission_id).strip()
    _apply_mission_id_effect(p, obj_id, adjustments, strength=1.0, reason_prefix="objective")

    for dp in driver_profiles:
        did = str(dp.get("id") or "").strip()
        if did:
            _apply_mission_id_effect(
                p,
                did,
                adjustments,
                strength=DRIVER_OVERLAY_STRENGTH,
                reason_prefix=f"driver:{did}",
            )

    for gp in guardrail_profiles:
        gid = str(gp.get("id") or "").strip()
        if gid:
            _apply_guardrail_profile(p, gid, adjustments)

    rp = str(effective_risk_posture or "moderate").strip().lower()
    _apply_risk_posture(p, rp, adjustments)

    composition = {
        "objective": obj_id,
        "drivers": [str(x.get("id") or "") for x in driver_profiles if x.get("id")],
        "guardrails": [str(x.get("id") or "") for x in guardrail_profiles if x.get("id")],
        "effective_risk_posture": rp,
    }

    block: dict[str, Any] = {
        "schema": OPERATOR_POLICY_MISSION_CONTEXT_SCHEMA,
        "resolved_mission_id": resolved_mission_id,
        "resolution_chain": list(resolution_chain),
        "mission_profile_id": obj_id,
        "risk_posture": rp,
        "primary_objective": objective_profile.get("primary_objective"),
        "drivers": objective_profile.get("drivers"),
        "policy_influence_hints": objective_profile.get("policy_influence_hints"),
        "weights": objective_profile.get("weights"),
        "mission_composition": composition,
        "adjustments_applied": adjustments,
        "policy_areas_touched": sorted(
            {
                str(a.get("area") or "").split(".")[0]
                for a in adjustments
                if isinstance(a, dict) and a.get("area")
            }
        ),
    }
    p["mission_integration"] = block
    return p, block


def apply_mission_profile_to_operator_policy(
    base_policy: dict[str, Any],
    *,
    mission_profile: dict[str, Any],
    resolved_mission_id: str,
    resolution_chain: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Return ``(policy_with_mission_context, mission_integration_block)``.

    ``base_policy`` must already satisfy :func:`validate_operator_policy` before call; the returned policy
    is re-validated after adjustments.
    """
    p = copy.deepcopy(base_policy)
    adjustments: list[dict[str, Any]] = []

    mid = str(mission_profile.get("id") or resolved_mission_id).strip()
    rp = str(mission_profile.get("risk_posture") or "moderate").strip().lower()

    if mid == "education":
        _apply_education_mission(p, adjustments)
    elif mid == "engagement":
        _apply_engagement_mission(p, adjustments)
    elif mid == "revenue":
        pass
    else:
        # Unknown custom mission id: apply risk posture only (safe fallback)
        adjustments.append(
            {
                "area": "mission",
                "field": "profile",
                "from": None,
                "to": mid,
                "reason": "mission:custom_profile_risk_posture_only",
            }
        )

    _apply_risk_posture(p, rp, adjustments)

    block: dict[str, Any] = {
        "schema": OPERATOR_POLICY_MISSION_CONTEXT_SCHEMA,
        "resolved_mission_id": resolved_mission_id,
        "resolution_chain": list(resolution_chain),
        "mission_profile_id": mid,
        "risk_posture": rp,
        "primary_objective": mission_profile.get("primary_objective"),
        "drivers": mission_profile.get("drivers"),
        "policy_influence_hints": mission_profile.get("policy_influence_hints"),
        "weights": mission_profile.get("weights"),
        "adjustments_applied": adjustments,
        "policy_areas_touched": sorted(
            {
                str(a.get("area") or "").split(".")[0]
                for a in adjustments
                if isinstance(a, dict) and a.get("area")
            }
        ),
    }
    p["mission_integration"] = block
    return p, block
