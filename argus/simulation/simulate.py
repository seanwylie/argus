"""Deterministic scenario simulation from product state, metrics, and optional experiment."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import LifecycleStage
from argus.core.models.product import ProductNode
from argus.experiments.models import Experiment, ExperimentType
from argus.signals.persistence import load_latest_bundle
from argus.simulation.models import ScenarioKind, ScenarioOutcome, SimulationResult


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_weight(stage: LifecycleStage) -> float:
    return {
        LifecycleStage.IDEA: 0.25,
        LifecycleStage.BUILD: 0.4,
        LifecycleStage.VALIDATE: 0.55,
        LifecycleStage.GROW: 0.85,
        LifecycleStage.MAINTAIN: 0.5,
        LifecycleStage.DECLINE: 0.3,
        LifecycleStage.KILL: 0.1,
    }.get(stage, 0.5)


def _stable_jitter(product_id: str, salt: str, *, lo: float, hi: float) -> float:
    """Deterministic scalar in [lo, hi] from ids (avoid identical numbers for every product)."""
    h = hashlib.sha256(f"{product_id}:{salt}".encode()).hexdigest()
    x = int(h[:8], 16) / 0xFFFFFFFF
    return round(lo + (hi - lo) * x, 4)


def _extract_metrics_from_signals(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Pull numeric hints from latest signal payloads (best-effort)."""
    bundle = load_latest_bundle(repo_root, product_id)
    if bundle is None:
        return {}
    aggregated: dict[str, list[float]] = {}
    for rec in bundle.records:
        p = rec.payload or {}
        if not isinstance(p, dict):
            continue
        for k, v in p.items():
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                key = str(k).lower().replace(" ", "_")
                aggregated.setdefault(key, []).append(float(v))
    out: dict[str, Any] = {}
    for k, vals in aggregated.items():
        if not vals:
            continue
        out[k] = round(sum(vals) / len(vals), 4)
        out[f"{k}_samples"] = len(vals)
    out["signal_record_count"] = len(bundle.records)
    out["signals_collected_at_utc"] = bundle.collected_at_utc
    return out


def _fmt_pct(delta: float) -> str:
    return f"{delta:+.1f}%"


def _generic_scenarios(
    product_id: str,
    stage: LifecycleStage,
    monthly: float | None,
    cap: float | None,
    baseline: dict[str, Any],
) -> list[ScenarioOutcome]:
    """No experiment: three branches from lifecycle + cost posture only."""
    sw = _stage_weight(stage)
    amp = 0.04 + 0.06 * sw

    def j(s: str) -> float:
        return _stable_jitter(product_id, s, lo=0.85, hi=1.15)

    m_best = {
        "engagement_proxy": _fmt_pct(amp * 8 * j("gb")),
        "delivery_risk": _fmt_pct(-amp * 5 * j("gbr")),
    }
    m_exp = {
        "engagement_proxy": _fmt_pct(amp * 2),
        "delivery_risk": "0.0%",
    }
    m_worst = {
        "engagement_proxy": _fmt_pct(-amp * 10 * j("gw")),
        "delivery_risk": _fmt_pct(amp * 12 * j("gwr")),
    }

    cap_ratio = (monthly / cap) if (monthly is not None and cap and cap > 0) else 0.5
    cost_stress = min(1.0, cap_ratio + (1.0 - sw) * 0.2)

    def costs(best_mult: float, exp_mult: float, worst_mult: float) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        base = monthly if monthly is not None else 0.0
        return (
            {
                "monthly_spend_delta_usd": f"{best_mult * base * 0.05:+.2f}",
                "vs_cap_headroom": _fmt_pct(-cost_stress * 3 * best_mult),
            },
            {
                "monthly_spend_delta_usd": f"{exp_mult * base * 0.02:+.2f}",
                "vs_cap_headroom": _fmt_pct(-cost_stress),
            },
            {
                "monthly_spend_delta_usd": f"{worst_mult * base * 0.12:+.2f}",
                "vs_cap_headroom": _fmt_pct(-cost_stress * 8 * worst_mult),
            },
        )

    cb, ce, cw = costs(0.5, 1.0, 1.2)

    risks_best = [
        "Assumes no new external dependency failures; validate with a small canary.",
        f"Lifecycle stage {stage.value} implies limited historical evidence for upside.",
    ]
    risks_exp = [
        "Baseline assumes current signal mix and spend trajectory continue.",
    ]
    risks_worst = [
        "Ops churn or observability gaps could amplify downside vs this preview.",
        "If spend is already near cap, worst-case overruns escalate review risk.",
    ]
    if baseline.get("signal_record_count", 0) == 0:
        risks_exp.append("No signal bundle — metric deltas are nominal proxies only.")

    return [
        ScenarioOutcome(
            ScenarioKind.BEST,
            "Best case (generic)",
            m_best,
            cb,
            risks_best,
        ),
        ScenarioOutcome(
            ScenarioKind.EXPECTED,
            "Expected case (generic)",
            m_exp,
            ce,
            risks_exp,
        ),
        ScenarioOutcome(
            ScenarioKind.WORST,
            "Worst case (generic)",
            m_worst,
            cw,
            risks_worst,
        ),
    ]


def _experiment_scenarios(
    product_id: str,
    stage: LifecycleStage,
    monthly: float | None,
    cap: float | None,
    baseline: dict[str, Any],
    exp: Experiment,
) -> list[ScenarioOutcome]:
    """Experiment-type and confidence drive branch spread."""
    sw = _stage_weight(stage)
    conf = max(0.05, min(1.0, exp.confidence))
    amp = 0.06 + 0.14 * conf * sw

    et = exp.type if isinstance(exp.type, ExperimentType) else ExperimentType(str(exp.type))

    # Type-specific emphasis (deterministic).
    if et == ExperimentType.GROWTH:
        keys = ("engagement_proxy", "revenue_proxy", "activation_proxy")
        best_d = (18 * amp, 12 * amp, 10 * amp)
        exp_d = (8 * amp, 4 * amp, 3 * amp)
        worst_d = (-12 * amp, -8 * amp, -6 * amp)
    elif et == ExperimentType.COST_REDUCTION:
        keys = ("unit_cost_proxy", "waste_proxy", "engagement_proxy")
        best_d = (-15 * amp, -20 * amp, -2 * amp)
        exp_d = (-7 * amp, -10 * amp, 0.0)
        worst_d = (5 * amp, 8 * amp, -8 * amp)
    elif et == ExperimentType.ENGAGEMENT:
        keys = ("engagement_proxy", "retention_proxy", "revenue_proxy")
        best_d = (22 * amp, 14 * amp, 6 * amp)
        exp_d = (10 * amp, 6 * amp, 2 * amp)
        worst_d = (-14 * amp, -10 * amp, -4 * amp)
    elif et == ExperimentType.CONTENT:
        keys = ("content_throughput", "engagement_proxy", "revenue_proxy")
        best_d = (16 * amp, 12 * amp, 5 * amp)
        exp_d = (7 * amp, 5 * amp, 2 * amp)
        worst_d = (-10 * amp, -8 * amp, -3 * amp)
    else:  # INFRASTRUCTURE
        keys = ("reliability_proxy", "cost_to_serve", "engagement_proxy")
        best_d = (12 * amp, -10 * amp, 4 * amp)
        exp_d = (5 * amp, -4 * amp, 1 * amp)
        worst_d = (-6 * amp, 10 * amp, -6 * amp)

    def j(s: str) -> float:
        return _stable_jitter(product_id, f"{exp.id}:{s}", lo=0.9, hi=1.1)

    def branch(deltas: tuple[float, float, float], salt: str) -> dict[str, str]:
        return {
            keys[0]: _fmt_pct(deltas[0] * j(f"{salt}a")),
            keys[1]: _fmt_pct(deltas[1] * j(f"{salt}b")),
            keys[2]: _fmt_pct(deltas[2] * j(f"{salt}c")),
        }

    base = monthly if monthly is not None else 0.0
    cap_ratio = (monthly / cap) if (monthly is not None and cap and cap > 0) else 0.45

    def cost_triplet() -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
        if et == ExperimentType.COST_REDUCTION:
            return (
                {"monthly_spend_delta_usd": f"{-base * 0.08 * conf:+.2f}", "runway_impact": "positive"},
                {"monthly_spend_delta_usd": f"{-base * 0.03 * conf:+.2f}", "runway_impact": "neutral"},
                {"monthly_spend_delta_usd": f"{base * 0.04:+.2f}", "runway_impact": "negative_if_initiative_fails"},
            )
        if et in (ExperimentType.GROWTH, ExperimentType.ENGAGEMENT, ExperimentType.CONTENT):
            return (
                {"monthly_spend_delta_usd": f"{base * 0.06 * conf:+.2f}", "vs_cap_headroom": _fmt_pct(-cap_ratio * 4)},
                {"monthly_spend_delta_usd": f"{base * 0.02 * conf:+.2f}", "vs_cap_headroom": _fmt_pct(-cap_ratio * 2)},
                {"monthly_spend_delta_usd": f"{base * 0.14:+.2f}", "vs_cap_headroom": _fmt_pct(-cap_ratio * 10)},
            )
        return (
            {"monthly_spend_delta_usd": f"{base * 0.03 * conf:+.2f}", "rework_risk": "low"},
            {"monthly_spend_delta_usd": f"{base * 0.01 * conf:+.2f}", "rework_risk": "medium"},
            {"monthly_spend_delta_usd": f"{base * 0.09:+.2f}", "rework_risk": "high"},
        )

    cb, ce, cw = cost_triplet()

    hyp_snip = (exp.hypothesis or "")[:120]
    risks_best = [
        f"Experiment type={et.value} with confidence={conf:.2f} — upside assumes hypothesis holds: {hyp_snip!r}",
        "Best case still requires measurement discipline (success metrics tracked).",
    ]
    risks_exp = [
        "Expected path assumes typical execution variance and no major outage window.",
    ]
    risks_worst = [
        "Worst case includes initiative cost without matching lift and possible regression.",
        f"Stage={stage.value} lowers tolerance for long payback; review kill/deprecate signals if worsening.",
    ]
    if exp.success_metrics:
        risks_exp.append(f"Success metrics declared: {', '.join(exp.success_metrics[:5])}.")

    return [
        ScenarioOutcome(ScenarioKind.BEST, f"Best case ({et.value})", branch(best_d, "b"), cb, risks_best),
        ScenarioOutcome(ScenarioKind.EXPECTED, f"Expected case ({et.value})", branch(exp_d, "e"), ce, risks_exp),
        ScenarioOutcome(ScenarioKind.WORST, f"Worst case ({et.value})", branch(worst_d, "w"), cw, risks_worst),
    ]


def run_simulation(
    repo_root: Path,
    node: ProductNode,
    *,
    experiment: Experiment | None = None,
) -> SimulationResult:
    """Build a simulation result from product node and optional persisted experiment."""
    repo_root = repo_root.resolve()
    pid = node.id
    baseline = _extract_metrics_from_signals(repo_root, pid)
    monthly = node.cost.monthly_usd
    cap = node.constraints.max_monthly_cost_usd

    if experiment is not None:
        scenarios = _experiment_scenarios(pid, node.lifecycle.stage, monthly, cap, baseline, experiment)
        exp_id = experiment.id
        hyp = experiment.hypothesis
        et = experiment.type.value if isinstance(experiment.type, ExperimentType) else str(experiment.type)
    else:
        scenarios = _generic_scenarios(pid, node.lifecycle.stage, monthly, cap, baseline)
        exp_id = None
        hyp = None
        et = None

    return SimulationResult(
        product_id=pid,
        generated_at_utc=_utc_now(),
        repo_root=str(repo_root),
        baseline_metrics=baseline,
        product_stage=node.lifecycle.stage.value,
        monthly_cost_usd=monthly,
        monthly_cap_usd=cap,
        experiment_id=exp_id,
        experiment_hypothesis=hyp,
        experiment_type=et,
        scenarios=scenarios,
    )
