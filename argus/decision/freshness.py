"""
Freshness gating for decisions tied to **operational** signal timestamps.

**Naming:** ``TemporalFreshnessContext`` / ``assess_temporal_freshness`` refer to
time-sensitive **metrics / analytics / cost / health** rows in the latest signal
bundle — *not* :class:`~argus.core.models.enums.SignalType.TEMPORAL` snapshot
signals (market/news/recency files). Snapshot ``TEMPORAL`` rows are evaluated
separately via finding rules and ``payload.temporal``.

Only a small set of finding kinds are **freshness-gated** (growth, cost risk,
launch posture, retention). Others are left unchanged to avoid overfitting
every decision to signal timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.models.enums import FindingKind, SignalType
from argus.core.models.finding import Finding
from argus.decision.intents import DecisionIntent
from argus.decision.priority import attach_priority
from argus.lifecycle.model import LifecycleAssessment
from argus.signals.persistence import load_latest_bundle
from argus.strategy.modes import LEGACY_PROFILE, StrategyProfile

# Observations newer than this (hours) count as "recent" for messaging.
_RECENT_HOURS = 72.0
# Beyond this age (hours), temporal inputs are treated as stale for gated decisions.
_STALE_HOURS = 168.0


def _utc_now(now: datetime | None) -> datetime:
    n = now or datetime.now(timezone.utc)
    if n.tzinfo is None:
        return n.replace(tzinfo=timezone.utc)
    return n


def _hours_between(later: datetime, earlier: datetime) -> float:
    return (later - earlier).total_seconds() / 3600.0


@dataclass(frozen=True)
class TemporalFreshnessContext:
    """Summarizes newest **operational** signal observations (metrics, analytics, cost, health) for one product."""

    product_id: str
    bundle_present: bool
    collected_at_utc: str | None
    newest_temporal_observed_at: str | None
    temporal_types_present: frozenset[str]
    age_hours_newest_temporal: float | None


def _required_types_for_finding(kind: FindingKind | None) -> frozenset[SignalType] | None:
    """Return required signal types for freshness gating, or None if not gated."""
    if kind == FindingKind.GROWTH_OPPORTUNITY:
        return frozenset({SignalType.METRICS, SignalType.ANALYTICS})
    if kind == FindingKind.COST_RISK:
        return frozenset({SignalType.COST})
    if kind == FindingKind.LAUNCH_CANDIDATE:
        return frozenset({SignalType.METRICS, SignalType.ANALYTICS, SignalType.HEALTH})
    if kind == FindingKind.STRUCTURAL_READINESS:
        return frozenset({SignalType.METRICS, SignalType.HEALTH})
    if kind in (FindingKind.VALIDATION_READINESS, FindingKind.VALIDATION_EVIDENCE_GAP):
        return frozenset({SignalType.METRICS, SignalType.ANALYTICS, SignalType.HEALTH})
    if kind == FindingKind.RETENTION_PROBLEM:
        return frozenset({SignalType.METRICS, SignalType.ANALYTICS})
    if kind == FindingKind.RELIABILITY_PROBLEM:
        return frozenset({SignalType.METRICS, SignalType.ANALYTICS, SignalType.HEALTH})
    return None


def assess_temporal_freshness(
    repo_root: Path,
    product_id: str,
    *,
    now: datetime | None = None,
) -> TemporalFreshnessContext:
    """Load latest signal bundle and summarize operational-signal recency (deterministic)."""
    bundle = load_latest_bundle(repo_root, product_id)
    clock = _utc_now(now)
    if bundle is None or not bundle.records:
        return TemporalFreshnessContext(
            product_id=product_id,
            bundle_present=bundle is not None,
            collected_at_utc=bundle.collected_at_utc if bundle else None,
            newest_temporal_observed_at=None,
            temporal_types_present=frozenset(),
            age_hours_newest_temporal=None,
        )

    temporal_types = frozenset(
        {
            SignalType.ANALYTICS,
            SignalType.METRICS,
            SignalType.COST,
            SignalType.HEALTH,
        }
    )
    newest: datetime | None = None
    present: set[str] = set()
    for r in bundle.records:
        if r.signal_type not in temporal_types:
            continue
        present.add(r.signal_type.value)
        o = r.observed_at
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        if newest is None or o > newest:
            newest = o

    newest_iso = newest.isoformat() if newest else None
    age_h: float | None = None
    if newest is not None:
        age_h = _hours_between(clock, newest)

    return TemporalFreshnessContext(
        product_id=product_id,
        bundle_present=True,
        collected_at_utc=bundle.collected_at_utc or None,
        newest_temporal_observed_at=newest_iso,
        temporal_types_present=frozenset(present),
        age_hours_newest_temporal=age_h,
    )


def _freshness_inputs_dict(ctx: TemporalFreshnessContext) -> dict[str, Any]:
    return {
        "signals_bundle_collected_at_utc": ctx.collected_at_utc,
        "newest_temporal_observed_at": ctx.newest_temporal_observed_at,
        "temporal_types_present": sorted(ctx.temporal_types_present),
        "age_hours_newest_temporal": ctx.age_hours_newest_temporal,
        "recent_threshold_hours": _RECENT_HOURS,
        "stale_threshold_hours": _STALE_HOURS,
    }


def _evaluate_gate(
    repo_root: Path,
    product_id: str,
    required: frozenset[SignalType],
    *,
    now: datetime | None,
) -> tuple[str, list[str]]:
    """Classify ok | stale | missing using per-record times for required types."""
    warnings: list[str] = []
    bundle = load_latest_bundle(repo_root, product_id)
    clock = _utc_now(now)
    if bundle is None:
        warnings.append("No signals bundle (runs/signals/latest/ missing); run `argus signals collect`.")
        return "missing", warnings

    newest: datetime | None = None
    have: set[SignalType] = set()
    for r in bundle.records:
        if r.signal_type not in required:
            continue
        have.add(r.signal_type)
        o = r.observed_at
        if o.tzinfo is None:
            o = o.replace(tzinfo=timezone.utc)
        if newest is None or o > newest:
            newest = o

    if len(have) == 0:
        warnings.append(
            "Missing temporal signals for this finding (need "
            f"{', '.join(sorted(t.value for t in required))})."
        )
        return "missing", warnings

    if newest is None:
        return "missing", warnings

    age = _hours_between(clock, newest)
    if age > _STALE_HOURS:
        warnings.append(
            f"Temporal signals are stale (newest required observation is {age:.1f}h old; "
            f"threshold {_STALE_HOURS:.0f}h)."
        )
        return "stale", warnings

    if age > _RECENT_HOURS:
        warnings.append(
            f"Temporal signals are usable but not recent (newest {age:.1f}h old; "
            f"recent threshold {_RECENT_HOURS:.0f}h)."
        )
    return "ok", warnings


def _scale_confidence(base: float | None, factor: float) -> float:
    v = 0.55 if base is None else float(base)
    return max(0.05, min(0.99, v * factor))


def apply_freshness_to_candidates(
    repo_root: Path | None,
    product_id: str,
    findings: list[Finding],
    candidates: list[DecisionCandidate],
    assessment: LifecycleAssessment,
    *,
    monthly_spend: float | None,
    spend_cap: float | None,
    strategy_profile: StrategyProfile | None,
    now: datetime | None = None,
) -> list[DecisionCandidate]:
    """
    Annotate candidates with freshness metadata; adjust confidence when gated inputs fail.

    Recomputes priority scores after adjustments and re-sorts by priority.
    """
    if repo_root is None:
        return candidates

    prof = strategy_profile if strategy_profile is not None else LEGACY_PROFILE
    f_missing = prof.freshness_missing_confidence_factor
    f_stale = prof.freshness_stale_confidence_factor

    ctx = assess_temporal_freshness(repo_root, product_id, now=now)
    base_inputs = _freshness_inputs_dict(ctx)
    by_fid = {f.id: f for f in findings}

    out: list[DecisionCandidate] = []
    for c in candidates:
        md = dict(c.metadata or {})
        md["freshness_inputs"] = dict(base_inputs)
        fid = md.get("finding_id")
        finding = by_fid.get(str(fid)) if fid else None
        kind = finding.kind if finding is not None else None
        required = _required_types_for_finding(kind)

        intent_str = md.get("intent")
        try:
            intent = DecisionIntent(str(intent_str)) if intent_str else None
        except ValueError:
            intent = None

        warnings: list[str] = []
        stale_affected = False
        recommend_gather = False
        escalation = False

        if required is not None and finding is not None:
            status, w = _evaluate_gate(repo_root, product_id, required, now=now)
            warnings.extend(w)
            if status == "missing":
                c.confidence = _scale_confidence(c.confidence, f_missing)
                stale_affected = True
                recommend_gather = True
            elif status == "stale":
                c.confidence = _scale_confidence(c.confidence, f_stale)
                stale_affected = True
                if intent == DecisionIntent.LAUNCH_EXPERIMENT:
                    escalation = True
            elif status == "ok":
                pass

        if warnings:
            md["freshness_warnings"] = warnings
        md["freshness_confidence_factors"] = {
            "missing": f_missing,
            "stale": f_stale,
        }
        md["stale_data_affected_confidence"] = stale_affected
        if recommend_gather:
            md["freshness_recommend_gather_data"] = True
        else:
            md.setdefault("freshness_recommend_gather_data", False)
        md["freshness_escalation"] = escalation

        c.metadata = md
        f2 = finding if finding is not None else None
        attach_priority(
            c,
            finding=f2,
            assessment=assessment,
            monthly_spend=monthly_spend,
            spend_cap=spend_cap,
            strategy_profile=strategy_profile,
        )
        out.append(c)

    out.sort(key=lambda x: (x.priority_score or 0), reverse=True)
    return out
