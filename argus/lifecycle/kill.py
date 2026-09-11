"""
Kill criteria scoring from local artifacts only (signals, findings, economics, decisions, escalations).

Maps multiple risk dimensions into a 0–100 ``kill_score`` and a coarse recommendation.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from argus.core.models.enums import FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.decision.history.analyze import analyze_churn
from argus.decision.history.store import load_product_decision_history
from argus.economics.analyze import analyze_product_economics
from argus.economics.models import GrowthSignal
from argus.escalation.packet import list_packets
from argus.findings.persistence import load_latest_findings
from argus.signals.persistence import load_latest_bundle
from argus.signals.snapshots.models import NO_USAGE


class KillRecommendation(str, Enum):
    """Coarse operator stance derived from ``kill_score`` thresholds."""

    CONTINUE = "continue"
    HOLD = "hold"
    DEPRECATE = "deprecate"
    KILL = "kill"


# Weights over normalized 0–1 risk dimensions (higher = more reason to wind down).
_WEIGHTS: dict[str, float] = {
    "inactivity": 0.18,
    "findings_trend": 0.18,
    "cost_vs_value": 0.22,
    "experiment_failure": 0.14,
    "decision_churn": 0.14,
    "escalation": 0.14,
}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _days_since_signals(records: list[Any]) -> tuple[float, bool]:
    """Return (days since newest observed_at, has_no_usage_signal)."""
    has_no_usage = False
    latest: datetime | None = None
    for r in records:
        p = r.payload or {}
        if p.get("business_signal") == NO_USAGE:
            has_no_usage = True
        obs = r.observed_at
        if isinstance(obs, datetime):
            if latest is None or obs > latest:
                latest = obs
    if latest is None:
        return (90.0, has_no_usage)
    now = datetime.now(timezone.utc)
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    delta = now - latest
    return (max(0.0, delta.total_seconds() / 86400.0), has_no_usage)


def _inactivity_risk(days: float, has_no_usage: bool) -> float:
    """0 = fresh activity, 1 = long silence / abandonment signals."""
    # 120d of staleness ~= full contribution at this dimension
    base = _clamp01(days / 120.0)
    if has_no_usage:
        base = _clamp01(base + 0.28)
    return base


_BAD_FINDING_KINDS: frozenset[FindingKind] = frozenset(
    {
        FindingKind.DEPRECATION_CANDIDATE,
        FindingKind.INACTIVITY,
        FindingKind.COST_RISK,
        FindingKind.RETENTION_PROBLEM,
        FindingKind.RELIABILITY_PROBLEM,
        FindingKind.CURRENT_RISK,
        FindingKind.STALE_CONTEXT,
        FindingKind.NO_RECENT_EVIDENCE,
    }
)

_BAD_SEVERITY: frozenset[SeverityLevel] = frozenset(
    {SeverityLevel.HIGH, SeverityLevel.CRITICAL}
)


def _findings_risk(findings: list[Finding], trend_flag_boost: float) -> float:
    if not findings:
        return 0.22 + trend_flag_boost * 0.5
    bad = 0.0
    for f in findings:
        if f.kind in _BAD_FINDING_KINDS:
            bad += 1.0
        elif f.severity in _BAD_SEVERITY:
            bad += 0.65
        else:
            bad += 0.12
    ratio = bad / max(1.0, len(findings) * 1.0)
    return _clamp01(ratio + trend_flag_boost)


def _trend_flag_boost(flags: list[str]) -> float:
    t = 0.0
    for fl in flags:
        u = (fl if isinstance(fl, str) else str(fl)).lower()
        if u in ("risk_increasing", "likely_abandon"):
            t += 0.35
        if u in ("stagnating", "drifting"):
            t += 0.2
    return _clamp01(t)


def _load_trend_flags(repo: Path, product_id: str) -> list[str]:
    p = repo / "runs" / "trends" / "latest.json"
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    summaries = data.get("summaries") or []
    if not isinstance(summaries, list):
        return []
    for s in summaries:
        if not isinstance(s, dict):
            continue
        if str(s.get("product_id", "")) != product_id:
            continue
        tf = s.get("trend_flags") or []
        if isinstance(tf, list):
            return [str(x) for x in tf]
    return []


def _cost_value_risk(
    monthly_cost: float,
    estimated_revenue: float,
    burn_rate: float,
    growth: GrowthSignal,
) -> float:
    """High when spending dominates value or there is no revenue with meaningful cost."""
    if monthly_cost <= 1e-9:
        return 0.1 if estimated_revenue <= 0 else 0.05
    if estimated_revenue <= 1e-9 and monthly_cost >= 5.0:
        return 0.92
    if estimated_revenue <= 1e-9:
        return _clamp01(0.55 + monthly_cost / 200.0)
    margin = estimated_revenue - monthly_cost
    if margin < 0:
        return _clamp01(0.45 + min(0.45, (-margin) / max(monthly_cost, 1.0)))
    # Profitable: still flag poor ROI if revenue barely clears cost
    roi_like = margin / max(monthly_cost, 1e-9)
    risk = max(0.0, 0.35 - min(0.35, roi_like * 0.12))
    if growth == GrowthSignal.DOWN:
        risk = _clamp01(risk + 0.25)
    if growth == GrowthSignal.UP:
        risk = _clamp01(risk - 0.2)
    return _clamp01(risk)


def _experiment_failure_risk(findings: list[Finding]) -> float:
    if not findings:
        return 0.15
    n = 0.0
    for f in findings:
        if f.kind in _BAD_FINDING_KINDS:
            n += 1.0
        elif f.severity in _BAD_SEVERITY:
            n += 0.55
    return _clamp01(n / max(1.0, len(findings)))


def _escalation_risk(count: int) -> float:
    return _clamp01(min(1.0, count * 0.22))


def recommendation_for_score(kill_score: int) -> KillRecommendation:
    if kill_score <= 24:
        return KillRecommendation.CONTINUE
    if kill_score <= 49:
        return KillRecommendation.HOLD
    if kill_score <= 74:
        return KillRecommendation.DEPRECATE
    return KillRecommendation.KILL


@dataclass
class KillScoreResult:
    """Structured kill scoring output for one product."""

    product_id: str
    kill_score: int
    recommendation: KillRecommendation
    dimensions: dict[str, float]
    """Normalized 0–1 risk per input dimension (higher = worse)."""
    weights: dict[str, float] = field(default_factory=lambda: dict(_WEIGHTS))
    notes: dict[str, Any] = field(default_factory=dict)

    def to_jsonable(self) -> dict[str, Any]:
        return {
            "schema": "argus.kill_score.v1",
            "product_id": self.product_id,
            "kill_score": self.kill_score,
            "recommendation": self.recommendation.value,
            "dimensions": dict(self.dimensions),
            "weights": dict(self.weights),
            "notes": self.notes,
        }


def combine_dimension_scores(
    *,
    inactivity: float,
    findings_trend: float,
    cost_vs_value: float,
    experiment_failure: float,
    decision_churn: float,
    escalation: float,
) -> int:
    """
    Combine normalized 0–1 risk dimensions into a single 0–100 kill score.

    All inputs are "higher means more kill pressure."
    """
    d = {
        "inactivity": _clamp01(inactivity),
        "findings_trend": _clamp01(findings_trend),
        "cost_vs_value": _clamp01(cost_vs_value),
        "experiment_failure": _clamp01(experiment_failure),
        "decision_churn": _clamp01(decision_churn),
        "escalation": _clamp01(escalation),
    }
    total = sum(_WEIGHTS[k] * d[k] for k in _WEIGHTS)
    return int(round(_clamp01(total) * 100.0))


def compute_kill_score_for_product(repo_root: Path, product_id: str, node: ProductNode) -> KillScoreResult:
    """
    Compute kill score from local ``runs/`` data and product manifest.

    Safe when some artifacts are missing; neutral or conservative defaults apply.
    """
    root = repo_root.resolve()
    bundle = load_latest_bundle(root, product_id)
    records = bundle.records if bundle is not None else []
    days, no_usage = _days_since_signals(records)
    in_r = _inactivity_risk(days, no_usage)

    f_bundle = load_latest_findings(root, product_id)
    findings: list[Finding] = list(f_bundle.findings) if f_bundle is not None else []
    tflags = _load_trend_flags(root, product_id)
    tboost = _trend_flag_boost(tflags)
    find_r = _findings_risk(findings, tboost)

    econ = analyze_product_economics(node, records)
    cv_r = _cost_value_risk(
        econ.monthly_cost,
        econ.estimated_revenue,
        econ.burn_rate,
        econ.growth_signal,
    )
    exp_r = _experiment_failure_risk(findings)

    entries = load_product_decision_history(root, product_id)
    churn_rep = analyze_churn(product_id, entries, repo_root=root)
    churn_r = float(churn_rep.churn_score)

    esc_n = sum(
        1
        for row in list_packets(root, limit=400)
        if str(row.get("product_id", "")) == product_id
    )
    esc_r = _escalation_risk(esc_n)

    ks = combine_dimension_scores(
        inactivity=in_r,
        findings_trend=find_r,
        cost_vs_value=cv_r,
        experiment_failure=exp_r,
        decision_churn=churn_r,
        escalation=esc_r,
    )
    rec = recommendation_for_score(ks)

    return KillScoreResult(
        product_id=product_id,
        kill_score=ks,
        recommendation=rec,
        dimensions={
            "inactivity": in_r,
            "findings_trend": find_r,
            "cost_vs_value": cv_r,
            "experiment_failure": exp_r,
            "decision_churn": churn_r,
            "escalation": esc_r,
        },
        notes={
            "inactivity_days": days,
            "no_usage_signal": no_usage,
            "findings_count": len(findings),
            "trend_flags": tflags,
            "decision_runs": churn_rep.run_count,
            "escalation_packets": esc_n,
            "monthly_cost_usd": econ.monthly_cost,
            "estimated_revenue_usd": econ.estimated_revenue,
        },
    )


def compute_kill_scores_inventory(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> list[KillScoreResult]:
    """One :class:`KillScoreResult` per valid inventory product."""
    from argus.products.inventory import build_inventory

    inv = build_inventory(repo_root, products_dir=products_dir)
    out: list[KillScoreResult] = []
    for pid in sorted(inv.valid.keys()):
        node = inv.valid[pid].node
        out.append(compute_kill_score_for_product(repo_root, pid, node))
    return out


def results_to_jsonable(results: list[KillScoreResult]) -> dict[str, Any]:
    return {
        "schema": "argus.kill_score_batch.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "products": [r.to_jsonable() for r in results],
    }
