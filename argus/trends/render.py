"""Human-readable rendering for trend runs."""

from __future__ import annotations

from argus.core.serialize import dumps_json, to_jsonable
from argus.trends.models import TrendsRunPayload, TrendSummary


def format_summary_line(s: TrendSummary) -> str:
    """One line for ``argus trends summary``."""
    flags = ",".join(s.trend_flags) if s.trend_flags else "-"
    drift_n = len(s.drift_signals)
    return (
        f"{s.product_id}: flags=[{flags}] confidence={s.confidence:.2f} "
        f"window={s.window_size} drifts={drift_n}"
    )


def format_trend_summary_block(s: TrendSummary) -> str:
    """Multi-line block for analyze output."""
    lines = [
        f"## {s.product_id}",
        f"window_size: {s.window_size}",
        f"trend_flags: {', '.join(s.trend_flags)}",
        f"confidence: {s.confidence}",
        f"summary: {s.summary}",
        f"interpretation: {s.recommended_interpretation}",
    ]
    if s.drift_signals:
        lines.append("drift_signals:")
        for d in s.drift_signals:
            lines.append(f"  - {d}")
    return "\n".join(lines) + "\n"


def format_drift_report(summaries: list[TrendSummary]) -> str:
    """Products with at least one drift signal."""
    rows = [s for s in summaries if s.drift_signals]
    if not rows:
        return "No drift patterns detected for products with history (or no history).\n"
    lines = ["# Drift report (products with drift signals)", ""]
    for s in rows:
        lines.append(format_trend_summary_block(s))
    return "\n".join(lines)


def format_full_text(payload: TrendsRunPayload) -> str:
    """Entire run as markdown-ish text."""
    lines = [
        f"# Argus trends ({payload.command})",
        f"generated_at_utc: {payload.generated_at_utc}",
        f"repo: {payload.repo_root}",
        "",
    ]
    for s in payload.summaries:
        lines.append(format_trend_summary_block(s))
    return "\n".join(lines)


def payload_to_json(payload: TrendsRunPayload) -> str:
    return dumps_json(to_jsonable(payload))
