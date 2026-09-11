"""
Attention allocation across products from local Argus artifacts.

Combines: metrics (signals/economics), priority scores (decisions), trends,
cost economics, and experiments into percentage focus + ignore / push lists.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import loads_json
from argus.decision.portfolio import build_portfolio_from_inventory
from argus.economics.analyze import analyze_inventory
from argus.economics.models import GrowthSignal, ProductEconomics
from argus.experiments.models import ExperimentStatus
from argus.experiments.store import list_experiments
from argus.portfolio.models import (
    AllocationBand,
    PortfolioAllocationResult,
    ProductAllocationInputs,
    ProductAllocationLine,
    ProductCostInput,
    ProductExperimentsInput,
    ProductMetricsInput,
    ProductPriorityInput,
    ProductTrendInput,
)
from argus.products.inventory import build_inventory
from argus.signals.persistence import load_latest_bundle


def _load_trends_by_product(repo: Path) -> dict[str, dict[str, Any]]:
    p = repo.resolve() / "runs" / "trends" / "latest.json"
    if not p.is_file():
        return {}
    try:
        raw = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    summaries = raw.get("summaries")
    if not isinstance(summaries, list):
        return {}
    for item in summaries:
        if not isinstance(item, dict):
            continue
        pid = item.get("product_id")
        if isinstance(pid, str) and pid:
            out[pid] = item
    return out


def _experiments_by_product(repo: Path) -> dict[str, ProductExperimentsInput]:
    counts: dict[str, dict[str, int]] = {}
    for exp in list_experiments(repo):
        d = counts.setdefault(
            exp.product_id,
            {"active": 0, "proposed": 0, "completed": 0, "failed": 0},
        )
        st = exp.status
        if st == ExperimentStatus.ACTIVE:
            d["active"] += 1
        elif st == ExperimentStatus.PROPOSED:
            d["proposed"] += 1
        elif st == ExperimentStatus.COMPLETED:
            d["completed"] += 1
        elif st == ExperimentStatus.FAILED:
            d["failed"] += 1

    out: dict[str, ProductExperimentsInput] = {}
    for pid, d in counts.items():
        out[pid] = ProductExperimentsInput(
            active_count=d["active"],
            proposed_count=d["proposed"],
            completed_count=d["completed"],
            failed_count=d["failed"],
        )
    return out


def _trend_inputs_for_product(
    pid: str,
    trends_map: dict[str, dict[str, Any]],
) -> ProductTrendInput:
    row = trends_map.get(pid)
    if not row:
        return ProductTrendInput(
            trend_flags=[],
            confidence=0.0,
            drift_signal_count=0,
            has_trend_data=False,
        )
    flags = row.get("trend_flags")
    if not isinstance(flags, list):
        flags = []
    dr = row.get("drift_signals")
    drift_n = len(dr) if isinstance(dr, list) else 0
    conf = row.get("confidence")
    try:
        conf_f = float(conf) if conf is not None else 0.0
    except (TypeError, ValueError):
        conf_f = 0.0
    return ProductTrendInput(
        trend_flags=[str(x) for x in flags],
        confidence=max(0.0, min(1.0, conf_f)),
        drift_signal_count=drift_n,
        has_trend_data=True,
    )


def _trend_multiplier(flags: list[str]) -> tuple[float, list[str]]:
    """Return (multiplier, reason tokens) from trend flags."""
    fset = {x.lower() for x in flags}
    reasons: list[str] = []
    mult = 1.0
    if {"drifting", "risk_increasing", "action_thrashing"} & fset:
        mult = max(mult, 1.22)
        reasons.append("trend:elevated_attention")
    if "improving" in fset or "ready_for_scale_review" in fset:
        mult = max(mult, 1.12)
        reasons.append("trend:positive_window")
    if "likely_abandon" in fset or "stagnating" in fset:
        mult = min(mult, 0.72)
        reasons.append("trend:deprioritize")
    if "insufficient_data" in fset and len(fset) <= 1:
        mult *= 0.94
        reasons.append("trend:insufficient_history")
    return mult, reasons


def _experiment_multiplier(ex: ProductExperimentsInput) -> tuple[float, list[str]]:
    """Slight boost when experiments need operator attention."""
    raw = 1.0 + min(0.22, 0.09 * ex.active_count + 0.045 * ex.proposed_count)
    reasons: list[str] = []
    if ex.active_count:
        reasons.append(f"experiments:active={ex.active_count}")
    if ex.proposed_count:
        reasons.append(f"experiments:proposed={ex.proposed_count}")
    return raw, reasons


def _economics_multiplier(
    cost: ProductCostInput,
    priority: ProductPriorityInput,
) -> tuple[float, list[str]]:
    reasons: list[str] = []
    m = 1.0
    roi = cost.roi_estimate
    if roi is not None:
        if roi > 0.12:
            m *= 1.08
            reasons.append("economics:positive_roi")
        elif roi < -0.35:
            m *= 0.78
            reasons.append("economics:weak_roi")
    if cost.cost_spike:
        m *= 1.06
        reasons.append("economics:cost_spike")
    if priority.kill_candidate:
        m *= 0.42
        reasons.append("lifecycle:kill_candidate")
    return m, reasons


def _raw_weight(inp: ProductAllocationInputs) -> tuple[float, list[str]]:
    """Single scalar weight + audit reasons."""
    p = inp.priority.priority_score
    base = max(0.0, float(p)) / 100.0
    if base <= 0 and not inp.trend.has_trend_data and inp.metrics.signal_record_count == 0:
        base = 0.02
    elif base <= 0:
        base = 0.04
    base_w = base**0.88

    reasons: list[str] = [f"priority:{p:.1f}"]
    tm, tr = _trend_multiplier(inp.trend.trend_flags)
    reasons.extend(tr)
    em, er = _experiment_multiplier(inp.experiments)
    reasons.extend(er)
    econ, ee = _economics_multiplier(inp.cost, inp.priority)
    reasons.extend(ee)

    # Drift density nudge when trend data exists
    drift_boost = 1.0
    if inp.trend.drift_signal_count and inp.trend.has_trend_data:
        drift_boost = min(1.18, 1.0 + 0.04 * min(inp.trend.drift_signal_count, 4))
        reasons.append(f"trend:drifts={inp.trend.drift_signal_count}")

    w = base_w * tm * em * econ * drift_boost
    w = max(w, 1e-9)
    return w, reasons


def gather_allocation_inputs(
    repo: Path,
    *,
    products_dir: Path | None = None,
) -> tuple[list[ProductAllocationInputs], dict[str, Any]]:
    """
    Load inventory and join economics, decisions portfolio row, trends, experiments, signals.
    """
    root = repo.resolve()
    inv = build_inventory(root, products_dir=products_dir)
    econ_products, _port = analyze_inventory(root, products_dir=products_dir)
    econ_by_id = {e.product_id: e for e in econ_products}
    all_experiments = list_experiments(root)

    ranked, _detail = build_portfolio_from_inventory(root, products_dir=products_dir)
    priority_by_id: dict[str, ProductPriorityInput] = {}
    for row in ranked:
        priority_by_id[row.product_id] = ProductPriorityInput(
            priority_score=float(row.priority_score),
            top_intent=str(row.top_intent),
            kill_candidate=bool(row.assessment.kill_candidate),
            lifecycle_stage=str(row.lifecycle_stage),
        )

    trends_map = _load_trends_by_product(root)
    exp_by_id = _experiments_by_product(root)

    inputs: list[ProductAllocationInputs] = []
    for pid in sorted(inv.valid.keys()):
        rec = inv.valid[pid]
        node = rec.node
        bundle = load_latest_bundle(root, pid)
        n_sig = len(bundle.records) if bundle is not None else 0

        pri = priority_by_id.get(
            pid,
            ProductPriorityInput(
                priority_score=0.0,
                top_intent="(no_decision_candidate)",
                kill_candidate=False,
                lifecycle_stage=node.lifecycle.stage.value,
            ),
        )

        econ = econ_by_id.get(pid)
        if econ is None:
            m_cost = float(node.cost.monthly_usd or 0)
            econ = ProductEconomics(
                product_id=pid,
                monthly_cost=m_cost,
                estimated_revenue=0.0,
                roi_estimate=None,
                burn_rate=max(0.0, m_cost),
                growth_signal=GrowthSignal.UNKNOWN,
                metadata={},
            )

        meta = econ.metadata or {}
        cost_spike = bool(meta.get("cost_spike_signal"))

        inp = ProductAllocationInputs(
            product_id=pid,
            metrics=ProductMetricsInput(
                estimated_revenue=float(econ.estimated_revenue),
                burn_rate=float(econ.burn_rate),
                growth_signal=(
                    econ.growth_signal.value
                    if hasattr(econ.growth_signal, "value")
                    else str(econ.growth_signal)
                ),
                signal_record_count=n_sig,
            ),
            priority=pri,
            trend=_trend_inputs_for_product(pid, trends_map),
            cost=ProductCostInput(
                monthly_cost_usd=float(econ.monthly_cost),
                roi_estimate=econ.roi_estimate,
                cost_spike=cost_spike,
            ),
            experiments=exp_by_id.get(
                pid,
                ProductExperimentsInput(0, 0, 0, 0),
            ),
        )
        inputs.append(inp)

    summary = {
        "valid_products": len(inv.valid),
        "with_decision_rank": len(ranked),
        "trends_file_present": (root / "runs" / "trends" / "latest.json").is_file(),
        "experiment_records": len(all_experiments),
    }
    return inputs, summary


def compute_allocation(
    inputs: list[ProductAllocationInputs],
    *,
    generated_at_utc: str | None = None,
) -> PortfolioAllocationResult:
    """
    Turn per-product inputs into percentages and ignore / push lists.

    Uses a deterministic weighted blend; percentages sum to ~100.
    """
    now = generated_at_utc or datetime.now(timezone.utc).isoformat()
    if not inputs:
        return PortfolioAllocationResult(generated_at_utc=now, inputs_summary={"products": 0})

    weights: list[tuple[ProductAllocationInputs, float, list[str]]] = []
    for inp in inputs:
        w, reasons = _raw_weight(inp)
        weights.append((inp, w, reasons))

    total = sum(w for _i, w, _r in weights)
    lines: list[ProductAllocationLine] = []
    for inp, w, reasons in weights:
        pct = 100.0 * (w / total) if total > 0 else 0.0
        lines.append(
            ProductAllocationLine(
                product_id=inp.product_id,
                focus_pct=round(pct, 2),
                band=AllocationBand.MAINTAIN,
                priority_score=inp.priority.priority_score,
                raw_weight=w,
                reasons=reasons,
            )
        )

    lines.sort(key=lambda x: (-x.focus_pct, x.product_id))
    n = len(lines)
    max_pct = lines[0].focus_pct if lines else 0.0

    # Top quartile by rank (stable tie-break on product_id) — avoids "everyone pushes" on flat ties.
    k_push = max(1, math.ceil(n * 0.25))
    top_quartile_ids = {line.product_id for line in lines[:k_push]}

    push_ids: list[str] = []
    ignore_ids: list[str] = []

    by_id = {i.product_id: i for i in inputs}
    for line in lines:
        inp = by_id[line.product_id]
        push = False
        if line.product_id in top_quartile_ids and line.focus_pct >= max(8.0, 0.12 * max_pct):
            push = True
        elif line.priority_score >= 68.0 and (
            inp.experiments.active_count > 0 or inp.trend.drift_signal_count > 0
        ):
            push = True
        elif line.priority_score >= 75.0:
            push = True

        ignore = False
        if inp.priority.kill_candidate and line.priority_score < 28.0:
            ignore = True
        elif "likely_abandon" in {x.lower() for x in inp.trend.trend_flags} and line.priority_score < 40.0:
            ignore = True
        elif line.focus_pct <= min(3.5, 0.08 * max_pct) and line.priority_score < 32.0:
            ignore = True

        if push and ignore:
            ignore = False

        if push:
            line.band = AllocationBand.PUSH
            push_ids.append(line.product_id)
        elif ignore:
            line.band = AllocationBand.IGNORE
            ignore_ids.append(line.product_id)
        else:
            line.band = AllocationBand.MAINTAIN

    return PortfolioAllocationResult(
        generated_at_utc=now,
        products=lines,
        ignore_product_ids=sorted(set(ignore_ids)),
        push_hard_product_ids=sorted(set(push_ids)),
        inputs_summary={"product_count": n},
    )


def run_allocation(
    repo: Path,
    *,
    products_dir: Path | None = None,
) -> tuple[PortfolioAllocationResult, dict[str, Any]]:
    """Gather inputs and compute allocation (single entry for CLI)."""
    inputs, gather_summary = gather_allocation_inputs(repo, products_dir=products_dir)
    result = compute_allocation(inputs)
    gather_summary["gather"] = "ok"
    result.inputs_summary.update(gather_summary)
    return result, gather_summary
