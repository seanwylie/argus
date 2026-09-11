"""Render escalation packets as JSON text, Markdown, or plain text."""

from __future__ import annotations

from argus.core.serialize import dumps_json
from argus.escalation.models import EscalationPacket
from argus.escalation.packet import packet_to_json_dict


def render_json(packet: EscalationPacket, *, indent: int | None = 2) -> str:
    """Pretty-printed JSON including schema."""
    return dumps_json(packet_to_json_dict(packet), indent=indent)


def _fmt_score(x: float | None, *, na: str = "not available (legacy packet or insufficient context)") -> str:
    if x is None:
        return f"*{na}*"
    return f"{x:.2f} (0 = low, 1 = high)"


def _has_confidence_context(packet: EscalationPacket) -> bool:
    return any(
        (
            packet.confidence_score is not None,
            packet.uncertainty_score is not None,
            packet.risk_score is not None,
            packet.recurrence_risk_score is not None,
            packet.escalation_pressure is not None,
            bool(packet.top_uncertainty_factors),
            bool(packet.top_blocking_factors),
            packet.exploratory_action_considered,
            packet.exploratory_action_rejected_reason,
        )
    )


def render_markdown(packet: EscalationPacket) -> str:
    """Human-readable Markdown summary."""
    lines = [
        f"# {packet.title}",
        "",
        f"- **Packet ID:** `{packet.packet_id}`",
        f"- **Created (UTC):** {packet.created_at.isoformat()}",
        f"- **Product:** `{packet.product_id}`",
        f"- **Risk level:** **{packet.risk_level.value}**",
        f"- **Source:** `{packet.source_type.value}` / `{packet.source_id}`",
        f"- **Stopped at:** `{packet.stopped_stage}`",
        "",
        "## Summary",
        "",
        packet.summary,
        "",
    ]

    if _has_confidence_context(packet):
        lines.extend(
            [
                "## What this escalation means for operators",
                "",
                "Argus is **not** claiming live production truth — these scores summarize **local artifacts** "
                "(decisions, lifecycle, prior escalation files on disk) to help triage.",
                "",
                "| Dimension | Value |",
                "|-----------|--------|",
                f"| Confidence in top-ranked decision | {_fmt_score(packet.confidence_score)} |",
                f"| Uncertainty (evidence gaps / doubt) | {_fmt_score(packet.uncertainty_score)} |",
                f"| Posture / kill & trigger risk (blended) | {_fmt_score(packet.risk_score)} |",
                f"| Recurrence risk (recent prior escalations) | {_fmt_score(packet.recurrence_risk_score)} |",
                f"| Escalation pressure (blend) | {_fmt_score(packet.escalation_pressure)} |",
                "",
            ]
        )
        if packet.top_uncertainty_factors:
            lines.extend(["### What made Argus uncertain", ""])
            for u in packet.top_uncertainty_factors:
                lines.append(f"- {u}")
            lines.append("")
        else:
            lines.extend(
                [
                    "### What made Argus uncertain",
                    "",
                    "*No separate uncertainty bullets — see triggering rules below.*",
                    "",
                ]
            )
        if packet.top_blocking_factors:
            lines.extend(["### What blocked automatic execution", ""])
            for b in packet.top_blocking_factors:
                lines.append(f"- {b}")
            lines.append("")
        else:
            lines.extend(
                [
                    "### What blocked automatic execution",
                    "",
                    "*See triggering rules — policy/safety gates fired.*",
                    "",
                ]
            )
        lines.extend(["### Leap-of-faith / experiment path", ""])
        if packet.exploratory_action_considered:
            lines.append(f"- **Visible in ranked decisions:** {packet.exploratory_action_considered}")
        else:
            lines.append("- **Visible in ranked decisions:** *No explicit growth/experiment intent in top candidates.*")
        if packet.exploratory_action_rejected_reason:
            lines.append(f"- **Why not proceeding automatically:** {packet.exploratory_action_rejected_reason}")
        else:
            lines.append(
                "- **Why not proceeding automatically:** *See triggering rules; "
                "may not apply if no exploratory path was ranked.*"
            )
        lines.append("")

    lines.extend(
        [
            "## Why Argus stopped",
            "",
            packet.why_stopped,
            "",
            "## Triggering rules",
            "",
        ]
    )
    for r in packet.triggering_rules:
        lines.append(f"- `{r}`")
    lines.extend(
        [
            "",
            "## Context files (open these for full state)",
            "",
        ]
    )
    for c in packet.context_files:
        lines.append(f"- `{c}`")
    lines.extend(["", "## Options", ""])
    for i, opt in enumerate(packet.options, 1):
        req = "yes" if opt.requires_human_confirmation else "no"
        lines.append(f"{i}. **{opt.action}** (risk: {opt.risk}, confirmation: {req})")
        lines.append(f"   - {opt.description}")
        lines.append(f"   - Suggested: `{opt.command}`")
        lines.append("")
    lines.extend(["## Notes", "", packet.notes, ""])
    return "\n".join(lines)


def render_text(packet: EscalationPacket) -> str:
    """Compact plain-text summary (no Markdown headers)."""
    lines = [
        packet.title,
        "=" * len(packet.title),
        f"packet_id: {packet.packet_id}",
        f"created_at: {packet.created_at.isoformat()}",
        f"product_id: {packet.product_id}",
        f"risk_level: {packet.risk_level.value}",
        f"stopped_stage: {packet.stopped_stage}",
        "",
        "Summary:",
        packet.summary,
        "",
    ]
    if _has_confidence_context(packet):
        lines.extend(
            [
                "Decision confidence context:",
                f"  confidence_score: {packet.confidence_score}",
                f"  uncertainty_score: {packet.uncertainty_score}",
                f"  risk_score: {packet.risk_score}",
                f"  recurrence_risk_score: {packet.recurrence_risk_score}",
                f"  escalation_pressure: {packet.escalation_pressure}",
            ]
        )
        if packet.top_uncertainty_factors:
            lines.append("  uncertainty_factors:")
            for u in packet.top_uncertainty_factors:
                lines.append(f"    - {u}")
        if packet.top_blocking_factors:
            lines.append("  blocking_factors:")
            for b in packet.top_blocking_factors:
                lines.append(f"    - {b}")
        if packet.exploratory_action_considered:
            lines.append(f"  exploratory_considered: {packet.exploratory_action_considered}")
        if packet.exploratory_action_rejected_reason:
            lines.append(f"  exploratory_rejected_reason: {packet.exploratory_action_rejected_reason}")
        lines.append("")
    lines.extend(
        [
            "Why stopped:",
            packet.why_stopped,
            "",
            "Options:",
        ]
    )
    for opt in packet.options:
        lines.append(
            f"  - {opt.action}: {opt.description} "
            f"[risk={opt.risk}, confirm={opt.requires_human_confirmation}]"
        )
        lines.append(f"    command: {opt.command}")
    lines.extend(["", "Notes:", packet.notes])
    return "\n".join(lines)
