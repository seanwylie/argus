"""
Deterministic drift and trend rules (explicit thresholds, no ML).

All helpers are pure given a time-ordered series of :class:`ProductSnapshot`.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from argus.history.models import ProductSnapshot
from argus.trends.models import TrendFlag

# --- Keyword lists (audit trail) ---
_DEPRECATE_HOLD_PAT = re.compile(
    r"\b(deprecat|sunset|hold|pause|freeze|kill|wind\s*down)\b",
    re.I,
)
_GROWTH_HINT_PAT = re.compile(
    r"\b(grow|scale|launch|traffic|retention|revenue|conversion)\b",
    re.I,
)


def parse_observed_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    t = s.strip()
    if t.endswith("Z"):
        t = t[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def series_metrics(series: list[ProductSnapshot]) -> dict[str, Any]:
    """Compact numeric summaries for JSON output and tests."""
    n = len(series)
    if n == 0:
        return {"n": 0}

    findings = [p.active_findings_count for p in series]
    costs = [p.monthly_cost_usd for p in series]
    prios = [p.priority_score for p in series]
    confs = [p.top_confidence for p in series]
    esc = [p.escalation_count for p in series]
    stages = [p.lifecycle_stage for p in series]
    actions = [p.top_recommended_action.strip() for p in series]
    kills = [p.kill_candidate for p in series]

    def delta_first_last(vals: list[float | None]) -> float | None:
        a, b = vals[0], vals[-1]
        if a is None or b is None:
            return None
        return float(b - a)

    # Monotonic steps for findings
    inc_steps = sum(
        1 for i in range(1, n) if findings[i] > findings[i - 1]
    )
    dec_steps = sum(
        1 for i in range(1, n) if findings[i] < findings[i - 1]
    )

    action_changes = sum(
        1
        for i in range(1, n)
        if actions[i] != actions[i - 1] and (actions[i] or actions[i - 1])
    )
    distinct_actions = len({a for a in actions if a})

    stage_changes = sum(1 for i in range(1, n) if stages[i] != stages[i - 1])

    # Signal age: days between last_signal_at and snapshot observed_at (best effort)
    signal_lag_days: list[float | None] = []
    for p in series:
        obs = parse_observed_ts(p.observed_at_utc)
        sig = parse_observed_ts(p.last_signal_at)
        if obs is not None and sig is not None:
            signal_lag_days.append((obs - sig).total_seconds() / 86400.0)
        else:
            signal_lag_days.append(None)

    return {
        "n": n,
        "findings_first": findings[0],
        "findings_last": findings[-1],
        "findings_net_delta": findings[-1] - findings[0],
        "findings_increase_steps": inc_steps,
        "findings_decrease_steps": dec_steps,
        "priority_delta": delta_first_last(
            [float(x) if x is not None else None for x in prios],
        ),
        "confidence_delta": delta_first_last(
            [float(x) if x is not None else None for x in confs],
        ),
        "cost_delta": delta_first_last(
            [float(x) if x is not None else None for x in costs],
        ),
        "cost_first": costs[0],
        "cost_last": costs[-1],
        "escalation_max": max(esc) if esc else 0,
        "escalation_sum": sum(esc),
        "escalation_nonzero_snapshots": sum(1 for x in esc if x > 0),
        "action_changes": action_changes,
        "distinct_top_actions": distinct_actions,
        "lifecycle_stage_changes": stage_changes,
        "distinct_stages": len({s for s in stages if s}),
        "kill_candidate_last": kills[-1] if kills else False,
        "signal_lag_days_max": max(
            (x for x in signal_lag_days if x is not None),
            default=None,
        ),
        "signal_freshness_trend": _signal_freshness_trend(series),
    }


def _signal_freshness_trend(series: list[ProductSnapshot]) -> str:
    """``stale`` if ``last_signal_at`` never changes while snapshots advance."""
    if len(series) < 2:
        return "unknown"
    first = series[0].last_signal_at
    if first is None:
        return "unknown"
    if all(p.last_signal_at == first for p in series[1:]):
        return "stale"
    return "updating"


def drift_signals(series: list[ProductSnapshot], m: dict[str, Any]) -> list[str]:
    """Human-readable drift reasons."""
    signals: list[str] = []
    n = m.get("n", 0)
    if n < 2:
        return signals

    # Rising findings
    if m.get("findings_increase_steps", 0) >= 2:
        signals.append("findings rose in multiple consecutive snapshots")
    elif m.get("findings_net_delta", 0) >= 3:
        signals.append("findings rose materially over the window")

    if m.get("signal_freshness_trend") == "stale" and n >= 3:
        signals.append("last_signal_at did not advance across snapshots (stale signals)")

    # Deprecate / hold repetition
    dep_hits = sum(
        1 for p in series if _DEPRECATE_HOLD_PAT.search(p.top_recommended_action or "")
    )
    if dep_hits >= 2:
        signals.append("top recommendation repeatedly suggests deprecate/hold/pause themes")

    # Cost up without growth language in top action
    cd = m.get("cost_delta")
    cost_first = m.get("cost_first")
    if (
        cd is not None
        and cd > 0
        and cost_first is not None
        and cost_first > 0
        and (cd / float(cost_first)) > 0.10
    ):
        growth_lang = sum(
            1 for p in series if _GROWTH_HINT_PAT.search(p.top_recommended_action or "")
        )
        if growth_lang == 0:
            signals.append("cost increased >10% without growth-oriented language in top action")

    # Action thrashing
    if m.get("action_changes", 0) >= 3 or m.get("distinct_top_actions", 0) >= 4:
        signals.append("top recommended action changed frequently (thrashing)")

    # Escalation recurrence
    if m.get("escalation_nonzero_snapshots", 0) >= 2:
        signals.append("escalations present in multiple snapshots")
    if n >= 2 and m.get("escalation_sum", 0) >= 2:
        signals.append("escalation volume accumulated over the window")

    # Stagnation: no findings change, no stage change, signal unchanged
    if n >= 3:
        findings = [p.active_findings_count for p in series]
        stages = [p.lifecycle_stage for p in series]
        sigs = [p.last_signal_at for p in series]
        stagnant = 0
        for i in range(1, n):
            if (
                findings[i] == findings[i - 1]
                and stages[i] == stages[i - 1]
                and sigs[i] == sigs[i - 1]
            ):
                stagnant += 1
        if stagnant >= n - 2:
            signals.append("no meaningful movement across findings, stage, and last signal")

    # Validate stuck
    if n >= 4:
        if all(p.lifecycle_stage.lower() == "validate" for p in series):
            signals.append("lifecycle remained validate across the entire window")

    return signals


def assign_trend_flags(
    series: list[ProductSnapshot],
    m: dict[str, Any],
    drifts: list[str],
) -> list[str]:
    """Return ordered flags (strings, deduplicated)."""
    n = m.get("n", 0)
    if n < 2:
        return [TrendFlag.INSUFFICIENT_DATA.value]

    findings_net = int(m.get("findings_net_delta", 0))
    findings_up = findings_net > 0
    findings_down = findings_net < 0
    conf_delta = m.get("confidence_delta")
    kill_last = bool(m.get("kill_candidate_last"))
    esc_multi = int(m.get("escalation_nonzero_snapshots", 0)) >= 2
    thrash = m.get("action_changes", 0) >= 3 or m.get("distinct_top_actions", 0) >= 4
    stagnating = any("no meaningful movement" in d for d in drifts)
    validate_stuck = any("lifecycle remained validate" in d for d in drifts)
    dep_repeated = sum(
        1 for p in series if _DEPRECATE_HOLD_PAT.search(p.top_recommended_action or "")
    ) >= 2

    risk_up = findings_up or kill_last or esc_multi

    flags: list[str] = []

    if thrash:
        flags.append(TrendFlag.ACTION_THRASHING.value)
    if stagnating:
        flags.append(TrendFlag.STAGNATING.value)
    if risk_up and not (findings_down and not kill_last):
        flags.append(TrendFlag.RISK_INCREASING.value)
    if (
        findings_down
        and (conf_delta is None or float(conf_delta) >= -0.05)
        and not kill_last
    ):
        flags.append(TrendFlag.IMPROVING.value)
    if kill_last and dep_repeated and (stagnating or findings_up):
        flags.append(TrendFlag.LIKELY_ABANDON.value)
    if (
        validate_stuck
        and not thrash
        and not findings_up
        and int(m.get("findings_last", 99)) <= 5
    ):
        flags.append(TrendFlag.READY_FOR_SCALE_REVIEW.value)
    if (
        m.get("findings_increase_steps", 0) == 0
        and m.get("findings_decrease_steps", 0) == 0
        and m.get("lifecycle_stage_changes", 0) == 0
        and not thrash
        and not stagnating
    ):
        flags.append(TrendFlag.STABLE.value)

    # Ambiguous window: not clearly improving or worsening
    if (
        TrendFlag.IMPROVING.value not in flags
        and TrendFlag.RISK_INCREASING.value not in flags
        and not thrash
        and not stagnating
        and TrendFlag.STABLE.value not in flags
        and TrendFlag.READY_FOR_SCALE_REVIEW.value not in flags
        and TrendFlag.LIKELY_ABANDON.value not in flags
    ):
        flags.append(TrendFlag.DRIFTING.value)

    seen: set[str] = set()
    out: list[str] = []
    for f in flags:
        if f not in seen:
            seen.add(f)
            out.append(f)
    return out if out else [TrendFlag.DRIFTING.value]


def confidence_score(
    window_size: int,
    drift_count: int,
    flag_count: int,
) -> float:
    """Deterministic 0..1: more data, clearer drift → higher confidence."""
    if window_size < 2:
        return 0.0
    base = min(1.0, 0.35 + 0.1 * (window_size - 2))
    bump = min(0.35, 0.07 * drift_count)
    penalty = min(0.2, 0.04 * max(0, flag_count - 3))
    return round(max(0.0, min(1.0, base + bump - penalty)), 3)


def build_summary_text(
    product_id: str,
    flags: list[str],
    drifts: list[str],
    m: dict[str, Any],
) -> str:
    parts = [f"{product_id}: window={m.get('n', 0)} snapshots"]
    parts.append("flags=" + ",".join(flags))
    if drifts:
        parts.append("drift: " + "; ".join(drifts[:4]))
    return ". ".join(parts) + "."


def build_interpretation(
    flags: list[str],
    drifts: list[str],
) -> str:
    if TrendFlag.INSUFFICIENT_DATA.value in flags:
        return "Capture more history snapshots (argus history snapshot) before relying on trends."
    lines: list[str] = []
    if TrendFlag.IMPROVING.value in flags:
        lines.append("Signals suggest improving health: fewer findings or stable confidence.")
    if TrendFlag.RISK_INCREASING.value in flags:
        lines.append("Risk appears to be rising; review findings, escalations, and kill_candidate.")
    if TrendFlag.STAGNATING.value in flags:
        lines.append("Little movement across signals; confirm whether work is intentional or blocked.")
    if TrendFlag.ACTION_THRASHING.value in flags:
        lines.append("Decision recommendations keep changing; stabilize inputs before acting.")
    if TrendFlag.LIKELY_ABANDON.value in flags:
        lines.append("Signals align with deprecation or abandonment; escalate for human decision.")
    if TrendFlag.READY_FOR_SCALE_REVIEW.value in flags:
        lines.append("Validate-stage product with sustained stability; consider a scale review gate.")
    if TrendFlag.STABLE.value in flags and len(flags) == 1:
        lines.append("Metrics are stable across the window.")
    if not lines:
        lines.append("Mixed or unclear signals; review drift details and snapshot cadence.")
    return " ".join(lines)
