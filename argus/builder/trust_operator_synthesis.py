"""
Deterministic trust posture + recommended next action for Builder operator UX.

Derived only from :func:`compute_builder_status` payload fields (invoke + reconcile blocks).
Does not change merge/reconcile semantics — labels existing truth for display.
"""

from __future__ import annotations

from typing import Any, Literal

BUILDER_TRUST_OPERATOR_VIEW_SCHEMA = "argus.builder_trust_operator_view.v1"

TrustPosture = Literal[
    "unsafe_scope_breach",
    "blocked",
    "degraded_unsandboxed",
    "degraded_plain_argus_root",
    "ready_to_review",
    "review_carefully",
    "unknown_incomplete",
]

RecommendedNext = Literal[
    "investigate_scope_breach",
    "review_and_merge",
    "inspect_diff",
    "rerun_prepare",
    "fix_host_readiness",
    "run_invoke_reconcile",
    "review_escalation",
    "fix_declaration",
    "no_clear_action",
]

# Short UI-facing labels (machine codes above stay canonical in JSON).
TRUST_POSTURE_LABELS: dict[str, str] = {
    "unsafe_scope_breach": "Unsafe scope",
    "blocked": "Blocked",
    "degraded_unsandboxed": "Degraded — unsandboxed",
    "degraded_plain_argus_root": "Degraded — Argus root workspace",
    "ready_to_review": "Ready to review",
    "review_carefully": "Review carefully",
    "unknown_incomplete": "Incomplete or unknown",
}

RECOMMENDED_NEXT_LABELS: dict[str, str] = {
    "investigate_scope_breach": "Investigate scope",
    "review_and_merge": "Review diff, then merge if appropriate",
    "inspect_diff": "Inspect diff and records",
    "rerun_prepare": "Rerun prepare",
    "fix_host_readiness": "Fix host readiness (sandbox)",
    "run_invoke_reconcile": "Run invoke / reconcile",
    "review_escalation": "Review escalation packet",
    "fix_declaration": "Fix declaration (next_expansion)",
    "no_clear_action": "No required action",
}

# Presentation-only: exact strings emitted by :func:`derive_trust_operator_view` → natural phrasing.
# ``trust_posture_reasons`` / ``recommended_next_reasons`` in JSON stay unchanged.
TRUST_POSTURE_REASON_PHRASES: dict[str, str] = {
    "scope or merge review labels unsafe/breached": "Scope breach or merge review marked unsafe.",
    "merge blocked or failed execute invoke": "Merge is blocked, or execute-mode invoke failed.",
    "unsandboxed or containment fallback on agent execute": "Agent run was unsandboxed or used containment fallback.",
    "Argus-root git workspace without product-scoped isolation": (
        "Working from repo root without product-scoped workspace."
    ),
    "merge_candidate with declaration/prepare not blocking": (
        "Merge candidate; declaration and prepare are aligned enough to show this label."
    ),
    "review_required, weak outcome evidence, or diff baseline fallback": (
        "Review required, weak outcome evidence, or diff used a baseline fallback."
    ),
    "missing records, declaration, or alignment incomplete": (
        "Missing loop records, declaration, or alignment is incomplete."
    ),
    "default conservative review": "Default: treat as needing careful review.",
}

RECOMMENDED_NEXT_REASON_PHRASES: dict[str, str] = {
    "content/next_expansion.json missing or invalid": "Missing or invalid `content/next_expansion.json`.",
    "prepare artifacts missing or stale vs declaration": (
        "Prepare output is missing or out of date vs the declaration."
    ),
    "invoke and/or reconcile record absent": "Invoke or reconcile snapshot is missing on disk.",
    "recorded targets drift from declaration — refresh prepare/loop": (
        "Recorded targets drift from the declaration — refresh prepare and re-run the loop."
    ),
    "scope breach or unsafe merge posture": "Scope breach or unsafe merge posture.",
    "last_escalation severity high/critical": "Latest escalation is high or critical severity.",
    "reconcile emitted builder_escalation packet": "Reconcile emitted a Builder escalation packet.",
    "degraded containment or workspace scope — verify invoke/reconcile before merge": (
        "Containment or workspace scope looks degraded — read invoke/reconcile before merging."
    ),
    "merge_candidate after manual review of diff/trust": (
        "Merge candidate after you review diff and trust signals."
    ),
    "no_new_privs not applied — check setpriv / Linux host readiness": (
        "`no_new_privs` was not applied — check host sandbox readiness."
    ),
    "inspect diff, invoke record, and reconcile detail before merge": (
        "Inspect diff, invoke record, and reconcile detail before merging."
    ),
    "aligned — follow suggested_next or continue loop as needed": (
        "Aligned — follow suggested next steps or continue the loop."
    ),
    "insufficient signal — inspect status JSON": "Thin signal — open status JSON for detail.",
}


def humanize_trust_posture_reason(raw: str) -> str:
    """Natural phrasing for a single ``trust_posture_reasons`` entry; unknown text passes through."""
    s = str(raw).strip()
    if not s:
        return s
    return TRUST_POSTURE_REASON_PHRASES.get(s, s)


def humanize_recommended_next_reason(raw: str) -> str:
    """Natural phrasing for a single ``recommended_next_reasons`` entry; unknown text passes through."""
    s = str(raw).strip()
    if not s:
        return s
    return RECOMMENDED_NEXT_REASON_PHRASES.get(s, s)


def trust_posture_label(code: str | None) -> str:
    """Human-readable trust posture title; unknown codes pass through."""
    if not code:
        return "—"
    c = str(code).strip()
    return TRUST_POSTURE_LABELS.get(c, c)


def recommended_next_label(code: str | None) -> str:
    """Human-readable next-step title; unknown codes pass through."""
    if not code:
        return "—"
    c = str(code).strip()
    return RECOMMENDED_NEXT_LABELS.get(c, c)


def human_summary_lines_for_trust_view(trust_view: dict[str, Any]) -> list[str]:
    """
    One or two concise operator lines derived from machine codes + existing reason strings.

    Does not change precedence; only formats fields already on ``trust_view``.
    """
    pl = trust_posture_label(trust_view.get("trust_posture"))
    pr = trust_view.get("trust_posture_reasons")
    pr0 = ""
    if isinstance(pr, list) and pr:
        pr0 = humanize_trust_posture_reason(str(pr[0]))
    line1 = f"Trust — {pl}"
    if pr0:
        line1 = f"{line1}: {pr0[:200]}"

    nl = recommended_next_label(trust_view.get("recommended_next_action"))
    ar = trust_view.get("recommended_next_reasons")
    ar0 = ""
    if isinstance(ar, list) and ar:
        ar0 = humanize_recommended_next_reason(str(ar[0]))
    line2 = f"Next — {nl}"
    if ar0:
        line2 = f"{line2}: {ar0[:200]}"

    return [line1, line2]


def augment_trust_operator_view_for_display(trust_view: dict[str, Any]) -> dict[str, Any]:
    """
    Copy of ``trust_view`` with additive display fields. Machine codes and reason lists unchanged.

    Added keys: ``trust_posture_label``, ``recommended_next_label``, ``human_summary_lines``,
    ``trust_posture_reasons_display``, ``recommended_next_reasons_display`` (parallel to machine reason lists).
    """
    out = dict(trust_view)
    out["trust_posture_label"] = trust_posture_label(out.get("trust_posture"))
    out["recommended_next_label"] = recommended_next_label(out.get("recommended_next_action"))
    tpr = out.get("trust_posture_reasons")
    if isinstance(tpr, list):
        out["trust_posture_reasons_display"] = [humanize_trust_posture_reason(str(x)) for x in tpr[:8]]
    else:
        out["trust_posture_reasons_display"] = []
    rnr = out.get("recommended_next_reasons")
    if isinstance(rnr, list):
        out["recommended_next_reasons_display"] = [humanize_recommended_next_reason(str(x)) for x in rnr[:8]]
    else:
        out["recommended_next_reasons_display"] = []
    out["human_summary_lines"] = human_summary_lines_for_trust_view(out)
    return out


def derive_trust_operator_view(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return trust posture + recommended next action + short precedence notes.

    **Precedence (trust_posture — first match wins, most severe first):**

    1. ``unsafe_scope_breach`` — reconcile ``scope_breach``, or ``review_status == unsafe``,
       or ``execution_outcome == breached``.
    2. ``blocked`` — ``review_status == blocked``, or last invoke ``invocation_status == failed``
       with ``mode == execute`` (not already classified as unsafe).
    3. ``degraded_unsandboxed`` — agent ``execute`` run with ``trust_degraded_unsandboxed`` or
       ``containment_fallback_used`` (bubblewrap not in effect as intended).
    4. ``degraded_plain_argus_root`` — ``builder_workspace_kind == argus_root`` with
       ``trust_degraded_workspace_scope`` (monorepo root without nested product isolation).
    5. ``ready_to_review`` — ``review_status == merge_candidate`` and alignment is not a
       blocking prepare/declaration gap (aligned, drift, or records present enough to show merge label).
    6. ``review_carefully`` — ``review_status == review_required``, or diff fallback, or execution
       outcome partial/unknown, or other trust flags not covered above.
    7. ``unknown_incomplete`` — missing invoke/reconcile records, missing declaration, or alignment
       states that prevent a merge/readiness story.

    **Precedence (recommended_next_action — operational order, first match wins):**

    1. ``fix_declaration`` — ``alignment_summary == missing_declared_target``.
    2. ``rerun_prepare`` — ``missing_prepared`` or ``prepared_stale``.
    3. ``run_invoke_reconcile`` — ``records_missing``.
    4. ``rerun_prepare`` — ``drift_detected`` (refresh prepare, then loop).
    5. ``investigate_scope_breach`` — unsafe scope / breach posture (see trust_posture rule 1).
    6. ``review_escalation`` — last escalation severity high/critical, or reconcile emitted packet.
    7. ``inspect_diff`` — trust posture ``degraded_unsandboxed`` or ``degraded_plain_argus_root`` (even if
       reconcile still carries a stale ``merge_candidate`` — defensive for inconsistent snapshots).
    8. ``review_and_merge`` — ``review_status == merge_candidate`` (after above gates).
    9. ``fix_host_readiness`` — agent execute with ``trust_degraded_missing_no_new_privs``.
    10. ``inspect_diff`` — blocked, ``review_required``, or other paths needing human review.
    11. ``no_clear_action`` — aligned with activity and no stronger signal.
    """
    align = str(payload.get("alignment_summary") or "unknown")
    inv = payload.get("latest_invoke") if isinstance(payload.get("latest_invoke"), dict) else {}
    rec = (
        payload.get("latest_reconcile")
        if isinstance(payload.get("latest_reconcile"), dict)
        else {}
    )
    le = payload.get("last_escalation") if isinstance(payload.get("last_escalation"), dict) else {}

    inv_present = bool(inv.get("present"))
    rec_present = bool(rec.get("present"))
    has_activity = inv_present or rec_present

    rs = str(rec.get("review_status") or "").strip()
    eo = str(rec.get("execution_outcome") or "").strip()
    scope_breach = bool(rec.get("scope_breach"))
    diff_fb = bool(rec.get("diff_fallback_used"))

    mode = str(inv.get("mode") or "")
    backend = str(inv.get("execution_backend") or "").strip().lower()
    inv_status = str(inv.get("invocation_status") or "").strip()
    gwk = str(inv.get("builder_workspace_kind") or "").strip()
    td_ws = bool(inv.get("trust_degraded_workspace_scope"))
    td_un = bool(inv.get("trust_degraded_unsandboxed"))
    cf_fallback = bool(inv.get("containment_fallback_used"))
    td_nnp = bool(inv.get("trust_degraded_missing_no_new_privs"))

    posture_reasons: list[str] = []
    posture: TrustPosture

    unsafe = (
        scope_breach
        or rs == "unsafe"
        or eo == "breached"
    )
    if unsafe:
        posture_reasons.append("scope or merge review labels unsafe/breached")
        posture = "unsafe_scope_breach"
    elif rs == "blocked" or (mode == "execute" and inv_status == "failed"):
        posture_reasons.append("merge blocked or failed execute invoke")
        posture = "blocked"
    elif mode == "execute" and backend == "agent" and (td_un or cf_fallback):
        posture_reasons.append("unsandboxed or containment fallback on agent execute")
        posture = "degraded_unsandboxed"
    elif gwk == "argus_root" and td_ws:
        posture_reasons.append("Argus-root git workspace without product-scoped isolation")
        posture = "degraded_plain_argus_root"
    elif rs == "merge_candidate" and align not in (
        "missing_declared_target",
        "missing_prepared",
        "prepared_stale",
        "drift_detected",
    ):
        posture_reasons.append("merge_candidate with declaration/prepare not blocking")
        posture = "ready_to_review"
    elif rs == "review_required" or eo in ("partial", "unknown") or diff_fb:
        if not posture_reasons:
            posture_reasons.append("review_required, weak outcome evidence, or diff baseline fallback")
        posture = "review_carefully"
    elif not has_activity or align in (
        "missing_declared_target",
        "records_missing",
        "unknown",
    ):
        posture = "unknown_incomplete"
        posture_reasons.append("missing records, declaration, or alignment incomplete")
    else:
        posture = "review_carefully"
        posture_reasons.append("default conservative review")

    # --- recommended_next (operational precedence) ---
    pid = str(payload.get("product_id") or "").strip() or "PRODUCT_ID"
    action: RecommendedNext
    action_reasons: list[str] = []

    if align == "missing_declared_target":
        action = "fix_declaration"
        action_reasons.append("content/next_expansion.json missing or invalid")
    elif align in ("missing_prepared", "prepared_stale"):
        action = "rerun_prepare"
        action_reasons.append("prepare artifacts missing or stale vs declaration")
    elif align == "records_missing":
        action = "run_invoke_reconcile"
        action_reasons.append("invoke and/or reconcile record absent")
    elif align == "drift_detected":
        action = "rerun_prepare"
        action_reasons.append("recorded targets drift from declaration — refresh prepare/loop")
    elif unsafe:
        action = "investigate_scope_breach"
        action_reasons.append("scope breach or unsafe merge posture")
    elif le.get("present") and str(le.get("severity") or "").lower() in ("high", "critical"):
        action = "review_escalation"
        action_reasons.append("last_escalation severity high/critical")
    elif rec.get("escalation_emit_emitted"):
        action = "review_escalation"
        action_reasons.append("reconcile emitted builder_escalation packet")
    elif posture in ("degraded_unsandboxed", "degraded_plain_argus_root"):
        action = "inspect_diff"
        action_reasons.append("degraded containment or workspace scope — verify invoke/reconcile before merge")
    elif rs == "merge_candidate":
        action = "review_and_merge"
        action_reasons.append("merge_candidate after manual review of diff/trust")
    elif td_nnp and mode == "execute" and backend == "agent":
        action = "fix_host_readiness"
        action_reasons.append("no_new_privs not applied — check setpriv / Linux host readiness")
    elif rs in ("blocked", "review_required"):
        action = "inspect_diff"
        action_reasons.append("inspect diff, invoke record, and reconcile detail before merge")
    elif align == "aligned" and has_activity:
        action = "no_clear_action"
        action_reasons.append("aligned — follow suggested_next or continue loop as needed")
    else:
        action = "inspect_diff"
        action_reasons.append("insufficient signal — inspect status JSON")

    return {
        "schema": BUILDER_TRUST_OPERATOR_VIEW_SCHEMA,
        "trust_posture": posture,
        "trust_posture_reasons": posture_reasons[:8],
        "recommended_next_action": action,
        "recommended_next_reasons": action_reasons[:8],
        "product_id": pid,
    }


__all__ = [
    "BUILDER_TRUST_OPERATOR_VIEW_SCHEMA",
    "RECOMMENDED_NEXT_LABELS",
    "RECOMMENDED_NEXT_REASON_PHRASES",
    "TRUST_POSTURE_LABELS",
    "TRUST_POSTURE_REASON_PHRASES",
    "augment_trust_operator_view_for_display",
    "derive_trust_operator_view",
    "human_summary_lines_for_trust_view",
    "humanize_recommended_next_reason",
    "humanize_trust_posture_reason",
    "recommended_next_label",
    "trust_posture_label",
]
