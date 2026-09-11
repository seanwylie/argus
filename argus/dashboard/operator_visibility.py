"""
Operator-facing confidence, uncertainty, risk, and escalation pressure.

When :mod:`argus.decision_assessment` data is present (``decision_context`` on the
product row), those canonical scores replace the legacy heuristic fields for display.

Legacy path: uses top candidate confidence; uncertainty was ``1 - confidence``.
"""

from __future__ import annotations

from typing import Any

# Severity rank for max-severity risk (aligned with dashboard ORDER_SEV concept).
_SEV_RANK = {"info": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _max_severity_rank(findings_by_severity: dict[str, Any]) -> int:
    m = -1
    for k, n in (findings_by_severity or {}).items():
        if not n:
            continue
        m = max(m, _SEV_RANK.get(str(k).lower(), -1))
    return m


def _risk_from_findings_and_posture(
    findings_by_severity: dict[str, Any],
    *,
    kill_candidate: bool,
    escalation_count: int,
    freshness_escalation: bool,
) -> float:
    """Deterministic 0–1 risk aggregate (not a second confidence model)."""
    mr = _max_severity_rank(findings_by_severity)
    base = (mr + 1) / 5.0 if mr >= 0 else 0.2
    if kill_candidate:
        base = _clip(base + 0.18)
    base = _clip(base + min(0.28, escalation_count * 0.09))
    if freshness_escalation:
        base = _clip(base + 0.12)
    return round(_clip(base), 3)


def _escalation_pressure(
    escalation_count: int,
    *,
    escalation_risk_high: bool,
    freshness_escalation: bool,
    trend_risky: bool,
) -> float:
    x = min(0.85, escalation_count * 0.22)
    if escalation_risk_high:
        x = _clip(x + 0.15)
    if freshness_escalation:
        x = _clip(x + 0.18)
    if trend_risky:
        x = _clip(x + 0.1)
    return round(_clip(x), 3)


def _escalation_has_high_risk(escalations: list[dict[str, Any]]) -> bool:
    for e in escalations or []:
        lv = str(e.get("risk_level") or "").lower()
        if lv in ("high", "critical"):
            return True
    return False


def _trend_suggests_recurrence_risk(trend_summary: dict[str, Any] | None) -> tuple[bool, list[str]]:
    if not trend_summary:
        return False, []
    flags = list(trend_summary.get("trend_flags") or [])
    drift = list(trend_summary.get("drift_signals") or [])
    risky = False
    notes: list[str] = []
    for f in flags:
        fs = str(f).lower()
        if any(
            x in fs
            for x in (
                "risk",
                "stagnat",
                "thrash",
                "drift",
                "abandon",
                "escalat",
            )
        ):
            risky = True
            notes.append(f"trend_flag:{f}")
    for d in drift:
        ds = str(d).lower()
        if any(x in ds for x in ("stale", "risk", "drift", "escalat")):
            risky = True
            notes.append(f"drift:{d}")
    return risky, notes[:12]


def _is_exploratory_intent(intent: str | None, top_metadata: dict[str, Any]) -> bool:
    if top_metadata.get("freshness_recommend_gather_data"):
        return True
    i = (intent or "").lower()
    return i in ("launch_experiment", "gather_more_data")


def build_operator_visibility(
    *,
    top_confidence: float | None,
    top_intent: str | None,
    top_metadata: dict[str, Any] | None,
    portfolio_freshness_warnings: list[str] | None = None,
    findings_by_severity: dict[str, Any],
    active_findings_count: int,
    signal_record_count: int,
    temporal_findings_count: int,
    kill_candidate: bool,
    escalations: list[dict[str, Any]],
    trend_summary: dict[str, Any] | None,
    temporal_visibility: dict[str, Any] | None,
    decision_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Single product block for dashboard JSON.

    ``uncertainty`` is ``1 - confidence`` when confidence is present; otherwise null.
    """
    md = dict(top_metadata or {})
    conf = float(top_confidence) if top_confidence is not None else None
    if conf is not None:
        conf = _clip(conf)

    uncertainty: float | None
    uncertainty_basis: str
    if conf is not None:
        uncertainty = round(1.0 - conf, 4)
        uncertainty_basis = "one_minus_top_candidate_confidence"
    else:
        uncertainty = None
        uncertainty_basis = "no_top_candidate_confidence"

    fe = bool(md.get("freshness_escalation"))
    stale_aff = bool(md.get("stale_data_affected_confidence"))

    esc_n = len(escalations or [])
    esc_hi = _escalation_has_high_risk(escalations)
    trend_risky, trend_notes = _trend_suggests_recurrence_risk(trend_summary)

    risk_score = _risk_from_findings_and_posture(
        findings_by_severity,
        kill_candidate=kill_candidate,
        escalation_count=esc_n,
        freshness_escalation=fe,
    )
    esc_pressure = _escalation_pressure(
        esc_n,
        escalation_risk_high=esc_hi,
        freshness_escalation=fe,
        trend_risky=trend_risky,
    )

    exploratory = _is_exploratory_intent(top_intent, md)

    # Evidence: signals vs findings scale (sparse signals → lower density).
    denom = max(1, active_findings_count) * 3 + 6
    evidence_density = round(_clip(signal_record_count / float(denom)), 3)

    friction: list[str] = []
    for w in md.get("freshness_warnings") or []:
        if str(w).strip():
            friction.append(f"freshness:{w}")
    for w in portfolio_freshness_warnings or []:
        if str(w).strip():
            friction.append(f"portfolio_freshness:{w}")
    tv = temporal_visibility or {}
    for x in tv.get("flags") or []:
        friction.append(f"temporal_flag:{x}")
    sig = (tv.get("signals") or {}) if isinstance(tv.get("signals"), dict) else {}
    for x in sig.get("validation_issues") or []:
        friction.append(f"signal_check:{x}")

    contributors: list[str] = []
    if conf is not None and conf < 0.45:
        contributors.append("low_top_candidate_confidence")
    if stale_aff:
        contributors.append("stale_or_missing_temporal_inputs_for_gated_decision")
    if conf is None:
        contributors.append("no_confidence_on_top_recommendation")
    if signal_record_count == 0 and active_findings_count > 0:
        contributors.append("no_persisted_signal_bundle_or_empty")
    if temporal_findings_count >= 2:
        contributors.append("multiple_temporal_findings")
    if esc_n >= 2:
        contributors.append("multiple_escalation_packets")
    if kill_candidate:
        contributors.append("kill_candidate_posture")

    recurrence_risk = {
        "present": trend_risky,
        "notes": trend_notes,
    }

    factor_breakdown = {
        "priority_weights": (md.get("weights") if isinstance(md.get("weights"), dict) else None),
        "strategy_mode": md.get("strategy_mode"),
        "freshness_inputs": md.get("freshness_inputs"),
        "finding_severity_mix": dict(findings_by_severity or {}),
    }

    badges: list[dict[str, str]] = []
    if exploratory:
        badges.append({"label": "exploratory", "tone": "warn"})
    if fe:
        badges.append({"label": "freshness_escalation", "tone": "bad"})
    if kill_candidate:
        badges.append({"label": "kill_candidate", "tone": "bad"})
    if esc_n > 0:
        badges.append({"label": f"esc×{esc_n}", "tone": "warn" if esc_n >= 2 else "muted"})

    out: dict[str, Any] = {
        "schema": "argus.dashboard_operator_visibility.v1",
        "confidence": conf,
        "uncertainty": uncertainty,
        "uncertainty_basis": uncertainty_basis,
        "risk_score": risk_score,
        "escalation_pressure": esc_pressure,
        "exploratory": exploratory,
        "badges": badges,
        "factor_breakdown": factor_breakdown,
        "evidence_density": evidence_density,
        "recurrence_risk": recurrence_risk,
        "friction_sources": friction[:24],
        "top_uncertainty_contributors": contributors[:16],
    }

    dc = decision_context if isinstance(decision_context, dict) else None
    if dc:
        out["decision_context_assessment"] = {
            "confidence_score": dc.get("confidence_score"),
            "uncertainty_score": dc.get("uncertainty_score"),
            "risk_score": dc.get("risk_score"),
            "recurrence_risk_score": dc.get("recurrence_risk_score"),
            "momentum_score": dc.get("momentum_score"),
            "friction_score": dc.get("friction_score"),
            "escalation_pressure": dc.get("escalation_pressure"),
            "escalation_recommendation": dc.get("escalation_recommendation"),
            "evidence_density_score": dc.get("evidence_density_score"),
            "exploratory_action_recommended": dc.get("exploratory_action_recommended"),
            "assessed_at_utc": dc.get("assessed_at_utc"),
        }
        cs = dc.get("confidence_score")
        if isinstance(cs, (int, float)):
            out["confidence"] = round(float(cs), 4)
        us = dc.get("uncertainty_score")
        if isinstance(us, (int, float)):
            out["uncertainty"] = round(float(us), 4)
        rs = dc.get("risk_score")
        if isinstance(rs, (int, float)):
            out["risk_score"] = round(float(rs), 4)
        ep = dc.get("escalation_pressure")
        if isinstance(ep, (int, float)):
            out["escalation_pressure"] = round(float(ep), 4)
        eds = dc.get("evidence_density_score")
        if isinstance(eds, (int, float)):
            out["evidence_density"] = round(float(eds), 4)
        if dc.get("exploratory_action_recommended") is True:
            out["exploratory"] = True
        out["uncertainty_basis"] = "decision_context_assessment"

    return out


def collect_operator_alerts(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Portfolio-level warning rows for the dashboard shell."""
    out: list[dict[str, Any]] = []
    for p in products:
        pid = p.get("product_id")
        ov = p.get("operator_visibility") or {}
        if not pid or not isinstance(ov, dict):
            continue
        conf = ov.get("confidence")
        risk = float(ov.get("risk_score") or 0)
        esc_p = float(ov.get("escalation_pressure") or 0)
        exploratory = bool(ov.get("exploratory"))

        if conf is not None and float(conf) < 0.42 and risk >= 0.55:
            out.append(
                {
                    "level": "warn",
                    "code": "low_confidence_high_risk",
                    "product_id": pid,
                    "message": (
                        f"Low confidence ({float(conf):.2f}) with elevated risk ({risk:.2f}) "
                        f"for top recommendation — review before acting."
                    ),
                }
            )
        if exploratory and risk >= 0.5:
            out.append(
                {
                    "level": "info",
                    "code": "exploratory_under_risk",
                    "product_id": pid,
                    "message": "Exploratory / weakly supported recommendation path — confirm assumptions.",
                }
            )
        if esc_p >= 0.72:
            out.append(
                {
                    "level": "warn",
                    "code": "escalation_pressure_high",
                    "product_id": pid,
                    "message": f"Escalation pressure is high ({esc_p:.2f}) — review packets and temporal context.",
                }
            )
    out.sort(key=lambda x: (x.get("level") != "warn", x.get("product_id")))
    return out
