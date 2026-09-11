"""
Builder v1.3: read-only snapshot of declared target, prepared contract, and latest loop records.

Does not mutate product state. Invoke/reconcile JSON files are observational, not proof of quality.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from argus.builder.branch_review import (
    compute_builder_branch_review,
    format_builder_branch_review_human,
)
from argus.builder.escalation_bridge import summarize_latest_builder_escalation
from argus.builder.invoke import (
    BUILDER_TASK_FILENAME,
    invoke_record_dir,
    try_resolve_prepared_artifacts,
)
from argus.builder.next_expansion_prepare import next_expansion_path
from argus.builder.reconcile import reconcile_record_dir
from argus.builder.trust_operator_synthesis import (
    augment_trust_operator_view_for_display,
    derive_trust_operator_view,
)

BUILDER_STATUS_SCHEMA = "argus.builder_status.v1"
BUILDER_OPERATOR_SUMMARY_SCHEMA = "argus.builder_operator_summary.v1"

AlignmentSummary = Literal[
    "aligned",
    "missing_declared_target",
    "missing_prepared",
    "prepared_stale",
    "records_missing",
    "drift_detected",
    "unknown",
]


def _rel(repo_root: Path, p: Path) -> str:
    try:
        return str(p.resolve().relative_to(repo_root.resolve()))
    except ValueError:
        return str(p.resolve())


def _safe_read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)
    if not isinstance(raw, dict):
        return None, "root must be an object"
    return raw, None


def _norm_target(d: dict[str, Any] | None) -> dict[str, Any] | None:
    if not d or not isinstance(d, dict):
        return None
    return {
        "id": d.get("id"),
        "target_type": d.get("target_type"),
        "group_id": d.get("group_id"),
    }


def _targets_equal(a: dict[str, Any] | None, b: dict[str, Any] | None) -> bool:
    if not a or not b:
        return False
    return (
        a.get("id") == b.get("id")
        and a.get("target_type") == b.get("target_type")
        and a.get("group_id") == b.get("group_id")
    )


def _alignment_notes(
    summary: AlignmentSummary,
    *,
    declared: dict[str, Any] | None,
    prepared: dict[str, Any] | None,
    invoke_target: dict[str, Any] | None,
    reconcile_target: dict[str, Any] | None,
) -> list[str]:
    notes: list[str] = []
    if summary == "aligned":
        notes.append(
            "Declared next_expansion, prepared builder_task, and latest invoke/reconcile records "
            "agree on the same normalized target (id, target_type, group_id)."
        )
        return notes
    if summary == "missing_declared_target":
        notes.append(
            "content/next_expansion.json is the source of truth for the next Builder target; "
            "without a readable primary_target, alignment cannot be established."
        )
        return notes
    if summary == "missing_prepared":
        notes.append(
            "Run `argus builder prepare <PRODUCT_ID>` (or invoke --prepare-first) so "
            "products/<id>/generated/ mirrors the declared contract."
        )
        return notes
    if summary == "prepared_stale":
        notes.append(
            "Prepared builder_task.json does not match declared primary_target — "
            "prepare likely needs to run after next_expansion changed."
        )
        return notes
    if summary == "records_missing":
        notes.append(
            "Invoke and reconcile records are operational breadcrumbs, not proof of implementation; "
            "missing files mean the loop was not fully recorded."
        )
        return notes
    if summary == "drift_detected":
        if declared and invoke_target and not _targets_equal(declared, invoke_target):
            notes.append(
                "Latest invoke record points at a different target than the current declaration — "
                "re-invoke after prepare, or the record predates a next_expansion edit."
            )
        if declared and reconcile_target and not _targets_equal(declared, reconcile_target):
            notes.append(
                "Latest reconcile current_target differs from declared primary_target — "
                "reconcile may be stale, or next_expansion changed since the last reconcile."
            )
        if not notes:
            notes.append("Observed target snapshots disagree; see sections below.")
        return notes
    if summary == "unknown":
        notes.append(
            "A loop record file exists but could not be parsed as JSON, or another edge case "
            "prevented a clean alignment verdict — inspect parse_error fields and paths."
        )
        return notes
    notes.append("Could not classify alignment cleanly; inspect raw sections and paths.")
    return notes


def _alignment_headline(alignment_summary: str) -> str:
    return {
        "aligned": "Declared target, prepared task, and latest records agree.",
        "missing_declared_target": "Declare a next target (content/next_expansion.json).",
        "missing_prepared": "Run builder prepare so generated/ matches the declaration.",
        "prepared_stale": "Prepared task is stale — run builder prepare after next_expansion changed.",
        "records_missing": "Missing invoke and/or reconcile record — loop not fully recorded.",
        "drift_detected": "Record(s) point at a different target than the current declaration.",
        "unknown": "Could not classify alignment (parse error or edge case).",
    }.get(alignment_summary, alignment_summary)


def _sandbox_operator_line(invoke_block: dict[str, Any]) -> str:
    """One human line: was the last agent run sandboxed / trust state."""
    if not invoke_block.get("present"):
        return "No invoke record — nothing executed under Builder yet."
    mode = invoke_block.get("mode")
    backend = (invoke_block.get("execution_backend") or "").strip().lower()
    if mode != "execute":
        return "Review-only invoke — agent did not execute (--execute not used)."
    if not backend:
        return "Invoke record missing execution_backend — open full status for detail."
    if backend != "agent":
        return f"Backend {backend!r} — outer bubblewrap sandbox applies to agent CLI only."
    cap = invoke_block.get("containment_applied")
    if cap == "bwrap":
        nm = invoke_block.get("network_mode") or "default"
        na = invoke_block.get("network_applied")
        net = f"network {nm}"
        if na is False:
            net = "offline (network isolated)"
        elif nm == "disabled" and na is True:
            net = "offline (--unshare-net)"
        parts = ["Sandboxed (bubblewrap)", net]
        if invoke_block.get("trust_degraded_unsandboxed") or invoke_block.get(
            "containment_fallback_used"
        ):
            parts.append("trust: degraded (unsandboxed or fallback — see detail)")
        if invoke_block.get("trust_degraded_missing_no_new_privs"):
            parts.append("trust: no_new_privs not applied")
        if invoke_block.get("trust_degraded_workspace_scope"):
            parts.append("trust: Argus-root workspace (not product-scoped)")
        if invoke_block.get("trust_degraded_network_open"):
            parts.append("trust: explicit allow_all network")
        return " · ".join(parts)
    if invoke_block.get("trust_degraded_unsandboxed") or invoke_block.get(
        "containment_fallback_used"
    ):
        return "Not sandboxed or sandbox failed — trust degraded (see invoke record)."
    return f"Containment: {cap or 'unknown'} — see latest invoke for trust flags."


def _suggested_next_operator(
    *,
    product_id: str,
    alignment_summary: str,
    invoke_block: dict[str, Any],
    reconcile_block: dict[str, Any],
    last_esc: dict[str, Any],
) -> str:
    """Single imperative line for operators (not schema vocabulary)."""
    rs = reconcile_block.get("review_status")
    pid = product_id.strip() or "PRODUCT_ID"

    if alignment_summary == "missing_prepared":
        return f"Run: argus builder prepare {pid}"
    if alignment_summary == "missing_declared_target":
        return "Fix or add content/next_expansion.json with a valid primary_target."
    if alignment_summary == "prepared_stale":
        return f"Run: argus builder prepare {pid} (prepared task does not match declaration)."
    if alignment_summary == "records_missing":
        return f"Run: argus builder invoke {pid} and argus builder reconcile {pid} to record the loop."
    if alignment_summary == "drift_detected":
        return "Re-run prepare and invoke so records match the current declared target."
    if last_esc.get("present") and str(last_esc.get("severity") or "").lower() in (
        "high",
        "critical",
    ):
        return "Review the Builder escalation under runs/escalations/latest/ (high/critical)."

    if reconcile_block.get("escalation_emit_emitted"):
        pkt = reconcile_block.get("escalation_emit_packet_id") or "packet"
        return f"Review reconcile-time escalation ({pkt}) under runs/escalations/latest/."
    if reconcile_block.get("escalation_emit_reason") == "dedupe_recent_packet":
        return "Escalation deduped recently — see reconcile JSON if you need rule-level detail."

    if rs == "unsafe":
        return "Do not merge. Fix scope or revert the builder branch before continuing."
    if rs == "blocked":
        return "Not ready to merge — fix invoke failure, branch isolation, or outcome (see merge readiness detail)."
    if rs == "merge_candidate":
        return f"Optional: argus builder merge {pid} after you review the diff (manual merge only)."
    if rs == "review_required":
        return "Review changes and trust flags manually before any merge; merge_candidate not assigned."

    if invoke_block.get("present") and invoke_block.get("mode") == "review":
        return f"To execute: argus builder invoke {pid} --execute (after policy/builder_execute allows it)."

    return "No special action — see alignment and records below if you are advancing the loop."


def build_builder_operator_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Compact, operator-facing summary derived from :func:`compute_builder_status` payload.

    Intended for CLI ``--brief``, Streamlit, and JSON consumers — same truth as status, less jargon.
    """
    pid = str(payload.get("product_id") or "")
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

    review = rec.get("review_status")
    outcome = rec.get("execution_outcome")

    esc_line = "None"
    if le.get("present"):
        sev = le.get("severity") or "?"
        sr = str(le.get("short_reason") or le.get("title") or "").strip()
        esc_line = f"{sev}: {sr}" if sr else str(sev)
    elif rec.get("escalation_emit_emitted"):
        esc_line = f"Emitted ({rec.get('escalation_emit_packet_id') or 'packet'})"
    elif rec.get("escalation_emit_reason") == "dedupe_recent_packet":
        esc_line = "Deduped (same triggers recently — no new packet)"

    suggested = _suggested_next_operator(
        product_id=pid,
        alignment_summary=align,
        invoke_block=inv,
        reconcile_block=rec,
        last_esc=le,
    )

    trust_view = augment_trust_operator_view_for_display(derive_trust_operator_view(payload))

    return {
        "schema": BUILDER_OPERATOR_SUMMARY_SCHEMA,
        "product_id": pid,
        "has_builder_activity": has_activity,
        "alignment_summary": align,
        "alignment_headline": _alignment_headline(align),
        "last_invoked_at_utc": inv.get("invoked_at_utc") if inv_present else None,
        "last_reconciled_at_utc": rec.get("reconciled_at_utc") if rec_present else None,
        "invoke_mode": inv.get("mode"),
        "execution_backend": inv.get("execution_backend"),
        "invocation_status": inv.get("invocation_status"),
        "sandbox_summary": _sandbox_operator_line(inv),
        "execution_outcome": outcome,
        "merge_readiness": review,
        "escalation_summary": esc_line,
        "suggested_next": suggested,
        "trust_posture": trust_view.get("trust_posture"),
        "recommended_next_action": trust_view.get("recommended_next_action"),
        "trust_posture_label": trust_view.get("trust_posture_label"),
        "recommended_next_label": trust_view.get("recommended_next_label"),
        "operator_trust_human_lines": trust_view.get("human_summary_lines"),
        "trust_operator_view": trust_view,
    }


def format_builder_operator_summary_human(payload: dict[str, Any]) -> str:
    """Short multi-line text for operators (CLI / dashboard)."""
    osum = payload.get("operator_summary")
    if not isinstance(osum, dict) or not osum:
        osum = build_builder_operator_summary(payload)
    pid = osum.get("product_id") or "?"
    lines = [
        f"=== Builder — {pid} ===",
        f"Alignment: {osum.get('alignment_headline') or osum.get('alignment_summary')}",
    ]
    if not osum.get("has_builder_activity"):
        lines.append("Activity: no invoke/reconcile records on disk yet.")
    else:
        iv = osum.get("last_invoked_at_utc") or "—"
        rc = osum.get("last_reconciled_at_utc") or "—"
        lines.append(f"Last invoke (UTC): {iv}  ·  Last reconcile (UTC): {rc}")
    mode = osum.get("invoke_mode")
    back = osum.get("execution_backend")
    ist = osum.get("invocation_status")
    lines.append(f"Last run: mode={mode or '—'} · backend={back or '—'} · status={ist or '—'}")
    lines.append(f"Sandbox: {osum.get('sandbox_summary') or '—'}")
    eo = osum.get("execution_outcome")
    lines.append(f"Execution outcome: {eo if eo is not None else '—'} (from reconcile)")
    mr = osum.get("merge_readiness")
    lines.append(f"Merge readiness: {mr or '—'}")
    lines.append(f"Escalation: {osum.get('escalation_summary') or '—'}")
    hlines = osum.get("operator_trust_human_lines")
    if isinstance(hlines, list) and hlines:
        lines.append("")
        lines.append("### Trust & next")
        for hl in hlines[:2]:
            if isinstance(hl, str) and hl.strip():
                lines.append(f"- {hl.strip()}")
        tp = osum.get("trust_posture")
        na = osum.get("recommended_next_action")
        lines.append(
            f"- Machine: `trust_posture={tp or '—'}` · `recommended_next_action={na or '—'}`"
        )
    else:
        tp = osum.get("trust_posture")
        na = osum.get("recommended_next_action")
        if tp:
            lines.append(f"Trust posture: {tp}")
        if na:
            lines.append(f"Recommended next: {na}")
    lines.append(f"Next: {osum.get('suggested_next') or '—'}")
    return "\n".join(lines) + "\n"


def compute_builder_status(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Build a structured status dict (``argus.builder_status.v1``).

    Alignment rules (first match wins):

    1. ``missing_declared_target`` — no ``content/next_expansion.json``, unreadable JSON,
       or missing/invalid ``primary_target``.
    2. ``missing_prepared`` — no ``builder_task.json`` pair under ``products/<id>/generated/``
       or ``runs/builder/prepare/<id>/`` (same resolution order as invoke).
    3. ``prepared_stale`` — normalized ``primary_target`` != ``resolved_target`` in prepared task.
    4. ``records_missing`` — either ``runs/builder/invoke/<id>/latest.json`` or
       ``runs/builder/reconcile/<id>/latest.json`` is absent.
    5. ``drift_detected`` — declared matches prepared, both records exist, but either
       invoke ``resolved_target`` or reconcile ``current_target`` disagrees with declared.
    6. ``aligned`` — all of the above checks pass.
    7. ``unknown`` — reserved for unexpected internal inconsistency (should be rare).
    """
    repo_root = repo_root.resolve()
    ne_path = next_expansion_path(repo_root, product_id, products_dir=products_dir)
    ne_raw, ne_err = _safe_read_json(ne_path)
    pt = ne_raw.get("primary_target") if ne_raw else None
    if not isinstance(pt, dict):
        pt = None

    declared_norm = _norm_target(pt) if pt else None
    declared_block: dict[str, Any] = {
        "path": _rel(repo_root, ne_path),
        "present": ne_path.is_file(),
        "parse_error": ne_err,
        "schema": ne_raw.get("schema") if ne_raw else None,
        "as_of_utc": ne_raw.get("as_of_utc") if ne_raw else None,
        "target_id": pt.get("id") if pt else None,
        "target_type": pt.get("target_type") if pt else None,
        "group_id": pt.get("group_id") if pt else None,
    }

    resolved = try_resolve_prepared_artifacts(
        repo_root,
        product_id,
        products_dir=products_dir,
    )
    task_path = resolved.task_json if resolved else None
    task_raw, task_err = _safe_read_json(task_path) if task_path else (None, None)
    if task_path and task_err:
        task_raw = None
    rt = task_raw.get("resolved_target") if task_raw else None
    if not isinstance(rt, dict):
        rt = None
    prepared_norm = _norm_target(rt) if rt else None

    prepared_block: dict[str, Any] = {
        "path": _rel(repo_root, task_path) if task_path else None,
        "source": resolved.source if resolved else None,
        "present": bool(task_path and task_path.is_file() and task_raw is not None),
        "parse_error": task_err if task_path and task_path.is_file() else None,
        "resolved_target_id": rt.get("id") if rt else None,
        "resolved_target_type": rt.get("target_type") if rt else None,
        "group_id": rt.get("group_id") if rt else None,
        "prompt_template_id": task_raw.get("prompt_template_id") if task_raw else None,
        "generated_at_utc": task_raw.get("generated_at_utc") if task_raw else None,
    }

    inv_path = invoke_record_dir(repo_root, product_id) / "latest.json"
    inv_raw, inv_err = _safe_read_json(inv_path)
    if inv_path.is_file() and inv_err:
        inv_raw = None
    inv_rt = inv_raw.get("resolved_target") if inv_raw else None
    if not isinstance(inv_rt, dict):
        inv_rt = None
    invoke_norm = _norm_target(inv_rt) if inv_raw else None

    inv = inv_raw or {}
    gbi = inv.get("git_branch_isolation") if isinstance(inv.get("git_branch_isolation"), dict) else {}
    gbi_status = gbi.get("branch_isolation_status")
    gbi_err = gbi.get("branch_isolation_error")
    gbi_trust_dirty = gbi.get("trust_degraded_dirty_tree")
    branch_isolation_reason: str | None = None
    if gbi_status == "degraded_dirty_tree":
        branch_isolation_reason = "working_tree_dirty_before_builder_branch"
    elif gbi_status == "failed" and gbi_err:
        branch_isolation_reason = str(gbi_err)[:400]
    elif gbi_status and str(gbi_status).startswith("skipped") and gbi_err:
        branch_isolation_reason = str(gbi_err)[:400]

    bc = inv.get("builder_containment") if isinstance(inv.get("builder_containment"), dict) else {}
    ppd = inv.get("project_permission_decision") if isinstance(inv.get("project_permission_decision"), dict) else {}
    gb_inv = inv.get("git_baseline") if isinstance(inv.get("git_baseline"), dict) else {}

    invoke_block: dict[str, Any] = {
        "path": _rel(repo_root, inv_path),
        "present": inv_path.is_file(),
        "parse_error": inv_err if inv_path.is_file() else None,
        "execution_backend": inv_raw.get("execution_backend") if inv_raw else None,
        "mode": inv_raw.get("mode") if inv_raw else None,
        "invocation_status": inv_raw.get("invocation_status") if inv_raw else None,
        "invoked_at_utc": inv_raw.get("invoked_at_utc") if inv_raw else None,
        "resolved_target_id": inv_rt.get("id") if inv_rt else None,
        "resolved_target_type": inv_rt.get("target_type") if inv_rt else None,
        "execution_contract_kind": inv_raw.get("execution_contract_kind") if inv_raw else None,
        "group_id": inv_rt.get("group_id") if inv_rt else None,
        "exit_code": inv_raw.get("exit_code") if inv_raw else None,
        "git_branch_before": gbi.get("git_branch_before"),
        "git_builder_branch": gbi.get("git_builder_branch"),
        "git_branch_created": gbi.get("git_branch_created"),
        "branch_isolation_status": gbi_status,
        "branch_isolation_mode": gbi.get("isolation_mode"),
        "trust_degraded_dirty_tree": gbi_trust_dirty,
        "branch_isolation_error": gbi_err,
        "branch_isolation_reason": branch_isolation_reason,
        "builder_workspace_kind": gb_inv.get("git_workspace_kind"),
        "trust_degraded_workspace_scope": bc.get("trust_degraded_workspace_scope"),
        "containment_requested": bc.get("containment_requested"),
        "containment_applied": bc.get("containment_applied"),
        "containment_fallback_used": bc.get("containment_fallback_used"),
        "containment_reason": bc.get("containment_reason"),
        "trust_degraded_unsandboxed": bc.get("trust_degraded_unsandboxed"),
        "sanitized_env_stripped_count": bc.get("sanitized_env_stripped_count"),
        "no_new_privs_requested": bc.get("no_new_privs_requested"),
        "no_new_privs_applied": bc.get("no_new_privs_applied"),
        "no_new_privs_launcher": bc.get("no_new_privs_launcher"),
        "setpriv_path": bc.get("setpriv_path"),
        "no_new_privs_reason": bc.get("no_new_privs_reason"),
        "trust_degraded_missing_no_new_privs": bc.get("trust_degraded_missing_no_new_privs"),
        "landlock_requested": bc.get("landlock_requested"),
        "landlock_applied": bc.get("landlock_applied"),
        "landlock_reason": bc.get("landlock_reason"),
        "trust_degraded_missing_landlock": bc.get("trust_degraded_missing_landlock"),
        "landlock_skipped_reason": bc.get("landlock_skipped_reason"),
        "filesystem_scope_mode": bc.get("filesystem_scope_mode"),
        "filesystem_scope_reason": bc.get("filesystem_scope_reason"),
        "repo_root_mount_mode": bc.get("repo_root_mount_mode"),
        "product_mount_mode": bc.get("product_mount_mode"),
        "product_rw_path": bc.get("product_rw_path"),
        "network_mode": bc.get("network_mode"),
        "network_applied": bc.get("network_applied"),
        "network_reason": bc.get("network_reason"),
        "trust_degraded_network_open": bc.get("trust_degraded_network_open"),
        "permission_aggregate_decision": ppd.get("aggregate_decision"),
        "permission_execution_proceeds": ppd.get("execution_proceeds"),
        "permission_evaluated_keys": ppd.get("phase1_policy_fields_evaluated"),
    }

    rec_path = reconcile_record_dir(repo_root, product_id) / "latest.json"
    rec_raw, rec_err = _safe_read_json(rec_path)
    if rec_path.is_file() and rec_err:
        rec_raw = None
    cur = rec_raw.get("current_target") if rec_raw else None
    if not isinstance(cur, dict):
        cur = None
    reconcile_norm = _norm_target(cur) if rec_raw else None

    gen = rec_raw.get("generate_next_expansion") if rec_raw else None
    if not isinstance(gen, dict):
        gen = {}
    pn = rec_raw.get("prepare_next") if rec_raw else None
    if not isinstance(pn, dict):
        pn = {}

    sc = rec_raw.get("builder_scope_check") if rec_raw else None
    if not isinstance(sc, dict):
        sc = {}

    path_sc = sc.get("path_scope") if isinstance(sc.get("path_scope"), dict) else {}
    # v1 reconcile records store path_scope fields at top level of builder_scope_check
    if not path_sc and sc.get("schema") == "argus.builder_scope_check.v1":
        path_sc = sc

    eo = rec_raw.get("execution_outcome") if rec_raw else None
    if not isinstance(eo, dict):
        eo = {}

    rec = rec_raw or {}
    gbc = rec.get("git_branch_context") if isinstance(rec.get("git_branch_context"), dict) else {}

    bds = rec_raw.get("builder_diff_summary") if rec_raw else None
    if not isinstance(bds, dict):
        bds = {}
    cf = bds.get("changed_files_argus_relative") if isinstance(bds.get("changed_files_argus_relative"), list) else []
    diff_argus = any(
        isinstance(p, str) and (p == "argus" or p.startswith("argus/")) for p in cf
    )
    preview = [p for p in cf[:8] if isinstance(p, str)]

    brv: dict[str, Any] | None = None
    if rec_raw is not None:
        brv = (
            rec_raw.get("builder_branch_review")
            if isinstance(rec_raw.get("builder_branch_review"), dict)
            else None
        )
        if brv is None:
            brv = compute_builder_branch_review(
                invoke_data=inv_raw,
                scope_check=sc,
                execution_outcome=eo,
                builder_diff_summary=bds,
            )

    bem: dict[str, Any] = {}
    if rec_raw and isinstance(rec_raw.get("builder_escalation_emit"), dict):
        bem = rec_raw["builder_escalation_emit"]

    reconcile_block: dict[str, Any] = {
        "path": _rel(repo_root, rec_path),
        "present": rec_path.is_file(),
        "parse_error": rec_err if rec_path.is_file() else None,
        "target_transition_status": rec_raw.get("target_transition_status") if rec_raw else None,
        "current_target_id": cur.get("id") if cur else None,
        "current_target_type": cur.get("target_type") if cur else None,
        "group_id": cur.get("group_id") if cur else None,
        "generate_next_expansion_status": gen.get("status"),
        "generate_next_expansion_reason": gen.get("reason"),
        "prepare_next_status": pn.get("status"),
        "prepare_next_reason": pn.get("reason"),
        "reconciled_at_utc": rec_raw.get("reconciled_at_utc") if rec_raw else None,
        "builder_scope_check_schema": sc.get("schema"),
        "scope_check_status": sc.get("status"),
        "scope_breach": sc.get("scope_breach"),
        "path_scope_breach": sc.get("path_scope_breach"),
        "semantic_scope_breach": sc.get("semantic_scope_breach"),
        "scope_breach_reasons": sc.get("breach_reasons"),
        "path_scope_argus_core_breach": path_sc.get("argus_core_breach"),
        "path_scope_non_product_root_breach": path_sc.get("non_product_root_breach"),
        "execution_outcome": eo.get("outcome"),
        "execution_outcome_schema": eo.get("schema"),
        "execution_outcome_reasons": eo.get("reasons"),
        "diff_source": bds.get("source"),
        "diff_changed_file_count": bds.get("changed_file_count"),
        "diff_changed_paths_preview": preview,
        "diff_truncated": bds.get("diff_truncated"),
        "diff_fallback_used": bds.get("fallback_used"),
        "diff_argus_paths_modified": diff_argus,
        "diff_limitations": bds.get("limitations"),
        "git_branch_at_reconcile": gbc.get("git_branch_at_reconcile"),
        "git_branch_context_error": gbc.get("read_error"),
        "review_status": (brv or {}).get("review_status"),
        "review_reasons": (brv or {}).get("review_reasons"),
        "review_builder_branch": (brv or {}).get("builder_branch"),
        "review_baseline_commit": (brv or {}).get("baseline_commit"),
        "review_changed_file_count": (brv or {}).get("changed_file_count"),
        "review_trust_degraded_dirty_tree": (brv or {}).get("trust_degraded_dirty_tree"),
        "review_git_workspace_kind": (brv or {}).get("git_workspace_kind"),
        "review_trust_degraded_workspace_scope": (brv or {}).get(
            "trust_degraded_workspace_scope"
        ),
        "escalation_emit_emitted": bem.get("emitted"),
        "escalation_emit_packet_id": bem.get("packet_id"),
        "escalation_emit_path_repo": bem.get("path_repo"),
        "escalation_emit_reason": bem.get("reason"),
        "escalation_triggering_rules": bem.get("triggering_rules"),
    }

    summary: AlignmentSummary
    if not ne_path.is_file() or ne_err or pt is None or declared_norm is None:
        summary = "missing_declared_target"
    elif not prepared_block["present"] or prepared_norm is None:
        summary = "missing_prepared"
    elif not _targets_equal(declared_norm, prepared_norm):
        summary = "prepared_stale"
    elif not inv_path.is_file() or not rec_path.is_file():
        summary = "records_missing"
    elif (inv_path.is_file() and inv_err) or (rec_path.is_file() and rec_err):
        summary = "unknown"
    else:
        # Both loop records exist and parsed; compare snapshots to declared target.
        inv_mismatch = inv_raw is not None and (
            invoke_norm is None or not _targets_equal(declared_norm, invoke_norm)
        )
        rec_mismatch = rec_raw is not None and (
            reconcile_norm is None or not _targets_equal(declared_norm, reconcile_norm)
        )
        if inv_mismatch or rec_mismatch:
            summary = "drift_detected"
        else:
            summary = "aligned"

    notes = _alignment_notes(
        summary,
        declared=declared_norm,
        prepared=prepared_norm,
        invoke_target=invoke_norm,
        reconcile_target=reconcile_norm,
    )

    pd = str(products_dir) if products_dir is not None else None
    last_esc = summarize_latest_builder_escalation(repo_root, product_id)

    payload: dict[str, Any] = {
        "schema": BUILDER_STATUS_SCHEMA,
        "product_id": product_id,
        "products_dir": pd,
        "alignment_summary": summary,
        "last_escalation": last_esc,
        "declared_next_target": declared_block,
        "prepared_task": prepared_block,
        "latest_invoke": invoke_block,
        "latest_reconcile": reconcile_block,
        "notes": notes,
        "disclaimer": (
            "Invoke and reconcile JSON files record what the Builder ran and saw; they do not "
            "assert implementation quality. execution_outcome (content_slot / bug_fix / signal_instrumentation) is evidence-based "
            "and conservative. next_expansion.json is the declaration of intent; "
            "builder_task.json may lag until prepare runs."
        ),
    }
    payload["operator_summary"] = build_builder_operator_summary(payload)
    return payload


def format_builder_status_human(payload: dict[str, Any]) -> str:
    """Compact multi-line text for terminal use."""
    pid = payload.get("product_id") or "?"
    summ = payload.get("alignment_summary") or "unknown"
    le = payload.get("last_escalation") if isinstance(payload.get("last_escalation"), dict) else {}
    lines: list[str] = [f"Product: {pid}", f"Summary: {summ}"]
    osum = payload.get("operator_summary") if isinstance(payload.get("operator_summary"), dict) else {}
    hlines = osum.get("operator_trust_human_lines")
    if isinstance(hlines, list) and hlines:
        lines.append("Trust & next:")
        for hl in hlines[:2]:
            if isinstance(hl, str) and hl.strip():
                lines.append(f"  {hl.strip()}")
        lines.append(
            f"  (trust_posture={osum.get('trust_posture')!s} · "
            f"recommended_next_action={osum.get('recommended_next_action')!s})"
        )
    if le.get("present"):
        sev = le.get("severity") or "?"
        sr = str(le.get("short_reason") or le.get("title") or "").strip()
        lines.append(
            f"Last Builder escalation: {sev} — {sr}" if sr else f"Last Builder escalation: {sev}"
        )
        pr = str(le.get("path_repo") or "").strip()
        if pr:
            lines.append(f"  packet: {pr}")
    else:
        lines.append("Last Builder escalation: none")
    lines.extend(["", "Declared target:"])
    d = payload.get("declared_next_target") or {}
    if not d.get("present"):
        lines.append(f"  (missing or unreadable) path: {d.get('path')}")
        if d.get("parse_error"):
            lines.append(f"  parse_error: {d.get('parse_error')}")
    else:
        lines.append(f"  id: {d.get('target_id')}")
        lines.append(f"  type: {d.get('target_type')}")
        if d.get("group_id"):
            lines.append(f"  group_id: {d.get('group_id')}")
        lines.append(f"  path: {d.get('path')}")
        if d.get("as_of_utc"):
            lines.append(f"  as_of_utc: {d.get('as_of_utc')}")

    lines.extend(["", "Prepared task:"])
    p = payload.get("prepared_task") or {}
    if not p.get("present"):
        lines.append(
            f"  (missing) expected {BUILDER_TASK_FILENAME} under products/<id>/generated/ "
            f"or runs/builder/prepare/<id>/"
        )
        if p.get("path"):
            lines.append(f"  last path checked: {p.get('path')}")
        if p.get("parse_error"):
            lines.append(f"  parse_error: {p.get('parse_error')}")
    else:
        lines.append(f"  id: {p.get('resolved_target_id')}")
        lines.append(f"  template: {p.get('prompt_template_id')}")
        lines.append(f"  source: {p.get('source')}")
        lines.append(f"  path: {p.get('path')}")
        if p.get("generated_at_utc"):
            lines.append(f"  generated_at_utc: {p.get('generated_at_utc')}")

    lines.extend(["", "Latest invoke:"])
    lv = payload.get("latest_invoke") or {}
    if not lv.get("present"):
        lines.append(f"  (missing) path: {lv.get('path')}")
    else:
        if lv.get("execution_backend"):
            lines.append(f"  execution_backend: {lv.get('execution_backend')}")
        lines.append(f"  mode: {lv.get('mode')}")
        lines.append(f"  status: {lv.get('invocation_status')}")
        if lv.get("permission_aggregate_decision") is not None:
            lines.append(
                f"  project_permission: aggregate={lv.get('permission_aggregate_decision')} "
                f"execution_proceeds={lv.get('permission_execution_proceeds')} "
                f"keys={lv.get('permission_evaluated_keys')}"
            )
        lines.append(f"  target id: {lv.get('resolved_target_id')}")
        lines.append(f"  invoked_at_utc: {lv.get('invoked_at_utc')}")
        if lv.get("builder_workspace_kind"):
            lines.append(f"  builder_workspace_kind: {lv.get('builder_workspace_kind')}")
        if lv.get("trust_degraded_workspace_scope"):
            lines.append(
                "  trust: DEGRADED — Argus-root workspace (filesystem scope not product-scoped; "
                "see invoke builder_containment)"
            )
        bis = lv.get("branch_isolation_status")
        if bis:
            extra = ""
            if lv.get("trust_degraded_dirty_tree"):
                extra = " (trust degraded: dirty tree before branch)"
            elif bis == "failed":
                extra = " (isolation failed)"
            lines.append(f"  branch_isolation: {bis}{extra}")
        if lv.get("git_builder_branch"):
            lines.append(f"  builder_branch: {lv.get('git_builder_branch')}")
        if lv.get("execution_backend") == "agent":
            cap = lv.get("containment_applied")
            lines.append(
                f"  containment: requested={lv.get('containment_requested')} applied={cap}"
            )
            if lv.get("filesystem_scope_mode"):
                lines.append(
                    f"  filesystem_scope: mode={lv.get('filesystem_scope_mode')} "
                    f"repo_root_mount={lv.get('repo_root_mount_mode')}"
                )
            if lv.get("network_mode"):
                nr = lv.get("network_reason")
                extra = f" reason={nr}" if nr else ""
                lines.append(
                    f"  network: mode={lv.get('network_mode')} applied={lv.get('network_applied')}{extra}"
                )
                if lv.get("trust_degraded_network_open"):
                    lines.append("  network: explicit allow_all (trust_degraded_network_open)")
                if lv.get("network_mode") == "disabled" and lv.get("network_applied") is True:
                    lines.append("  network: offline (--unshare-net) when sandboxed")
            if lv.get("no_new_privs_requested") is not None or lv.get("no_new_privs_applied") is not None:
                lines.append(
                    f"  no_new_privs: requested={lv.get('no_new_privs_requested')} "
                    f"applied={lv.get('no_new_privs_applied')} "
                    f"({lv.get('no_new_privs_reason') or '—'})"
                )
            if lv.get("trust_degraded_unsandboxed") or lv.get("containment_fallback_used"):
                lines.append(
                    "  containment: TRUST DEGRADED (unsandboxed or sandbox fallback — see invoke JSON)"
                )
            if lv.get("trust_degraded_missing_no_new_privs"):
                lines.append(
                    "  no_new_privs: TRUST DEGRADED (setpriv unavailable — see invoke JSON)"
                )
        lines.append(f"  path: {lv.get('path')}")

    lines.extend(["", "Latest reconcile:"])
    lr = payload.get("latest_reconcile") or {}
    if not lr.get("present"):
        lines.append(f"  (missing) path: {lr.get('path')}")
    else:
        if lr.get("review_status"):
            for raw_ln in format_builder_branch_review_human(
                lr, product_id=pid
            ).splitlines():
                if raw_ln.strip():
                    lines.append(f"  {raw_ln}")
        if lr.get("escalation_emit_emitted") and lr.get("escalation_emit_packet_id"):
            lines.append(
                f"  escalation_packet: {lr.get('escalation_emit_packet_id')} "
                f"({lr.get('escalation_emit_path_repo')})"
            )
        elif lr.get("escalation_emit_reason") == "dedupe_recent_packet":
            lines.append("  escalation: dedupe skip (same triggers within window — see reconcile JSON)")
        lines.append(f"  transition: {lr.get('target_transition_status')}")
        lines.append(f"  current target id: {lr.get('current_target_id')}")
        lines.append(f"  generate_next_expansion: {lr.get('generate_next_expansion_status')} ({lr.get('generate_next_expansion_reason')})")
        lines.append(f"  prepare_next: {lr.get('prepare_next_status')} ({lr.get('prepare_next_reason')})")
        if lr.get("builder_scope_check_schema") == "argus.builder_scope_check.v2":
            lines.append(
                f"  scope_check: {lr.get('scope_check_status')} "
                f"(breach={lr.get('scope_breach')}, path={lr.get('path_scope_breach')}, semantic={lr.get('semantic_scope_breach')})"
            )
        elif lr.get("scope_breach") is not None:
            lines.append(f"  scope_check: breach={lr.get('scope_breach')} (path-only v1 record)")
        if lr.get("path_scope_argus_core_breach"):
            lines.append(
                "  path_scope: Argus core (argus/) modified — forbidden for product-scoped Builder"
            )
        if lr.get("path_scope_non_product_root_breach") and lr.get("scope_breach"):
            lines.append(
                "  path_scope: changes outside product tree (non-argus) — see scope_breach_reasons"
            )
        if lr.get("execution_outcome") is not None:
            lines.append(f"  execution_outcome: {lr.get('execution_outcome')}")
        if lr.get("diff_changed_file_count") is not None:
            lines.append(
                f"  diff: {lr.get('diff_changed_file_count')} file(s) changed "
                f"(source={lr.get('diff_source')})"
            )
            prev = lr.get("diff_changed_paths_preview") or []
            if prev:
                lines.append(f"  diff paths (preview): {', '.join(prev)}")
            if lr.get("diff_argus_paths_modified"):
                lines.append("  diff: argus/ modified (see scope breach if product-scoped)")
            if lr.get("diff_truncated"):
                lines.append("  diff: patch truncated in snapshot — see reconcile JSON")
            if lr.get("diff_fallback_used"):
                lines.append("  diff: fallback used (no invoke baseline) — weaker trust")
        if lr.get("git_branch_at_reconcile"):
            lines.append(f"  git_branch_at_reconcile: {lr.get('git_branch_at_reconcile')}")
        if lr.get("git_branch_context_error"):
            lines.append(f"  git_branch_context_error: {lr.get('git_branch_context_error')}")
        lines.append(f"  reconciled_at_utc: {lr.get('reconciled_at_utc')}")
        lines.append(f"  path: {lr.get('path')}")

    for n in payload.get("notes") or []:
        lines.extend(["", f"Note: {n}"])
    return "\n".join(lines) + "\n"
