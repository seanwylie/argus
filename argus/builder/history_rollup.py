"""
Compact, deterministic Builder history rows from :func:`argus.builder.status.compute_builder_status`.

Artifact-grounded only: no causal claims, strategy attribution, or portfolio memory.
Used by portfolio ``builder_activity`` JSON and multi-product CLI summaries.
"""

from __future__ import annotations

from typing import Any

from argus.builder.trust_operator_synthesis import trust_posture_label

BUILDER_HISTORY_ROW_SCHEMA = "argus.builder_history_row.v1"


def derive_cleanup_hint(merge_readiness: object, trust_posture: object) -> str | None:
    """
    Short operator-facing hint from merge readiness + trust posture (display only).

    Does not change merge rules; same inputs as shown in status / operator summary.
    """
    mr = str(merge_readiness or "").strip().lower()
    tp = str(trust_posture or "").strip().lower()
    if not mr and not tp:
        return None
    if mr == "unsafe" or tp == "unsafe_scope_breach":
        return "Scope or merge posture is unsafe — address scope before considering merge."
    if mr == "blocked" or tp == "blocked":
        return "Merge is blocked until failed invoke/reconcile or blocked review is resolved."
    if tp in ("degraded_unsandboxed", "degraded_plain_argus_root"):
        return "Containment or workspace scope looks off — read invoke/reconcile before merging."
    if tp == "unknown_incomplete":
        return "Finish declaration, prepare, invoke, and reconcile so the loop is complete."
    if mr == "merge_candidate" and tp == "ready_to_review":
        return "Merge is an option once you accept the diff and your policy bar."
    if mr == "review_required" or tp == "review_carefully":
        return "Needs a human pass on diff and trust signals before merge."
    if mr == "merge_candidate":
        return "Review labels allow merge — still confirm trust and policy match your bar."
    return "Use `argus builder status` JSON for full merge and trust fields."


def format_recent_run_compact_line(row: dict[str, Any]) -> str:
    """
    Single-line scan: ``product_id | contract | trust | next``.

    Prefers ``recommended_next_label`` when present, else ``recommended_next_action``.
    Trust column prefers ``trust_posture_label`` when present, else raw ``trust_posture`` code.
    """
    pid = str(row.get("product_id") or "").strip() or "?"
    contract = row.get("execution_contract_kind") or row.get("declared_target_type")
    c_s = str(contract).strip() if contract not in (None, "") else "—"
    tl = row.get("trust_posture_label")
    trust = (
        str(tl).strip()
        if tl not in (None, "")
        else (str(row.get("trust_posture") or "").strip() or "—")
    )
    nxt = row.get("recommended_next_label") or row.get("recommended_next_action")
    n_s = str(nxt).strip() if nxt not in (None, "") else "—"
    if len(n_s) > 48:
        n_s = n_s[:45] + "..."
    return f"{pid} | {c_s} | {trust} | {n_s}"


def _max_iso(a: object, b: object) -> str | None:
    sa = str(a).strip() if a is not None else ""
    sb = str(b).strip() if b is not None else ""
    if not sa:
        return sb or None
    if not sb:
        return sa
    return sa if sa >= sb else sb


def _trust_flag_highlights(inv: dict[str, Any]) -> list[str] | None:
    flags: list[str] = []
    if inv.get("trust_degraded_workspace_scope"):
        flags.append("trust_degraded_workspace_scope")
    if inv.get("trust_degraded_unsandboxed"):
        flags.append("trust_degraded_unsandboxed")
    if inv.get("containment_fallback_used"):
        flags.append("containment_fallback_used")
    if inv.get("trust_degraded_missing_landlock"):
        flags.append("trust_degraded_missing_landlock")
    if inv.get("trust_degraded_network_open"):
        flags.append("trust_degraded_network_open")
    if inv.get("trust_degraded_missing_no_new_privs"):
        flags.append("trust_degraded_missing_no_new_privs")
    bis = inv.get("branch_isolation_status")
    if bis and str(bis).strip() and str(bis) not in ("ok", "clean"):
        flags.append(f"branch_isolation_status={bis}")
    return flags or None


def builder_history_row_from_status(payload: dict[str, Any]) -> dict[str, Any]:
    """
    One portfolio / summary row: fields copied or lightly grouped from a status payload.

    Missing or unreadable artifacts are represented as ``null`` or omitted subfields where noted.
    """
    inv = payload.get("latest_invoke") if isinstance(payload.get("latest_invoke"), dict) else {}
    rec = payload.get("latest_reconcile") if isinstance(payload.get("latest_reconcile"), dict) else {}
    osum = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    le = payload.get("last_escalation") if isinstance(payload.get("last_escalation"), dict) else {}
    decl = (
        payload.get("declared_next_target")
        if isinstance(payload.get("declared_next_target"), dict)
        else {}
    )
    prep = payload.get("prepared_task") if isinstance(payload.get("prepared_task"), dict) else {}
    tv = (
        osum.get("trust_operator_view")
        if isinstance(osum.get("trust_operator_view"), dict)
        else {}
    )

    inv_ts = inv.get("invoked_at_utc")
    rec_ts = rec.get("reconciled_at_utc")
    updated_at = _max_iso(inv_ts, rec_ts)

    breach_reasons = rec.get("scope_breach_reasons")
    reasons_preview: list[str] | None = None
    if isinstance(breach_reasons, list) and breach_reasons:
        reasons_preview = [str(x)[:240] for x in breach_reasons[:3]]

    esc_emit: dict[str, Any] | None = None
    if rec.get("present"):
        esc_emit = {
            "emitted": rec.get("escalation_emit_emitted"),
            "packet_id": rec.get("escalation_emit_packet_id"),
            "path_repo": rec.get("escalation_emit_path_repo"),
            "reason": rec.get("escalation_emit_reason"),
        }
        tr = rec.get("escalation_triggering_rules")
        if isinstance(tr, list) and tr:
            esc_emit["triggering_rules_preview"] = [str(x) for x in tr[:5]]

    inv_present = bool(inv.get("present"))
    rec_present = bool(rec.get("present"))

    highlights = _trust_flag_highlights(inv) if inv_present else None

    return {
        "product_id": str(payload.get("product_id") or ""),
        "rollup_schema": BUILDER_HISTORY_ROW_SCHEMA,
        "updated_at_utc": updated_at,
        "alignment_summary": osum.get("alignment_summary"),
        "alignment_headline": osum.get("alignment_headline"),
        "declared_target_type": decl.get("target_type") if decl.get("present") else None,
        "execution_contract_kind": inv.get("execution_contract_kind") if inv_present else None,
        "target_type_invoke": inv.get("resolved_target_type") if inv_present else None,
        "target_type_reconcile": rec.get("current_target_type") if rec_present else None,
        "invoke_mode": inv.get("mode") if inv_present else None,
        "execution_backend": inv.get("execution_backend") if inv_present else None,
        "invocation_status": inv.get("invocation_status") if inv_present else None,
        "execution_outcome": rec.get("execution_outcome") if rec_present else None,
        "merge_readiness": osum.get("merge_readiness"),
        "cleanup_hint": derive_cleanup_hint(osum.get("merge_readiness"), osum.get("trust_posture")),
        "review_status": rec.get("review_status") if rec_present else None,
        "scope_breach": rec.get("scope_breach") if rec_present else None,
        "path_scope_breach": rec.get("path_scope_breach") if rec_present else None,
        "semantic_scope_breach": rec.get("semantic_scope_breach") if rec_present else None,
        "scope_breach_reasons_preview": reasons_preview,
        "trust_posture": osum.get("trust_posture"),
        "trust_posture_label": trust_posture_label(osum.get("trust_posture")),
        "trust_posture_reasons": tv.get("trust_posture_reasons"),
        "recommended_next_action": osum.get("recommended_next_action"),
        "recommended_next_label": osum.get("recommended_next_label"),
        "sandbox_summary": osum.get("sandbox_summary"),
        "trust_highlights": highlights,
        "branch_isolation_status": inv.get("branch_isolation_status") if inv_present else None,
        "branch_isolation_mode": inv.get("branch_isolation_mode") if inv_present else None,
        "builder_workspace_kind": inv.get("builder_workspace_kind") if inv_present else None,
        "filesystem_scope_mode": inv.get("filesystem_scope_mode") if inv_present else None,
        "containment_applied": inv.get("containment_applied") if inv_present else None,
        "operator_visible_escalation": bool(le.get("present") and le.get("operator_visible")),
        "escalation_severity": le.get("severity") if le.get("present") else None,
        "escalation_packet_path_repo": le.get("path_repo") if le.get("present") else None,
        "escalation_summary_line": osum.get("escalation_summary"),
        "reconcile_escalation_emit": esc_emit,
        "suggested_next": osum.get("suggested_next"),
        "changed_file_count": rec.get("review_changed_file_count") if rec_present else None,
        "latest_invoke_path": inv.get("path") if inv_present else None,
        "latest_reconcile_path": rec.get("path") if rec_present else None,
        "next_expansion_path": decl.get("path") if decl.get("present") else None,
        "prepared_task_path": prep.get("path") if prep.get("present") else None,
        "derived_from_schema": "argus.builder_status.v1",
        "compact_line": format_recent_run_compact_line(
            {
                "product_id": str(payload.get("product_id") or ""),
                "execution_contract_kind": inv.get("execution_contract_kind") if inv_present else None,
                "declared_target_type": decl.get("target_type") if decl.get("present") else None,
                "trust_posture": osum.get("trust_posture"),
                "trust_posture_label": trust_posture_label(osum.get("trust_posture")),
                "recommended_next_label": osum.get("recommended_next_label"),
                "recommended_next_action": osum.get("recommended_next_action"),
            }
        ),
    }


__all__ = [
    "BUILDER_HISTORY_ROW_SCHEMA",
    "builder_history_row_from_status",
    "derive_cleanup_hint",
    "format_recent_run_compact_line",
]
