"""
Plain-language explanations for orchestration freshness (read-only translation).

Uses ``eligibility_facts`` from :func:`~argus.orchestrator.eligibility.evaluate_product_orchestration`
and optional ``orchestration_status``. Does **not** change eligibility or ``stale_refresh_needed`` semantics.
"""

from __future__ import annotations

from typing import Any

from argus.orchestrator.state_models import ORCH_STATUS_STALE_REFRESH_NEEDED


def _bool(facts: dict[str, Any], key: str) -> bool | None:
    v = facts.get(key)
    if v is True:
        return True
    if v is False:
        return False
    return None


def freshness_explanation_lines(
    eligibility_facts: dict[str, Any] | None,
    *,
    orchestration_status: str | None = None,
) -> list[str]:
    """
    Human-readable lines for ELI5 / dashboard / portfolio summaries.

    When ``orchestration_status`` is ``stale_refresh_needed``, lines clarify *which*
    dimensions are still open (signals clock vs temporal aggregate vs audit age vs audit coverage).
    """
    if not isinstance(eligibility_facts, dict):
        return []

    f = eligibility_facts
    lines: list[str] = []

    sig_stale = _bool(f, "signals_collection_time_stale")
    if sig_stale is True:
        lines.append(
            "Signals: collection timestamp is past the freshness window — run signals collect again."
        )
    elif sig_stale is False:
        lines.append("Signals: freshly collected (collection timestamp within SLA).")
    else:
        lines.append("Signals: collection freshness not present in saved state.")

    temp_stale = _bool(f, "temporal_freshness_stale")
    worst = f.get("temporal_worst_freshness_status")
    worst_s = str(worst).strip() if worst is not None else ""
    if temp_stale is True:
        extra = f" (worst bucket: {worst_s})" if worst_s else ""
        lines.append(
            "Temporal: time-based health snapshot needs refresh — aggregate freshness is stale or expired"
            + extra
            + "."
        )
    elif temp_stale is False:
        lines.append(
            "Temporal: aggregate freshness looks OK (worst bucket is not stale/expired for current rules)."
        )
    else:
        lines.append("Temporal: aggregate freshness not available from saved state.")

    comb = _bool(f, "signals_refresh_needed")
    if comb is True and sig_stale is False and temp_stale is True:
        lines.append(
            "Note: the combined “signals refresh” expectation is driven by temporal lag — "
            "the signals bundle clock is still within SLA."
        )

    aud_time = _bool(f, "audit_bundle_time_stale")
    if aud_time is True:
        lines.append("Audit: bundle on disk is older than the audit freshness window — run audit.")
    elif aud_time is False:
        lines.append("Audit: bundle recency is within SLA (audit artifact not time-stale).")
    else:
        lines.append("Audit: bundle age not available from saved state.")

    gap_inc = _bool(f, "audit_product_gap_incomplete")
    sec_stub = _bool(f, "audit_security_stub")
    gap_partial = _bool(f, "audit_product_gap_partial")
    cov_bits: list[str] = []
    if gap_inc is True:
        cov_bits.append("product gap incomplete")
    if sec_stub is True:
        cov_bits.append("security angle stub")
    if gap_partial is True:
        cov_bits.append("product gap partial")
    if cov_bits:
        lines.append(
            "Audit alignment: coverage still open — " + "; ".join(cov_bits) + "."
        )
    elif gap_inc is False and sec_stub is False:
        lines.append(
            "Audit alignment: no stub/incomplete flags on file for product gap and security angles."
        )

    ost = str(orchestration_status or "").strip()
    if ost == ORCH_STATUS_STALE_REFRESH_NEEDED:
        pending = _pending_short_labels(f)
        if pending:
            lines.append("Other checks pending: " + ", ".join(pending) + ".")

    return lines


def _pending_short_labels(f: dict[str, Any]) -> list[str]:
    """Compact labels for the summary line (non-empty subsets of what still fails)."""
    out: list[str] = []
    if _bool(f, "signals_collection_time_stale") is True:
        out.append("signals collection")
    if _bool(f, "temporal_freshness_stale") is True:
        out.append("temporal refresh")
    if _bool(f, "audit_bundle_time_stale") is True:
        out.append("audit recency")
    if _bool(f, "audit_product_gap_incomplete") is True or _bool(f, "audit_security_stub") is True:
        out.append("audit alignment")
    # De-dupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def freshness_summary_sentence(
    eligibility_facts: dict[str, Any] | None,
    *,
    orchestration_status: str | None = None,
) -> str | None:
    """Single-line addendum for portfolio ``evidence_summary`` or tooltips."""
    lines = freshness_explanation_lines(
        eligibility_facts,
        orchestration_status=orchestration_status,
    )
    if not lines:
        return None
    ost = str(orchestration_status or "").strip()
    if ost != ORCH_STATUS_STALE_REFRESH_NEEDED:
        return None
    pending = _pending_short_labels(eligibility_facts) if isinstance(eligibility_facts, dict) else []
    if not pending:
        return None
    # Short headline matching operator FAQ
    sig_ok = _bool(eligibility_facts, "signals_collection_time_stale") is False if isinstance(eligibility_facts, dict) else False
    rest = [p for p in pending if p != "signals collection"]
    if sig_ok and rest:
        return (
            "Freshness: signals look fresh on the clock; other checks pending: "
            + ", ".join(rest)
            + "."
        )
    return "Freshness: still pending — " + ", ".join(pending) + "."
