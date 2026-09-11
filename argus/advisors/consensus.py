"""Combine advisor responses into consensus, disagreement, and confidence."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from argus.advisors.models import Advisor, AdvisorResponse, AdvisorRunResult, ConsensusResult
from argus.advisors.registry import resolve_advisors
from argus.advisors.runner import _stance, run_advisors
from argus.advisors.temporal import TemporalGrounding


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    num = sum(v * w for v, w in zip(values, weights, strict=True))
    den = sum(weights)
    if den <= 0:
        return sum(values) / max(1, len(values))
    return num / den


def _weighted_std(values: list[float], weights: list[float], mean: float) -> float:
    den = sum(weights)
    if den <= 0 or len(values) < 2:
        return 0.0
    var = sum(w * (v - mean) ** 2 for v, w in zip(values, weights, strict=True)) / den
    return var**0.5


DECISION_BUCKETS = (
    "favor_prudent_hold",
    "favor_measured_progress",
    "favor_accelerated_bets",
)

FINAL_RECOMMENDATION_TEXT: dict[str, str] = {
    "favor_prudent_hold": (
        "Lean toward a prudent hold: stabilize execution, reduce risk, and clarify gates before new spend."
    ),
    "favor_measured_progress": (
        "Lean toward measured progress: ship bounded next steps with explicit success metrics."
    ),
    "favor_accelerated_bets": (
        "Lean toward accelerated bets: increase deliberate investment where guardrails and signals align."
    ),
}


def build_consensus(
    repo_root: Path,
    product_id: str,
    advisors: list[Advisor],
    responses: list[AdvisorResponse],
    *,
    temporal_grounding: TemporalGrounding | None = None,
) -> ConsensusResult:
    """
    Deterministic consensus from advisor stances (``metadata["stance"]`` or stub hash).

    ``confidence_score`` blends agreement spread with per-advisor confidence when present.
    """
    assert len(advisors) == len(responses)
    now = datetime.now(timezone.utc).isoformat()

    stances: list[float] = []
    weights: list[float] = []
    stance_map: dict[str, float] = {}

    for adv, resp in zip(advisors, responses, strict=True):
        md = resp.metadata or {}
        st = md.get("stance")
        try:
            st_f = float(st) if st is not None else float(_stance(product_id, adv.id))
        except (TypeError, ValueError):
            st_f = float(_stance(product_id, adv.id))
        stances.append(st_f)
        w = max(0.0, adv.weight)
        weights.append(w)
        stance_map[adv.id] = st_f

    wmean = _weighted_mean(stances, weights)
    wstd = _weighted_std(stances, weights, wmean)

    spread_penalty = min(1.0, wstd * 2.5)
    confidences = [r.confidence for r in responses if r.confidence is not None]
    if confidences:
        mean_adv_conf = sum(confidences) / len(confidences)
        confidence = max(
            0.0,
            min(1.0, 0.45 * (1.0 - spread_penalty) + 0.55 * mean_adv_conf),
        )
    else:
        confidence = max(0.0, min(1.0, 1.0 - spread_penalty))

    if temporal_grounding is not None:
        fresh_penalty = temporal_grounding.overall_freshness_risk
    else:
        pens = []
        for r in responses:
            md = r.metadata or {}
            p = md.get("temporal_freshness_penalty")
            if p is not None:
                try:
                    pens.append(float(p))
                except (TypeError, ValueError):
                    pass
        fresh_penalty = sum(pens) / len(pens) if pens else 0.0

    freshness_adjustment_applied = min(1.0, fresh_penalty)
    confidence = max(0.0, confidence * (1.0 - 0.55 * freshness_adjustment_applied))

    if temporal_grounding is not None:
        temporal_evidence_summary = temporal_grounding.one_line_summary()
    else:
        temporal_evidence_summary = ""
        if responses:
            md0 = responses[0].metadata or {}
            tl = md0.get("temporal_summary_line")
            if isinstance(tl, str) and tl:
                temporal_evidence_summary = tl

    disagreement: list[str] = []
    threshold = 0.18
    for adv, st in zip(advisors, stances, strict=True):
        if abs(st - wmean) > threshold:
            direction = "above" if st > wmean else "below"
            disagreement.append(
                f"{adv.id} ({adv.archetype.value}) stance is {direction} "
                f"the weighted mean ({st:.3f} vs {wmean:.3f})"
            )

    bucket = int(wmean * len(DECISION_BUCKETS)) % len(DECISION_BUCKETS)
    consensus_decision = DECISION_BUCKETS[bucket]
    final_recommendation = FINAL_RECOMMENDATION_TEXT.get(
        consensus_decision,
        "Review advisor outputs and align on a single next gate.",
    )
    disagreement_summary = (
        "; ".join(disagreement[:8])
        if disagreement
        else "No strong cross-advisor disagreement vs the weighted mean stance."
    )

    return ConsensusResult(
        product_id=product_id,
        repo_root=str(repo_root.resolve()),
        generated_at_utc=now,
        consensus_decision=consensus_decision,
        final_recommendation=final_recommendation,
        disagreement_signals=disagreement,
        disagreement_summary=disagreement_summary,
        confidence_score=round(confidence, 4),
        per_advisor_stance={k: round(v, 6) for k, v in stance_map.items()},
        source_responses=list(responses),
        temporal_evidence_summary=temporal_evidence_summary,
        freshness_adjustment_applied=round(freshness_adjustment_applied, 4),
    )


def run_consensus(
    repo_root: Path,
    product_id: str,
    *,
    use_llm: bool | None = None,
) -> tuple[AdvisorRunResult, ConsensusResult]:
    """
    Run advisors then compute consensus; writes ``consensus.json`` into the consultation log if present.

    ``use_llm`` is passed through to :func:`run_advisors` (``None`` = auto from env).
    """
    advisors = resolve_advisors(repo_root, product_id)
    run = run_advisors(repo_root, product_id, advisors=advisors, use_llm=use_llm)
    tg = run.temporal_grounding
    cons = build_consensus(
        repo_root,
        product_id,
        advisors,
        run.responses,
        temporal_grounding=tg if isinstance(tg, TemporalGrounding) else None,
    )
    if run.consultation_log_dir:
        from argus.advisors.consult_log import write_consensus_sidecar
        from argus.core.serialize import to_jsonable

        write_consensus_sidecar(Path(run.consultation_log_dir), to_jsonable(cons))
    return run, cons


def run_consultation(
    repo_root: Path,
    product_id: str,
    *,
    use_llm: bool | None = None,
) -> tuple[AdvisorRunResult, ConsensusResult]:
    """Alias for :func:`run_consensus` (full “board” consultation)."""
    return run_consensus(repo_root, product_id, use_llm=use_llm)
