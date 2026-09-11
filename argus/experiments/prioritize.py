"""
Deterministic prioritization of proposed experiments (no execution).

Scores combine expected impact, product cost posture, confidence, strategy weights,
and lifecycle fit — aligned with ``argus.strategy`` profiles and decision priority math.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import LifecycleStage
from argus.core.models.product import ProductNode
from argus.core.serialize import dumps_json, loads_json, to_jsonable
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.doctrine.load import load_doctrine_for_product
from argus.experiments.models import (
    ExperimentProposal,
    ExperimentType,
    PrioritizationRun,
    PrioritizedProposal,
    ProposalRun,
)
from argus.experiments.propose import proposal_from_dict, propose_experiments
from argus.products.inventory import ProductInventory, build_inventory
from argus.strategy.apply import get_strategy_profile
from argus.strategy.modes import StrategyProfile
from argus.strategy.uncertainty import leap_of_faith_adjusted_confidence


def _effort_str_to_penalty(s: str) -> float:
    m = {
        "trivial": 0.08,
        "small": 0.22,
        "medium": 0.42,
        "large": 0.62,
        "xlarge": 0.82,
    }
    return m.get(str(s).lower().strip(), 0.35)


def _impact_by_type(etype: ExperimentType) -> float:
    """Expected upside / leverage of the experiment category (0–1)."""
    return {
        ExperimentType.GROWTH: 0.78,
        ExperimentType.COST_REDUCTION: 0.62,
        ExperimentType.ENGAGEMENT: 0.72,
        ExperimentType.CONTENT: 0.68,
        ExperimentType.INFRASTRUCTURE: 0.58,
    }.get(etype, 0.55)


def _urgency_from_proposal(p: ExperimentProposal) -> float:
    """Proxy for time-sensitivity from proposal rationale (deterministic)."""
    r = p.rationale.lower()
    if "kill_candidate" in r:
        return 0.9
    if "risk_increasing" in r or "cost_risk" in r:
        return 0.88
    if "reliability" in r or "retention" in r:
        return 0.76
    if "inactivity" in r or "stagnating" in r:
        return 0.72
    if "deprecation" in r:
        return 0.68
    if "decision_intent" in r:
        return 0.65
    return 0.48


def _lifecycle_fit(stage: LifecycleStage, et: ExperimentType) -> float:
    """How well the experiment type matches the product lifecycle stage (0–1)."""
    if stage == LifecycleStage.IDEA:
        return {
            ExperimentType.INFRASTRUCTURE: 0.7,
            ExperimentType.GROWTH: 0.55,
            ExperimentType.ENGAGEMENT: 0.48,
            ExperimentType.CONTENT: 0.45,
            ExperimentType.COST_REDUCTION: 0.4,
        }.get(et, 0.48)
    if stage == LifecycleStage.BUILD:
        return {
            ExperimentType.INFRASTRUCTURE: 0.75,
            ExperimentType.GROWTH: 0.62,
            ExperimentType.ENGAGEMENT: 0.55,
            ExperimentType.CONTENT: 0.5,
            ExperimentType.COST_REDUCTION: 0.42,
        }.get(et, 0.5)
    if stage == LifecycleStage.VALIDATE:
        return {
            ExperimentType.GROWTH: 0.76,
            ExperimentType.ENGAGEMENT: 0.74,
            ExperimentType.CONTENT: 0.65,
            ExperimentType.INFRASTRUCTURE: 0.58,
            ExperimentType.COST_REDUCTION: 0.48,
        }.get(et, 0.52)
    if stage == LifecycleStage.GROW:
        return {
            ExperimentType.GROWTH: 0.8,
            ExperimentType.CONTENT: 0.78,
            ExperimentType.ENGAGEMENT: 0.77,
            ExperimentType.INFRASTRUCTURE: 0.62,
            ExperimentType.COST_REDUCTION: 0.54,
        }.get(et, 0.53)
    if stage == LifecycleStage.MAINTAIN:
        return {
            ExperimentType.COST_REDUCTION: 0.72,
            ExperimentType.CONTENT: 0.7,
            ExperimentType.ENGAGEMENT: 0.68,
            ExperimentType.GROWTH: 0.65,
            ExperimentType.INFRASTRUCTURE: 0.6,
        }.get(et, 0.55)
    if stage == LifecycleStage.DECLINE:
        return {
            ExperimentType.COST_REDUCTION: 0.85,
            ExperimentType.ENGAGEMENT: 0.7,
            ExperimentType.INFRASTRUCTURE: 0.55,
            ExperimentType.GROWTH: 0.5,
            ExperimentType.CONTENT: 0.52,
        }.get(et, 0.5)
    if stage == LifecycleStage.KILL:
        return {
            ExperimentType.COST_REDUCTION: 0.88,
            ExperimentType.ENGAGEMENT: 0.45,
            ExperimentType.INFRASTRUCTURE: 0.48,
            ExperimentType.GROWTH: 0.35,
            ExperimentType.CONTENT: 0.4,
        }.get(et, 0.38)
    return 0.5


def _cost_penalty_from_product(monthly: float | None, cap: float | None, profile: StrategyProfile) -> float:
    """Spend pressure vs cap (0–1), scaled by strategy (same spirit as decision priority)."""
    if monthly is None or cap is None or cap <= 0:
        base = 0.15
    else:
        ratio = min(1.5, monthly / cap)
        base = min(1.0, max(0.0, ratio * 0.55))
    return min(1.0, max(0.0, base * profile.cost_penalty_input_scale))


def _is_launch_like(p: ExperimentProposal) -> bool:
    if p.type in (
        ExperimentType.GROWTH,
        ExperimentType.ENGAGEMENT,
        ExperimentType.CONTENT,
    ):
        return True
    r = p.rationale
    return "launch_experiment" in r or "intent_exp" in r


def _score_components(
    p: ExperimentProposal,
    *,
    stage: LifecycleStage,
    node: ProductNode | None,
    profile: StrategyProfile,
    repo_root: Path | None = None,
) -> tuple[float, float, float, float, float, float, float, float, float]:
    """
    Return (score_0_100, impact, urgency, conf, lc_fit, cost_p, eff_p, strat_raw_0_1, exp_mult_applied).
    """
    impact = _impact_by_type(p.type)
    urgency = _urgency_from_proposal(p)
    conf = max(0.0, min(1.0, p.confidence))
    if _is_launch_like(p):
        conf, _ = leap_of_faith_adjusted_confidence(conf, profile)
    lc = _lifecycle_fit(stage, p.type)
    monthly = node.cost.monthly_usd if node else None
    cap = node.constraints.max_monthly_cost_usd if node else None
    cost_p = _cost_penalty_from_product(monthly, cap, profile)
    eff_p = _effort_str_to_penalty(p.estimated_effort)

    raw = (
        profile.w_impact * impact
        + profile.w_confidence * conf
        + profile.w_urgency * urgency
        + profile.w_lifecycle_fit * lc
        - profile.w_cost_penalty * cost_p
        - profile.w_effort_penalty * eff_p
    )
    raw = max(0.0, min(1.0, raw))
    if repo_root is not None and node is not None:
        doc, _ = load_doctrine_for_product(
            repo_root, p.product_id, product_root=node.product_root
        )
        if doc is not None and doc.scoring.experiment_score_boost:
            raw = min(1.0, raw + doc.scoring.experiment_score_boost)
    launch = _is_launch_like(p)
    mult = profile.experiment_score_multiplier if launch else 1.0
    score = min(100.0, 100.0 * raw * mult)
    return round(score, 2), impact, urgency, conf, lc, cost_p, eff_p, raw, mult


def prioritization_latest_path(repo_root: Path, product_id: str) -> Path:
    """Deterministic path for ``argus.experiment_prioritization_run.v2`` for one product."""
    return repo_root.resolve() / "runs" / "experiments" / "prioritization" / "latest" / f"{product_id}.json"


def save_prioritization_run_latest(repo_root: Path, product_id: str, run: PrioritizationRun) -> Path:
    """Write ``PrioritizationRun`` JSON for ``product_id`` (advisory ranking only)."""
    path = prioritization_latest_path(repo_root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(to_jsonable(run)), encoding="utf-8")
    return path


def prioritized_proposal_from_dict(d: dict[str, Any]) -> PrioritizedProposal:
    """Deserialize ``PrioritizedProposal`` from persisted prioritization JSON."""
    pd = d.get("proposal")
    if not isinstance(pd, dict):
        raise ValueError("prioritized row missing proposal object")
    prop = proposal_from_dict(pd)
    return PrioritizedProposal(
        rank=int(d["rank"]),
        score=float(d["score"]),
        impact=float(d["impact"]),
        urgency=float(d["urgency"]),
        confidence=float(d["confidence"]),
        lifecycle_fit=float(d["lifecycle_fit"]),
        cost_penalty=float(d["cost_penalty"]),
        effort_penalty=float(d["effort_penalty"]),
        strategy_alignment_raw=float(d["strategy_alignment_raw"]),
        experiment_multiplier=float(d["experiment_multiplier"]),
        proposal=prop,
        schema=str(d.get("schema", "argus.prioritized_experiment_proposal.v1")),
    )


def load_prioritization_run_latest(repo_root: Path, product_id: str) -> PrioritizationRun:
    """Load ``PrioritizationRun`` from ``prioritization_latest_path`` (canonical input for materialize)."""
    path = prioritization_latest_path(repo_root, product_id)
    if not path.is_file():
        raise FileNotFoundError(str(path))
    raw = loads_json(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("prioritization JSON must be an object")
    bp_raw = raw.get("by_product") or {}
    by_product: dict[str, list[PrioritizedProposal]] = {}
    if isinstance(bp_raw, dict):
        for pid, rows in bp_raw.items():
            if not isinstance(rows, list):
                continue
            parsed: list[PrioritizedProposal] = []
            for row in rows:
                if isinstance(row, dict):
                    parsed.append(prioritized_proposal_from_dict(row))
            by_product[str(pid)] = parsed
    tr_raw = raw.get("top_recommendations") or []
    top_recs: list[PrioritizedProposal] = []
    if isinstance(tr_raw, list):
        for row in tr_raw:
            if isinstance(row, dict):
                top_recs.append(prioritized_proposal_from_dict(row))
    dc_raw = raw.get("decision_context_by_product") or {}
    dc: dict[str, dict[str, Any]] = {}
    if isinstance(dc_raw, dict):
        for k, v in dc_raw.items():
            if isinstance(v, dict):
                dc[str(k)] = dict(v)
    return PrioritizationRun(
        generated_at_utc=str(raw.get("generated_at_utc") or ""),
        repo_root=str(raw.get("repo_root") or ""),
        strategy_mode=(None if raw.get("strategy_mode") in (None, "") else str(raw["strategy_mode"])),
        by_product=dict(sorted(by_product.items())),
        top_recommendations=top_recs,
        decision_context_by_product=dc,
        schema=str(raw.get("schema", "argus.experiment_prioritization_run.v2")),
    )


def prioritization_latest_has_ranked_for_product(repo_root: Path, product_id: str) -> bool:
    """True when persisted prioritization lists at least one ranked row for ``product_id``."""
    p = prioritization_latest_path(repo_root, product_id)
    if not p.is_file():
        return False
    try:
        raw = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return False
    if not isinstance(raw, dict):
        return False
    bp = raw.get("by_product") or {}
    if not isinstance(bp, dict):
        return False
    rows = bp.get(product_id)
    return isinstance(rows, list) and len(rows) > 0


def prioritize_experiments_from_proposal_run(
    repo_root: Path,
    proposal_run: ProposalRun,
    *,
    inventory: ProductInventory | None = None,
) -> PrioritizationRun:
    """
    Rank proposals from an existing ``ProposalRun`` (e.g. loaded from ``runs/experiments/proposals/latest``).

    Does not generate or refresh proposals.
    """
    root = repo_root.resolve()
    now = datetime.now(timezone.utc).isoformat()
    inv = inventory or build_inventory(root)
    profile = get_strategy_profile(root)
    mode_label = profile.mode.value if profile.mode else None

    grouped: dict[str, list[tuple[float, ExperimentProposal, dict[str, Any]]]] = defaultdict(list)
    for prop in proposal_run.proposals:
        rec = inv.valid.get(prop.product_id)
        node = rec.node if rec else None
        stage = node.lifecycle.stage if node else LifecycleStage.IDEA
        t = _score_components(
            prop, stage=stage, node=node, profile=profile, repo_root=root
        )
        score, impact, urgency, conf, lc, cost_p, eff_p, raw, mult = t
        grouped[prop.product_id].append(
            (
                score,
                prop,
                {
                    "impact": impact,
                    "urgency": urgency,
                    "confidence": conf,
                    "lifecycle_fit": lc,
                    "cost_penalty": cost_p,
                    "effort_penalty": eff_p,
                    "strategy_alignment_raw": raw,
                    "experiment_multiplier": mult,
                },
            )
        )

    by_pid: dict[str, list[PrioritizedProposal]] = {}
    for pid in sorted(grouped.keys()):
        rows = sorted(
            grouped[pid],
            key=lambda x: (-x[0], x[1].proposal_id),
        )
        out: list[PrioritizedProposal] = []
        for rank, (score, prop, comp) in enumerate(rows, start=1):
            out.append(
                PrioritizedProposal(
                    rank=rank,
                    score=score,
                    impact=float(comp["impact"]),
                    urgency=float(comp["urgency"]),
                    confidence=float(comp["confidence"]),
                    lifecycle_fit=float(comp["lifecycle_fit"]),
                    cost_penalty=float(comp["cost_penalty"]),
                    effort_penalty=float(comp["effort_penalty"]),
                    strategy_alignment_raw=float(comp["strategy_alignment_raw"]),
                    experiment_multiplier=float(comp["experiment_multiplier"]),
                    proposal=prop,
                )
            )
        by_pid[pid] = out

    flat = sorted(
        (p for plist in by_pid.values() for p in plist),
        key=lambda r: (-r.score, r.proposal.product_id, r.proposal.proposal_id),
    )
    top = flat[:3]

    decision_context_by_product: dict[str, dict[str, Any]] = {}
    for pid in inv.valid.keys():
        try:
            decision_context_by_product[pid] = evaluate_decision_context(root, pid).to_jsonable()
        except ValueError:
            continue

    return PrioritizationRun(
        generated_at_utc=now,
        repo_root=str(root),
        strategy_mode=mode_label,
        by_product=dict(sorted(by_pid.items())),
        top_recommendations=top,
        decision_context_by_product=decision_context_by_product,
    )


def prioritize_experiments(
    repo_root: Path,
    *,
    product_id: str | None = None,
    inventory: ProductInventory | None = None,
) -> PrioritizationRun:
    """Propose experiments, then rank by deterministic strategy-aware score."""
    root = repo_root.resolve()
    inv = inventory or build_inventory(root)
    proposal_run = propose_experiments(root, product_id=product_id, inventory=inv)
    return prioritize_experiments_from_proposal_run(root, proposal_run, inventory=inv)


def format_prioritization_text(run: PrioritizationRun) -> str:
    """Human-readable summary for CLI."""
    lines = [
        "Experiment prioritization (deterministic; uses current strategy profile)",
        f"Generated: {run.generated_at_utc}",
        f"Strategy mode: {run.strategy_mode or 'default (legacy weights)'}",
        "",
        "Top recommendations (portfolio-wide, up to 3):",
    ]
    if not run.top_recommendations:
        lines.append("  (none)")
    else:
        for i, r in enumerate(run.top_recommendations, start=1):
            p = r.proposal
            lines.append(
                f"  {i}. score={r.score:.2f}  [{p.type.value}] {p.product_id} — {p.hypothesis}"
            )
            lines.append(f"     id: {p.proposal_id}  rationale: {p.rationale}")
    lines.extend(["", "Per product (highest score first):", ""])
    for pid in sorted(run.by_product.keys()):
        rows = run.by_product[pid]
        lines.append(f"## {pid}")
        if not rows:
            lines.append("  (no proposals)")
            continue
        for r in rows:
            p = r.proposal
            lines.append(
                f"  {r.rank}. score={r.score:.2f}  [{p.type.value}] {p.hypothesis}"
            )
            lines.append(
                f"      impact={r.impact:.2f} conf={r.confidence:.2f} "
                f"lc_fit={r.lifecycle_fit:.2f} cost_p={r.cost_penalty:.2f} "
                f"effort_p={r.effort_penalty:.2f}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
