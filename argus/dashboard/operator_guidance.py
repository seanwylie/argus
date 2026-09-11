"""
Operator-facing guidance strings derived from existing artifacts only (no new state).

Used by the Streamlit console to reduce cognitive load: “what matters first” and “what to do next”.
"""

from __future__ import annotations

from typing import Any

# Severity sort key (lower = higher priority)
_SEV_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "": 9}


def _sev_key(sev: str) -> int:
    return _SEV_ORDER.get(str(sev).strip().lower(), 5)


def enrich_escalation_item(row: dict[str, Any]) -> dict[str, Any]:
    """
    Add normalized label, plain-language hint, suggested steps, and console jump hints.

    ``jump_hints`` are symbolic: ``governance_pending``, ``governance_triage``, ``governance_safe_actions``,
    ``tab_autonomous``, ``tab_needs_you``.
    """
    cat = str(row.get("category") or "").strip()
    sev = str(row.get("severity") or "").strip()
    pid = str(row.get("product_id") or "").strip() or "—"
    ev = str(row.get("evidence_summary") or "").strip()
    ra = str(row.get("requested_action") or "").strip()
    src = str(row.get("source") or "").strip()

    blocker_label = cat.replace("_", " ") if cat else "operator attention"
    plain = ev[:320] + ("…" if len(ev) > 320 else "")
    if not plain and ra:
        plain = ra[:320] + ("…" if len(ra) > 320 else "")
    if not plain:
        plain = f"Item from {src or 'escalation inbox'} for product `{pid}`."

    steps: list[str] = []
    jumps: list[str] = []

    if cat == "approval_needed":
        blocker_label = "Approval / confirmation needed"
        steps = [
            "Open **Governance & actions** → Pending approvals — respond (confirm_once / always / no) or adjust policy YAML.",
            "If this is subprocess-scoped, use the scoped action id shown on the pending artifact.",
        ]
        jumps = ["governance_pending", "governance_triage"]
    elif cat == "external_dependency":
        blocker_label = "External dependency (outside Argus automation)"
        steps = [
            "Disambiguate (still inside Argus): **pending Phase 2 approvals?** → **Governance & actions** → Pending.",
            "**Policy yes/confirm but environment not ready?** → same tab → product drill-in (policy × environment).",
            "**Truly outside** (vendor, manual work elsewhere): track outside Argus; after it lands, use **Governance** safe actions or **Overview** refresh so artifacts catch up.",
        ]
        jumps = ["governance_pending", "governance_triage", "governance_safe_actions"]
    elif cat == "review_needed":
        blocker_label = "Review needed"
        steps = [
            "Read the evidence summary, then use **Governance & actions** product drill-in for policy/env context.",
            "Optionally run **Orchestration advance (dry)** for the product after you understand the gate.",
        ]
        jumps = ["governance_triage", "governance_safe_actions"]
    elif cat == "unsafe_to_continue":
        blocker_label = "Unsafe to continue (stop)"
        steps = [
            "Treat as high priority: read **Autonomous session** for the latest stop reason and session summary.",
            "Do not force progression until the safety concern is understood.",
        ]
        jumps = ["tab_autonomous", "tab_needs_you"]
    elif cat == "informational":
        blocker_label = "Informational (context)"
        steps = ["Skim for context; no mandatory operator action unless policy says otherwise."]
        jumps = ["tab_needs_you"]
    else:
        steps = [
            "Review evidence and requested action in **Needs you**, then use **Governance & actions** if policy/approvals are involved.",
        ]
        jumps = ["tab_needs_you", "governance_triage"]

    return {
        "item_id": row.get("item_id"),
        "product_id": pid,
        "category": cat,
        "severity": sev,
        "blocker_label": blocker_label,
        "plain_explanation": plain,
        "requested_action_short": ra[:240] + ("…" if len(ra) > 240 else "") if ra else "",
        "recommended_steps": steps,
        "jump_hints": jumps,
        "source": src,
    }


def pick_highest_priority_escalation(actionable_raw: list[dict[str, Any]]) -> dict[str, Any] | None:
    """First item when sorted by severity (critical first) then existing order."""
    if not actionable_raw:
        return None
    ranked = sorted(
        actionable_raw,
        key=lambda r: (_sev_key(str(r.get("severity") or "")), str(r.get("item_id") or "")),
    )
    return ranked[0]


# From :func:`autonomous_session_primary_status` — worth surfacing in “start here”.
# Map jump_hint keys to Governance tab focus (session_state ``gov_highlight``).
GOV_FOCUS_BY_HINT: dict[str, str] = {
    "governance_pending": "pending",
    "governance_triage": "triage",
    "governance_safe_actions": "safe",
}

# Human-readable routing (cross-tab is manual — Streamlit cannot switch tabs programmatically).
JUMP_HINT_CAPTION: dict[str, str] = {
    "tab_governance": "**Governance & actions** tab — policy, approvals, safe CLI",
    "tab_autonomous": "**Autonomous session** tab — runner heartbeat & latest session",
    "tab_needs_you": "**Needs you** tab — escalation inbox (actionable items)",
    "tab_over": "**Overview** tab — operator summary & narrative",
    "tab_q": "**Queue** tab — portfolio operator queue",
    "governance_pending": "**Governance** → Pending Phase 2 approvals (use Focus button below)",
    "governance_triage": "**Governance** → Product triage & drill-in",
    "governance_safe_actions": "**Governance** → Safe actions (bottom)",
}


def jump_hint_caption(hint: str) -> str:
    return JUMP_HINT_CAPTION.get(hint, hint)

_AUTONOMOUS_ATTENTION_PRIMARY = frozenset(
    {
        "stopped_quiescent",
        "stopped_pipeline_error",
        "stopped_cycle_guardrail",
        "stopped_intervention_heavy",
        "stopped_manual",
    },
)


def compute_start_here(
    *,
    escalation_actionable_raw: list[dict[str, Any]],
    pending_approval_count: int,
    autonomous_primary_status: str | None,
    autonomous_stop_reason: str | None,
    runner_stale_note: str | None,
    operator_headline: str | None,
    operator_next_step: str | None,
    portfolio_zero_state: bool = False,
    zero_state_world_context: str | None = None,
    zero_state_creation_hint: str | None = None,
) -> dict[str, Any]:
    """
    Single “start here” card: headline, why stalled, next action, jump hints.

    All inputs must already exist in artifacts; no I/O here.
    """
    top = pick_highest_priority_escalation(escalation_actionable_raw)
    why: list[str] = []
    headline = "No urgent blocker detected in escalation inbox."
    next_action = "Use **Overview** / **Queue** for routine context, or **Governance & actions** for policy and approvals."
    jumps: list[str] = ["tab_over", "tab_q", "tab_governance"]

    if top:
        g = enrich_escalation_item(top)
        headline = f"{g['blocker_label']} — product `{g['product_id']}`"
        why.append(g["plain_explanation"])
        if g.get("requested_action_short"):
            why.append(f"Requested action: {g['requested_action_short']}")
        next_action = " → ".join(g["recommended_steps"][:2]) if g["recommended_steps"] else next_action
        jumps = list(dict.fromkeys(g.get("jump_hints") or [] + ["tab_needs_you", "tab_governance"]))
    elif pending_approval_count > 0:
        headline = f"{pending_approval_count} pending Phase 2 approval(s)"
        why.append("Execution is blocked on confirm policy until you respond or change policy.")
        next_action = "Open **Governance & actions** → Pending approvals, or edit product `argus.policy.yaml`."
        jumps = ["tab_governance"]
    elif portfolio_zero_state:
        headline = "Portfolio zero-state — no validated products yet"
        why.append(
            "Inventory under products/ is empty. Routine portfolio automation has nothing to operate on; "
            "add a product before expecting queue/cycle output."
        )
        zc = str(zero_state_world_context or "").strip()
        if zc:
            if len(zc) > 500:
                zc = zc[:497] + "…"
            why.append(f"External context (advisory, not authoritative): {zc}")
        zh = str(zero_state_creation_hint or "").strip()
        if zh:
            if len(zh) > 400:
                zh = zh[:397] + "…"
            why.append(zh)
        next_action = str(operator_next_step or "See **Overview** operator summary for next steps.")
        jumps = ["tab_over", "tab_q"]
    elif str(autonomous_primary_status or "") == "stopped_empty_portfolio":
        headline = "Autonomous session stopped: empty portfolio"
        why.append(
            "Portfolio refresh completed in zero-state — there are no validated products to cycle yet. "
            "This is intentional, not a pipeline error."
        )
        next_action = str(operator_next_step or "Add a product under products/, run `argus portfolio refresh`, then retry.")
        jumps = ["tab_autonomous", "tab_over"]
    elif (
        autonomous_primary_status
        and str(autonomous_primary_status).strip() in _AUTONOMOUS_ATTENTION_PRIMARY
        and autonomous_stop_reason
    ):
        sr = str(autonomous_stop_reason).strip()
        headline = f"Autonomous session attention: {sr}"
        why.append(
            f"Latest session primary status is `{autonomous_primary_status}` (see **Autonomous session** for detail)."
        )
        next_action = "Open **Autonomous session** — check stop reason, per-cycle outcomes, and refresh artifacts after you address the gate."
        jumps = ["tab_autonomous"]
    elif runner_stale_note:
        headline = "Runner service heartbeat may be stale"
        why.append(runner_stale_note)
        next_action = "Check the runner process / **Autonomous session** tab; restart cadence if needed."
        jumps = ["tab_autonomous"]
    elif operator_headline:
        headline = str(operator_headline)
        if operator_next_step:
            next_action = str(operator_next_step)
        why.append("From latest operator summary artifact.")
        jumps = ["tab_over"]

    return {
        "headline": headline[:500],
        "why_lines": [x for x in why if x],
        "recommended_next": next_action[:900],
        "jump_hints": jumps[:12],
    }


def recommended_product_actions(
    phase1_summary: dict[str, Any],
    *,
    pending_count_for_product: int,
    active_grants: int,
) -> list[str]:
    """
    Short bullet list for “Recommended operator actions” from existing phase1_summary_for_product output.
    """
    out: list[str] = []
    if phase1_summary.get("policy_load_error"):
        out.append("Create or fix `products/<id>/argus.policy.yaml` so the policy loads (see error below).")
        return out

    mism = phase1_summary.get("policy_environment_mismatches") or []
    if mism:
        out.append(
            "Fix **policy vs environment** gaps: policy asks yes/confirm for a key the environment cannot satisfy yet — adjust policy, containment, or signals (see mismatch table).",
        )
    if pending_count_for_product > 0:
        out.append(
            f"Approve pending action(s): **{pending_count_for_product}** in **Governance** → Pending (confirm_once / always / no).",
        )
    elif active_grants > 0:
        out.append(
            "Active grants on file — confirm_once may consume on the next matching governed run; **always** persists until revoked.",
        )

    if not mism and pending_count_for_product == 0:
        if active_grants == 0:
            out.append(
                "**No action needed** for policy/env alignment and approvals in current artifacts — optional: safe refresh or orchestration advance (dry) from Governance.",
            )
        else:
            out.append(
                "Optional: after external or repo changes, use **Governance** safe actions to refresh artifacts and re-check alignment.",
            )
    return out
