"""Turn context bundles into prompt-sized text blocks."""

from __future__ import annotations

from typing import Any


def bundle_to_refinement_prompt_addon(bundle: dict[str, Any]) -> str:
    """Compact block for LLM prompts (deterministic structure)."""
    ps = bundle.get("product") or {}
    sys = ps.get("system") or {}
    if not sys.get("present"):
        return f"(context packet: product unavailable — {sys.get('reason', 'unknown')})\n"

    lines: list[str] = [
        "=== CONTEXT PACKET (argus.context) ===",
        f"schema={bundle.get('packet_schema')} purpose={bundle.get('purpose')}",
        "",
        "--- product.system ---",
        sys.get("product_summary") or "",
        "",
        f"Doctrine (excerpt):\n{sys.get('doctrine_excerpt') or '(none)'}",
        "",
        f"Strategy:\n{sys.get('strategy_summary') or ''}",
        "",
    ]
    fr = sys.get("findings_recent") or []
    if fr:
        lines.append("Recent findings (capped):")
        for row in fr:
            lines.append(
                f"  - [{row.get('severity')}] {row.get('title', '')}: {(row.get('summary') or '')[:200]}"
            )
        lines.append("")
    gaps = sys.get("capability_gap_ids") or []
    if gaps:
        lines.append(f"Capability gap ids (capped): {', '.join(gaps)}")
        lines.append("")
    tc = sys.get("temporal_compact") or {}
    if tc:
        lines.append(f"Temporal: overall={tc.get('overall')} requirement={tc.get('temporal_requirement')}")
        lines.append("")

    om = bundle.get("omitted_counts") or {}
    if om:
        parts = [f"{k}={v}" for k, v in om.items()]
        lines.append(f"(Omitted due to caps: {', '.join(parts)})")
        lines.append("")

    aud = bundle.get("audit") or {}
    sm = aud.get("summary") or {}
    if sm:
        lines.extend(
            [
                "--- audit.summary (Product Gap) ---",
                f"generated_at={sm.get('audit_generated_at_utc')} depth={sm.get('scan_depth')} fp={sm.get('inputs_fingerprint', '')[:16]}",
                f"counts_by_status: {sm.get('counts_by_status')}",
                f"already_exists (capped): {', '.join(sm.get('already_exists') or [])}",
                f"known_missing: {', '.join(sm.get('known_missing') or [])}",
                f"partial: {', '.join(sm.get('partial_capabilities') or [])}",
                f"unknown: {', '.join(sm.get('unknown_capabilities') or [])}",
                "",
            ]
        )
        cov = sm.get("coverage") or {}
        if cov:
            lines.append(
                f"audit coverage: depth={cov.get('depth')} scanned_n={len(cov.get('scanned_paths') or [])}"
            )
            lines.append("")
    acov = aud.get("audit_coverage") or {}
    if acov:
        lines.append("--- audit_coverage (angle depth) ---")
        lines.append(f"implemented_angles: {', '.join(acov.get('implemented_angles') or [])}")
        lines.append(f"partial_angles: {', '.join(acov.get('partial_angles') or [])}")
        lines.append(f"stub_angles: {', '.join(acov.get('stub_angles') or [])}")
        if aud.get("inputs_fingerprint_bundle"):
            lines.append(f"bundle_fp={str(aud.get('inputs_fingerprint_bundle'))[:20]}")
        lines.append("")
    ang = aud.get("angles") or {}
    if ang:
        lines.append("--- audit.angles (summary_lines only) ---")
        for aid in sorted(ang.keys()):
            for line in ang.get(aid) or []:
                lines.append(f"  [{aid}] {line}")
        lines.append("")

    art = bundle.get("artifact") or {}
    d = art.get("draft")
    if d:
        lines.extend(
            [
                "--- artifact.draft (preview; full draft may be appended separately) ---",
                f"round={d.get('round_number')} type={d.get('artifact_type')} title={d.get('title')}",
                f"content_preview:\n{d.get('content_preview') or ''}",
                "",
            ]
        )

    src = bundle.get("context_sources") or []
    if src:
        lines.append("Sources: " + "; ".join(src[:24]))
    lines.append("=== END CONTEXT PACKET ===")
    return "\n".join(lines)
