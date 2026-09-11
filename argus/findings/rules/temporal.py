"""
Temporal-aware finding rules: map ``payload.temporal`` + ``observed_at`` to decision-worthy findings.

Does not replace the core engine — registers as standard :class:`FindingRule` instances.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, ClassVar

from argus.core.models.enums import FindingKind, SeverityLevel
from argus.core.models.signal import SignalRecord
from argus.findings.candidates import FindingCandidate
from argus.findings.context import RuleContext
from argus.findings.rules.base import FindingRule
from argus.findings.temporal_schema import (
    KEY_CONTEXT_STALE_SECONDS,
    KEY_CURRENT_RISK,
    KEY_DEADLINE,
    KEY_HOURS_REMAINING,
    KEY_MARKET_SPIKE,
    KEY_MOMENTUM,
    KEY_NEWS_ACCELERATION,
    KEY_NO_RECENT_EVIDENCE,
    KEY_PRIOR_WINDOW_LABEL,
    KEY_RISK_VELOCITY,
    KEY_SPIKE_RATIO,
    KEY_STALE_CONTEXT,
    KEY_TOPIC,
)

TEMPORAL_FINDING_KINDS: frozenset[FindingKind] = frozenset(
    {
        FindingKind.CURRENT_OPPORTUNITY,
        FindingKind.CURRENT_RISK,
        FindingKind.STALE_CONTEXT,
        FindingKind.TRENDING_TOPIC,
        FindingKind.URGENCY_WINDOW,
        FindingKind.NO_RECENT_EVIDENCE,
    }
)

_SECONDS_24H = 86400
_SECONDS_10D = 10 * 24 * 3600
_SECONDS_14D = 14 * 24 * 3600
_SPIKE_RATIO_MIN = 1.35
_MOMENTUM_MIN = 0.45
_URGENCY_MAX_HOURS = 72.0


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _age_seconds(observed_at: datetime) -> float:
    return max(0.0, (_utcnow() - _aware(observed_at)).total_seconds())


def _freshness_multiplier(age_seconds: float) -> float:
    """Slightly decay confidence when evidence is older than 24h."""
    if age_seconds <= _SECONDS_24H:
        return 1.0
    # Linear decay from 1.0 at 24h to ~0.55 at 14d
    span = max(1.0, _SECONDS_14D - _SECONDS_24H)
    excess = age_seconds - _SECONDS_24H
    return max(0.5, 1.0 - 0.45 * min(1.0, excess / span))


def _clip_conf(base: float, observed_at: datetime) -> float:
    m = _freshness_multiplier(_age_seconds(observed_at))
    return round(max(0.12, min(0.95, base * m)), 3)


def _temporal_block(payload: dict[str, Any]) -> dict[str, Any]:
    t = payload.get("temporal")
    return t if isinstance(t, dict) else {}


class TemporalSignalsRule(FindingRule):
    """
    Emit temporal findings from ``payload.temporal`` on any signal type.

    Conventions (see ``argus.findings.temporal_schema``):
    - **current_opportunity**: ``spike_ratio`` / ``market_spike`` / strong ``news_acceleration``
    - **current_risk**: ``risk_velocity`` negative, or ``current_risk`` flag
    - **stale_context**: ``context_stale_seconds`` or ``stale_context`` flag
    - **trending_topic**: ``topic`` + ``momentum``
    - **urgency_window**: ``deadline`` or ``hours_remaining`` within a short window
    - **no_recent_evidence**: explicit flag, or product-level staleness (see below)
    """

    rule_id: ClassVar[str] = "temporal_signals"

    def evaluate(self, ctx: RuleContext) -> list[FindingCandidate]:
        out: list[FindingCandidate] = []
        for s in ctx.signals:
            p = s.payload if isinstance(s.payload, dict) else {}
            t = _temporal_block(p)
            out.extend(self._from_signal(s, t, p))

        out.extend(self._product_level(ctx))
        return out

    def _from_signal(
        self,
        s: SignalRecord,
        t: dict[str, Any],
        payload: dict[str, Any],
    ) -> list[FindingCandidate]:
        out: list[FindingCandidate] = []
        obs_iso = _aware(s.observed_at).isoformat()

        if t.get(KEY_NO_RECENT_EVIDENCE) is True:
            out.append(
                self._cand(
                    FindingKind.NO_RECENT_EVIDENCE,
                    "no_recent_evidence_flag",
                    "No recent evidence for temporal assessment",
                    t.get("reason") or "Source marked inputs as stale or missing recent observations.",
                    (
                        "Refresh metrics snapshots, widen ingestion, or confirm pipeline schedule "
                        "so decisions rest on current data."
                    ),
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        "signal_id": s.id,
                        "rationale": "Adapter set temporal.no_recent_evidence.",
                        "raw_temporal": {k: t[k] for k in t if not str(k).startswith("_")},
                    },
                    SeverityLevel.MEDIUM,
                    0.62,
                    s.observed_at,
                )
            )

        sr = t.get(KEY_SPIKE_RATIO)
        try:
            spike_ratio = float(sr) if sr is not None else 0.0
        except (TypeError, ValueError):
            spike_ratio = 0.0
        if t.get(KEY_MARKET_SPIKE) is True or spike_ratio >= _SPIKE_RATIO_MIN:
            prior = str(t.get(KEY_PRIOR_WINDOW_LABEL) or "prior window")
            summary = (
                f"Metric spike vs {prior}: ratio {spike_ratio:.2f}"
                if spike_ratio > 0
                else "Market or demand spike flagged by upstream signals."
            )
            out.append(
                self._cand(
                    FindingKind.CURRENT_OPPORTUNITY,
                    "market_spike",
                    "Temporal window: elevated demand or interest",
                    summary,
                    "Prioritize capture experiments, capacity, and messaging while the window is open.",
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        "spike_ratio": spike_ratio,
                        "prior_window_label": prior,
                        "change_summary": summary,
                        "rationale": "Observed change vs prior window exceeds temporal spike threshold.",
                    },
                    SeverityLevel.LOW,
                    _clip_conf(0.78, s.observed_at),
                    s.observed_at,
                )
            )

        try:
            nv = float(t.get(KEY_NEWS_ACCELERATION, 0.0) or 0.0)
        except (TypeError, ValueError):
            nv = 0.0
        if nv >= 0.6 and not any(x.kind == FindingKind.CURRENT_OPPORTUNITY for x in out):
            out.append(
                self._cand(
                    FindingKind.CURRENT_OPPORTUNITY,
                    "news_acceleration",
                    "Topic or news acceleration detected",
                    f"Acceleration index {nv:.2f} vs baseline narrative window.",
                    "Validate relevance to your ICP; consider timed campaigns or partnerships.",
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        KEY_NEWS_ACCELERATION: nv,
                        "rationale": "Upstream trend acceleration metric crossed temporal threshold.",
                    },
                    SeverityLevel.LOW,
                    _clip_conf(0.7, s.observed_at),
                    s.observed_at,
                )
            )

        try:
            rv = float(t.get(KEY_RISK_VELOCITY, 0.0) or 0.0)
        except (TypeError, ValueError):
            rv = 0.0
        if t.get(KEY_CURRENT_RISK) is True or rv <= -0.2:
            out.append(
                self._cand(
                    FindingKind.CURRENT_RISK,
                    "current_risk_velocity",
                    "Temporal risk: deterioration vs recent baseline",
                    f"Risk velocity {rv:.2f}" if rv != 0.0 else "Upstream flagged acute risk in the current window.",
                    "Tighten monitoring, freeze risky changes, and rehearse mitigation.",
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        KEY_RISK_VELOCITY: rv,
                        "rationale": "Negative velocity or explicit risk flag in temporal block.",
                    },
                    SeverityLevel.HIGH if rv <= -0.35 else SeverityLevel.MEDIUM,
                    _clip_conf(0.74, s.observed_at),
                    s.observed_at,
                )
            )

        try:
            stale_sec = float(t.get(KEY_CONTEXT_STALE_SECONDS, 0.0) or 0.0)
        except (TypeError, ValueError):
            stale_sec = 0.0
        if t.get(KEY_STALE_CONTEXT) is True or stale_sec >= _SECONDS_10D:
            out.append(
                self._cand(
                    FindingKind.STALE_CONTEXT,
                    "stale_context",
                    "Stale context: decisions may rely on old market or product state",
                    f"Context age ~{stale_sec / 86400:.1f} d" if stale_sec else "Temporal layer marked context as stale.",
                    "Refresh competitive intel, pricing assumptions, or user research before committing.",
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        KEY_CONTEXT_STALE_SECONDS: stale_sec,
                        "rationale": "Stale age exceeds temporal threshold or explicit stale_context flag.",
                    },
                    SeverityLevel.MEDIUM,
                    _clip_conf(0.68, s.observed_at),
                    s.observed_at,
                )
            )

        topic = t.get(KEY_TOPIC)
        try:
            mom = float(t.get(KEY_MOMENTUM, 0.0) or 0.0)
        except (TypeError, ValueError):
            mom = 0.0
        if isinstance(topic, str) and topic.strip() and mom >= _MOMENTUM_MIN:
            out.append(
                self._cand(
                    FindingKind.TRENDING_TOPIC,
                    f"trending:{topic.strip()[:40]}",
                    f"Trending topic: {topic.strip()[:120]}",
                    f"Momentum score {mom:.2f} in the monitored narrative window.",
                    "Evaluate product fit and SEO/content bets while attention is elevated.",
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        KEY_TOPIC: topic.strip(),
                        KEY_MOMENTUM: mom,
                        "rationale": "Topic + momentum exceed temporal thresholds.",
                    },
                    SeverityLevel.LOW,
                    _clip_conf(0.65, s.observed_at),
                    s.observed_at,
                )
            )

        hr = t.get(KEY_HOURS_REMAINING)
        deadline_s = t.get(KEY_DEADLINE)
        hours: float | None = None
        if hr is not None:
            try:
                hours = float(hr)
            except (TypeError, ValueError):
                hours = None
        elif isinstance(deadline_s, str) and deadline_s.strip():
            try:
                ddl = datetime.fromisoformat(deadline_s.replace("Z", "+00:00"))
                hours = (_aware(ddl) - _utcnow()).total_seconds() / 3600.0
            except ValueError:
                hours = None
        if hours is not None and 0 < hours <= _URGENCY_MAX_HOURS:
            out.append(
                self._cand(
                    FindingKind.URGENCY_WINDOW,
                    "urgency_window",
                    "Urgency window: time-bounded opportunity or obligation",
                    f"≈{hours:.1f} hours remaining in monitored window.",
                    "Assign an owner, decide in/out, and avoid letting the window expire silently.",
                    [s.id],
                    {
                        "temporal_finding": True,
                        "observed_at": obs_iso,
                        KEY_HOURS_REMAINING: hours,
                        KEY_DEADLINE: deadline_s,
                        "rationale": "Deadline within configured urgency horizon.",
                    },
                    SeverityLevel.HIGH if hours <= 24 else SeverityLevel.MEDIUM,
                    _clip_conf(0.8, s.observed_at),
                    s.observed_at,
                )
            )

        return out

    def _product_level(self, ctx: RuleContext) -> list[FindingCandidate]:
        """If all signals are very old, emit a single no-recent-evidence finding."""
        if not ctx.signals:
            return [
                self._cand(
                    FindingKind.NO_RECENT_EVIDENCE,
                    "no_signal_records",
                    "No signal records — no recent evidence",
                    "Argus collected zero normalized signals for this product in the latest bundle.",
                    "Run signal collection or ingest snapshots so temporal rules and decisions have data.",
                    [],
                    {
                        "temporal_finding": True,
                        "rationale": "Empty signal list in rule context.",
                        "observed_at": _utcnow().isoformat(),
                    },
                    SeverityLevel.MEDIUM,
                    0.55,
                    _utcnow(),
                )
            ]
        newest = max(_aware(s.observed_at) for s in ctx.signals)
        age = _age_seconds(newest)
        if age > _SECONDS_14D:
            return [
                self._cand(
                    FindingKind.NO_RECENT_EVIDENCE,
                    "all_signals_stale",
                    "No recent evidence: newest signal is older than 14 days",
                    f"Newest observation is ~{age / 86400:.1f} days old.",
                    "Refresh ingestion or confirm collection cadence before trusting portfolio posture.",
                    [s.id for s in ctx.signals[:24]],
                    {
                        "temporal_finding": True,
                        "newest_observed_at": newest.isoformat(),
                        "age_seconds": age,
                        "rationale": "Product-level staleness across all bundled signals.",
                    },
                    SeverityLevel.MEDIUM,
                    _clip_conf(0.6, newest),
                    newest,
                )
            ]
        return []

    def _cand(
        self,
        kind: FindingKind,
        issue_key: str,
        title: str,
        summary: str,
        recommendation: str,
        source_ids: list[str],
        evidence: dict[str, Any],
        severity: SeverityLevel,
        confidence: float,
        created_anchor: datetime,
    ) -> FindingCandidate:
        evidence = {
            **evidence,
            "anchor_timestamp": _aware(created_anchor).isoformat(),
        }
        return FindingCandidate(
            rule_id=self.rule_id,
            issue_key=issue_key,
            kind=kind,
            title=title,
            summary=summary,
            recommendation=recommendation,
            source_signal_ids=source_ids,
            evidence=evidence,
            severity_hint=severity,
            confidence=confidence,
        )
