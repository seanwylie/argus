"""
Deterministic experiment evaluation from portfolio snapshots and trend summaries.

Compares before/after snapshot metrics around ``experiment.start_at`` and blends
per-type scoring with ``argus trends`` signals. No external APIs.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.experiments.models import (
    EvaluationVerdict,
    Experiment,
    ExperimentEvaluation,
    ExperimentStatus,
    ExperimentType,
)
from argus.experiments.store import list_experiments, save_experiment
from argus.history.models import ProductSnapshot
from argus.history.summarize import load_product_timeline
from argus.trends.analyze import analyze_product
from argus.trends.models import TrendFlag


def _clip(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _parse_iso(s: str) -> datetime | None:
    if not s or not str(s).strip():
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def _pick_before_after(
    timeline: list[tuple[str, str, ProductSnapshot]],
    start: datetime,
) -> tuple[ProductSnapshot | None, ProductSnapshot | None]:
    """Latest snapshot strictly before start, and latest at/after start."""
    before_candidates: list[tuple[datetime, ProductSnapshot]] = []
    after_candidates: list[tuple[datetime, ProductSnapshot]] = []
    for _sid, obs_str, snap in timeline:
        obs = _parse_iso(obs_str)
        if obs is None:
            continue
        if obs < start:
            before_candidates.append((obs, snap))
        else:
            after_candidates.append((obs, snap))
    before_snap = max(before_candidates, key=lambda x: x[0])[1] if before_candidates else None
    after_snap = max(after_candidates, key=lambda x: x[0])[1] if after_candidates else None
    return before_snap, after_snap


def _metrics_dict(s: ProductSnapshot) -> dict[str, Any]:
    return {
        "active_findings_count": s.active_findings_count,
        "monthly_cost_usd": s.monthly_cost_usd,
        "priority_score": s.priority_score,
        "escalation_count": s.escalation_count,
        "kill_candidate": s.kill_candidate,
    }


def _type_score(
    exp: Experiment,
    before: ProductSnapshot,
    after: ProductSnapshot,
) -> tuple[float, list[str]]:
    """Map metric deltas to [-1, 1] with human-readable reasons."""
    reasons: list[str] = []
    df = after.active_findings_count - before.active_findings_count
    de = after.escalation_count - before.escalation_count
    pb = before.priority_score
    pa = after.priority_score
    dp = (pa or 0.0) - (pb or 0.0)
    cb = before.monthly_cost_usd
    ca = after.monthly_cost_usd
    dc = (ca if ca is not None else 0.0) - (cb if cb is not None else 0.0)

    t = exp.type
    if t == ExperimentType.COST_REDUCTION:
        denom = max(1.0, abs(cb or 0.0), 1.0)
        s = _clip(-dc / denom)
        reasons.append(f"cost_delta_usd={dc:.4f}")
        return s, reasons

    if t in (ExperimentType.GROWTH, ExperimentType.ENGAGEMENT, ExperimentType.CONTENT):
        s = (
            0.45 * _clip(-df / 8.0)
            + 0.45 * _clip(dp / 22.0)
            + 0.1 * _clip(-de / 4.0)
        )
        reasons.append(f"findings_delta={df} priority_delta={dp:.4f} escalation_delta={de}")
        return s, reasons

    if t == ExperimentType.INFRASTRUCTURE:
        s = 0.5 * _clip(-df / 10.0) + 0.3 * _clip(-de / 3.0) + 0.2 * _clip(dp / 18.0)
        reasons.append(f"findings_delta={df} escalation_delta={de} priority_delta={dp:.4f}")
        return s, reasons

    s = 0.4 * _clip(-df / 8.0) + 0.4 * _clip(dp / 22.0) + 0.2 * _clip(-de / 4.0)
    reasons.append(f"findings_delta={df} priority_delta={dp:.4f}")
    return s, reasons


def _trend_adjustment(summary_flags: list[str]) -> tuple[float, list[str]]:
    flags = set(summary_flags)
    notes: list[str] = []
    adj = 0.0
    if TrendFlag.IMPROVING.value in flags:
        adj += 0.12
        notes.append("trend_improving")
    if TrendFlag.RISK_INCREASING.value in flags:
        adj -= 0.14
        notes.append("trend_risk_increasing")
    if TrendFlag.DRIFTING.value in flags:
        adj -= 0.06
        notes.append("trend_drifting")
    if flags == {TrendFlag.INSUFFICIENT_DATA.value} or (
        TrendFlag.INSUFFICIENT_DATA.value in flags and len(flags) == 1
    ):
        adj -= 0.04
        notes.append("trend_insufficient_data_only")
    return adj, notes


def _verdict_from_score(
    score: float,
    has_before_after: bool,
    trend_insufficient_only: bool,
) -> EvaluationVerdict:
    if not has_before_after:
        return EvaluationVerdict.INCONCLUSIVE
    if trend_insufficient_only and abs(score) < 0.11:
        return EvaluationVerdict.INCONCLUSIVE
    if score >= 0.18:
        return EvaluationVerdict.SUCCESS
    if score <= -0.18:
        return EvaluationVerdict.FAILED
    return EvaluationVerdict.PARTIAL_SUCCESS


def evaluate_experiment(repo_root: Path, exp: Experiment) -> ExperimentEvaluation:
    """Compute a deterministic verdict for one experiment."""
    now = datetime.now(timezone.utc).isoformat()
    reasons: list[str] = []
    start = _parse_iso(exp.start_at)
    if start is None:
        return ExperimentEvaluation(
            experiment_id=exp.id,
            product_id=exp.product_id,
            verdict=EvaluationVerdict.INCONCLUSIVE,
            composite_score=0.0,
            summary="inconclusive: invalid or missing start_at",
            evaluated_at_utc=now,
            reasons=["invalid_start_at"],
        )

    tl = load_product_timeline(repo_root, exp.product_id)
    before, after = _pick_before_after(tl, start)
    has_pair = before is not None and after is not None

    deltas: dict[str, Any] = {}
    mb: dict[str, Any] = {}
    ma: dict[str, Any] = {}
    base_score = 0.0

    if has_pair and before is not None and after is not None:
        mb = _metrics_dict(before)
        ma = _metrics_dict(after)
        deltas = {
            "active_findings_count": after.active_findings_count - before.active_findings_count,
            "monthly_cost_usd_delta": (after.monthly_cost_usd or 0.0) - (before.monthly_cost_usd or 0.0),
            "priority_score_delta": (after.priority_score or 0.0) - (before.priority_score or 0.0),
            "escalation_count_delta": after.escalation_count - before.escalation_count,
        }
        base_score, r2 = _type_score(exp, before, after)
        reasons.extend(r2)
    else:
        reasons.append("missing_before_or_after_snapshot")

    tr = analyze_product(repo_root, exp.product_id)
    trend_flags = list(tr.trend_flags)
    adj, tnotes = _trend_adjustment(trend_flags)
    reasons.extend(tnotes)

    trend_insufficient_only = trend_flags == [TrendFlag.INSUFFICIENT_DATA.value] or (
        len(trend_flags) == 1 and trend_flags[0] == TrendFlag.INSUFFICIENT_DATA.value
    )

    composite = _clip(base_score + adj)
    verdict = _verdict_from_score(composite, has_pair, trend_insufficient_only)

    summary = (
        f"{verdict.value} score={composite:.3f} "
        f"(snapshots={'ok' if has_pair else 'insufficient'})"
    )

    return ExperimentEvaluation(
        experiment_id=exp.id,
        product_id=exp.product_id,
        verdict=verdict,
        composite_score=round(composite, 4),
        summary=summary,
        evaluated_at_utc=now,
        metrics_before=mb,
        metrics_after=ma,
        deltas=deltas,
        trend_flags=trend_flags,
        reasons=reasons,
    )


def apply_evaluation_to_experiment(exp: Experiment, ev: ExperimentEvaluation) -> Experiment:
    """Attach evaluation metadata; update status only for ACTIVE experiments."""
    exp.last_evaluation_verdict = ev.verdict.value
    exp.last_evaluation_at = ev.evaluated_at_utc
    exp.last_evaluation_summary = ev.summary
    if exp.status != ExperimentStatus.ACTIVE:
        return exp
    if ev.verdict == EvaluationVerdict.INCONCLUSIVE:
        return exp
    if ev.verdict == EvaluationVerdict.SUCCESS:
        exp.status = ExperimentStatus.COMPLETED
    elif ev.verdict == EvaluationVerdict.FAILED:
        exp.status = ExperimentStatus.FAILED
    elif ev.verdict == EvaluationVerdict.PARTIAL_SUCCESS:
        exp.status = ExperimentStatus.COMPLETED
    if exp.end_at is None:
        exp.end_at = ev.evaluated_at_utc
    return exp


def run_evaluations(
    repo_root: Path,
    *,
    product_id: str | None = None,
    apply_updates: bool = True,
) -> list[ExperimentEvaluation]:
    """Evaluate non-terminal experiments; optionally persist status updates."""
    root = repo_root.resolve()
    exps = list_experiments(root, product_id=product_id)
    out: list[ExperimentEvaluation] = []
    for exp in exps:
        if exp.status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED):
            continue
        ev = evaluate_experiment(root, exp)
        out.append(ev)
        if apply_updates:
            updated = apply_evaluation_to_experiment(exp, ev)
            save_experiment(root, updated)
    return out
