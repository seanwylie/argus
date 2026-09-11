"""
Mission-aware interpretation for portfolio outcomes (additive, deterministic).

Uses existing per-product trajectory fields only — no changes to classification math.
Truth-producing layers do not import this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.mission.mission import resolve_product_mission

PORTFOLIO_OUTCOME_MISSION_INTERPRETATION_SCHEMA = "argus.portfolio_outcome_mission_interpretation.v1"

# Thresholds on weighted score for alignment label (tuned for typical [-6, 6] range).
_ALIGN_POS = 1.25
_ALIGN_NEG = -1.25
_DRIVER_SUPPORT_THRESHOLD = 0.35


def _w(objective: str) -> dict[str, float]:
    """Weights per signal key for objective-specific alignment."""
    o = str(objective or "").strip().lower()
    if o == "education":
        return {
            "readiness_improved": 0.8,
            "readiness_regressed": -1.4,
            "debt_decreased": 1.2,
            "debt_increased": -1.2,
            "rank_improved": 0.5,
            "rank_worsened": -0.5,
            "blocked_cleared": 0.9,
            "blocked_bad": -1.1,
            "confidence_improved": 1.6,
            "confidence_worsened": -1.6,
            "import_recovered": 0.8,
            "import_bad": -1.0,
            "overall_positive": 1.8,
            "overall_negative": -1.8,
            "intervention_resolved": 0.6,
            "intervention_risk": -0.8,
        }
    if o == "engagement":
        return {
            "readiness_improved": 0.9,
            "readiness_regressed": -1.0,
            "debt_decreased": 0.7,
            "debt_increased": -0.8,
            "rank_improved": 1.1,
            "rank_worsened": -0.9,
            "blocked_cleared": 0.7,
            "blocked_bad": -0.9,
            "confidence_improved": 0.8,
            "confidence_worsened": -0.9,
            "import_recovered": 0.6,
            "import_bad": -0.8,
            "overall_positive": 1.5,
            "overall_negative": -1.5,
            "intervention_resolved": 1.0,
            "intervention_risk": -1.2,
        }
    # revenue (default / unknown objectives)
    return {
        "readiness_improved": 1.0,
        "readiness_regressed": -1.1,
        "debt_decreased": 1.0,
        "debt_increased": -1.0,
        "rank_improved": 1.0,
        "rank_worsened": -0.7,
        "blocked_cleared": 1.0,
        "blocked_bad": -1.0,
        "confidence_improved": 0.7,
        "confidence_worsened": -0.7,
        "import_recovered": 0.9,
        "import_bad": -1.0,
        "overall_positive": 1.8,
        "overall_negative": -1.8,
        "intervention_resolved": 0.7,
        "intervention_risk": -0.9,
    }


def _driver_weights(driver_id: str) -> dict[str, float]:
    """Lighter overlay: same shape as objective but scaled for driver 'support' detection."""
    base = _w(driver_id)
    return {k: v * 0.75 for k, v in base.items()}


def _signal_vector(row: dict[str, Any]) -> dict[str, float]:
    """Extract 0/1 (or fractional) presence of directional signals from outcome row."""
    r: dict[str, float] = {k: 0.0 for k in (
        "readiness_improved",
        "readiness_regressed",
        "debt_decreased",
        "debt_increased",
        "rank_improved",
        "rank_worsened",
        "blocked_cleared",
        "blocked_bad",
        "confidence_improved",
        "confidence_worsened",
        "import_recovered",
        "import_bad",
        "overall_positive",
        "overall_negative",
        "intervention_resolved",
        "intervention_risk",
    )}
    if row.get("readiness_trajectory") == "improved":
        r["readiness_improved"] = 1.0
    elif row.get("readiness_trajectory") == "regressed":
        r["readiness_regressed"] = 1.0
    if row.get("understanding_debt_trajectory") == "decreased":
        r["debt_decreased"] = 1.0
    elif row.get("understanding_debt_trajectory") == "increased":
        r["debt_increased"] = 1.0
    if row.get("queue_rank_trajectory") == "improved":
        r["rank_improved"] = 1.0
    elif row.get("queue_rank_trajectory") == "worsened":
        r["rank_worsened"] = 1.0
    bp = str(row.get("blocked_pattern") or "")
    if bp == "cleared":
        r["blocked_cleared"] = 1.0
    elif bp in ("persisted", "newly_blocked"):
        r["blocked_bad"] = 1.0
    ct = str(row.get("decision_confidence_trajectory") or "")
    if ct == "improved":
        r["confidence_improved"] = 1.0
    elif ct == "worsened":
        r["confidence_worsened"] = 1.0
    imp = str(row.get("import_health_trajectory") or "")
    if imp == "recovered":
        r["import_recovered"] = 1.0
    elif imp in ("stayed_bad", "regressed"):
        r["import_bad"] = 1.0
    ov = str(row.get("overall_trajectory") or "")
    if ov == "positive":
        r["overall_positive"] = 1.0
    elif ov == "negative":
        r["overall_negative"] = 1.0
    ip = str(row.get("intervention_pattern") or "")
    if ip == "resolved":
        r["intervention_resolved"] = 1.0
    elif ip in ("newly_flagged", "repeated"):
        r["intervention_risk"] = 1.0
    return r


def _dot(weights: dict[str, float], sig: dict[str, float]) -> float:
    return sum(weights.get(k, 0.0) * sig.get(k, 0.0) for k in weights)


def _alignment_label(score: float) -> str:
    if score >= _ALIGN_POS:
        return "positive"
    if score <= _ALIGN_NEG:
        return "negative"
    return "neutral"


def _guardrail_risks(guardrail_id: str, row: dict[str, Any], sig: dict[str, float]) -> list[str]:
    gid = str(guardrail_id).strip().lower()
    codes: list[str] = []
    if gid == "education":
        if sig.get("readiness_regressed", 0) >= 1:
            codes.append("outcomes.mission_guardrail.education.readiness_regressed")
        if sig.get("confidence_worsened", 0) >= 1:
            codes.append("outcomes.mission_guardrail.education.confidence_worsened")
        if sig.get("debt_increased", 0) >= 1:
            codes.append("outcomes.mission_guardrail.education.debt_increased")
    elif gid == "engagement":
        if sig.get("intervention_risk", 0) >= 1 and str(row.get("overall_trajectory")) == "negative":
            codes.append("outcomes.mission_guardrail.engagement.intervention_strain_under_negative_outcome")
        if sig.get("rank_worsened", 0) >= 1 and sig.get("readiness_regressed", 0) >= 1:
            codes.append("outcomes.mission_guardrail.engagement.visibility_and_readiness_stress")
    elif gid == "revenue":
        if sig.get("blocked_bad", 0) >= 1 and str(row.get("overall_trajectory")) in ("negative", "mixed"):
            codes.append("outcomes.mission_guardrail.revenue.blocked_under_poor_outcome")
        if sig.get("import_bad", 0) >= 1:
            codes.append("outcomes.mission_guardrail.revenue.import_health_strain")
    return codes


def interpret_product_outcome_mission(
    repo_root: Path,
    product_id: str,
    outcome_row: dict[str, Any],
) -> dict[str, Any]:
    """
    Build deterministic mission interpretation for one product's outcome row.

    Always returns a dict with ``schema`` — uses repository fallback mission when product has none.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    eff = resolve_product_mission(root, pid)
    sm = eff.get("structured_mission") if isinstance(eff.get("structured_mission"), dict) else {}
    objective = str(sm.get("objective") or eff.get("resolved_mission_id") or "").strip()
    drivers = [str(x).strip() for x in (sm.get("drivers") or []) if str(x).strip()]
    guardrails = [str(x).strip() for x in (sm.get("guardrails") or []) if str(x).strip()]
    eff_rp = str(sm.get("effective_risk_posture") or "").strip() if sm else ""

    sig = _signal_vector(outcome_row)
    insufficient = "outcomes.insufficient_snapshot_history" in (outcome_row.get("reason_codes") or [])

    w_obj = _w(objective)
    score = _dot(w_obj, sig)
    alignment = _alignment_label(score) if not insufficient else "neutral"

    driver_support: list[str] = []
    for did in drivers:
        ws = _driver_weights(did)
        if _dot(ws, sig) >= _DRIVER_SUPPORT_THRESHOLD:
            driver_support.append(did)

    gr_list: list[dict[str, Any]] = []
    for gid in guardrails:
        rc = _guardrail_risks(gid, outcome_row, sig)
        if rc:
            gr_list.append({"guardrail": gid, "risk_codes": rc})

    summary_parts: list[str] = []
    summary_parts.append(f"objective={objective or '—'} mission_alignment={alignment} (score={round(score, 3)})")
    if insufficient:
        summary_parts.append("sparse snapshot history — mission view provisional")
    if driver_support:
        summary_parts.append(f"driver support: {', '.join(driver_support)}")
    if gr_list:
        summary_parts.append(
            "guardrail attention: "
            + "; ".join(f"{g['guardrail']}: {len(g['risk_codes'])}" for g in gr_list)
        )
    else:
        summary_parts.append("guardrail risks: none flagged")

    return {
        "schema": PORTFOLIO_OUTCOME_MISSION_INTERPRETATION_SCHEMA,
        "mission_objective": objective or None,
        "mission_drivers": drivers,
        "mission_guardrails": guardrails,
        "effective_risk_posture": eff_rp or None,
        "mission_alignment": alignment,
        "mission_alignment_score": round(score, 4),
        "driver_support_signals": driver_support,
        "guardrail_risk_signals": gr_list,
        "outcome_quality_summary": " — ".join(summary_parts),
        "interpretation_reason_codes": (
            ["outcomes.mission_interpretation.insufficient_history"] if insufficient else []
        ),
    }
