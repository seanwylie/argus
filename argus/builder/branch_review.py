"""
Local merge-readiness / review classification for Builder (conservative, no remote or auto-merge).

Uses invoke + reconcile signals only; persisted on reconcile records as ``builder_branch_review``.
"""

from __future__ import annotations

from typing import Any

BUILDER_BRANCH_REVIEW_SCHEMA = "argus.builder.branch_review.v1"

REVIEW_MERGE_CANDIDATE = "merge_candidate"
REVIEW_REVIEW_REQUIRED = "review_required"
REVIEW_BLOCKED = "blocked"
REVIEW_UNSAFE = "unsafe"


def compute_builder_branch_review(
    *,
    invoke_data: dict[str, Any] | None,
    scope_check: dict[str, Any] | None,
    execution_outcome: dict[str, Any] | None,
    builder_diff_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Classify whether the latest Builder increment looks safe to keep / manually merge.

    **Ordering:** ``unsafe`` → ``blocked`` → ``merge_candidate`` → ``review_required`` (default).

    This does **not** run git merge or push; it is an evidence-based label for operators.
    """
    reasons: list[str] = []

    inv = invoke_data if isinstance(invoke_data, dict) else None
    sco = scope_check if isinstance(scope_check, dict) else {}
    eo_obj = execution_outcome if isinstance(execution_outcome, dict) else {}
    bds = builder_diff_summary if isinstance(builder_diff_summary, dict) else {}

    gbi = inv.get("git_branch_isolation") if inv else None
    gbi = gbi if isinstance(gbi, dict) else {}
    gb = inv.get("git_baseline") if inv else None
    gb = gb if isinstance(gb, dict) else {}
    bc_inv = inv.get("builder_containment") if inv else None
    bc_inv = bc_inv if isinstance(bc_inv, dict) else {}

    bis = gbi.get("branch_isolation_status")
    trust_dirty = bool(gbi.get("trust_degraded_dirty_tree"))
    builder_branch = gbi.get("git_builder_branch")
    baseline_commit = gb.get("baseline_commit")
    changed_file_count = bds.get("changed_file_count")
    diff_fallback = bool(bds.get("fallback_used"))

    outcome = eo_obj.get("outcome")
    scope_breach = bool(sco.get("scope_breach"))
    path_sc = sco.get("path_scope") if isinstance(sco.get("path_scope"), dict) else {}
    if not path_sc and sco.get("schema") == "argus.builder_scope_check.v1":
        path_sc = sco

    argus_core = bool(path_sc.get("argus_core_breach"))
    non_product = bool(path_sc.get("non_product_root_breach"))
    semantic_breach = bool(sco.get("semantic_scope_breach"))

    invocation_status = inv.get("invocation_status") if inv else None
    mode = inv.get("mode") if inv else None

    def _finish(status: str, rs: list[str]) -> dict[str, Any]:
        return {
            "schema": BUILDER_BRANCH_REVIEW_SCHEMA,
            "review_status": status,
            "review_reasons": rs[:24],
            "builder_branch": builder_branch,
            "baseline_commit": baseline_commit,
            "changed_file_count": changed_file_count,
            "trust_degraded_dirty_tree": trust_dirty,
            "git_workspace_kind": gb.get("git_workspace_kind"),
            "trust_degraded_workspace_scope": bool(bc_inv.get("trust_degraded_workspace_scope")),
        }

    # --- unsafe: any hard scope violation (merge would violate product contract) ---
    unsafe_signal = (
        scope_breach
        or outcome == "breached"
        or argus_core
        or non_product
        or semantic_breach
    )
    if unsafe_signal:
        if scope_breach:
            br = sco.get("breach_reasons")
            if isinstance(br, list) and br:
                reasons.extend(str(x) for x in br[:8])
            else:
                reasons.append("builder_scope_check.scope_breach")
        if argus_core:
            reasons.append("path_scope:argus_core_breach")
        if semantic_breach:
            reasons.append("semantic_scope_breach")
        if non_product:
            reasons.append("path_scope:non_product_root_breach")
        if outcome == "breached":
            reasons.append("execution_outcome:breached")
        return _finish(REVIEW_UNSAFE, reasons or ["unsafe_merge_not_allowed"])

    # --- blocked: no trustworthy merge path ---
    if inv is None:
        return _finish(
            REVIEW_BLOCKED,
            ["missing_invoke_record"],
        )

    if invocation_status == "failed":
        reasons.append("invocation_status:failed")
        return _finish(REVIEW_BLOCKED, reasons)

    if bis == "failed":
        reasons.append("branch_isolation:failed")
        if gbi.get("branch_isolation_error"):
            reasons.append(str(gbi.get("branch_isolation_error"))[:200])
        return _finish(REVIEW_BLOCKED, reasons)

    if mode != "execute":
        reasons.append("invoke_mode_not_execute")
        return _finish(REVIEW_BLOCKED, reasons)

    if isinstance(bis, str) and bis.startswith("skipped"):
        # Expected for Argus-root git: no per-product isolation branch — not merge_candidate,
        # but review_required (degraded trust), not blocked.
        if bis != "skipped_not_nested_product_repo":
            reasons.append(f"branch_isolation:{bis}")
            return _finish(REVIEW_BLOCKED, reasons)

    if outcome in ("blocked", "partial"):
        reasons.append(f"execution_outcome:{outcome}")
        return _finish(REVIEW_BLOCKED, reasons)

    # --- merge_candidate: strict ---
    if (
        bis == "ok"
        and not trust_dirty
        and outcome == "completed"
        and invocation_status == "ok"
        and not diff_fallback
    ):
        bc = inv.get("builder_containment") if isinstance(inv.get("builder_containment"), dict) else {}
        eb = (inv.get("execution_backend") or "").strip().lower()
        mode = inv.get("mode")
        gwk = gb.get("git_workspace_kind")
        worktree_branch_ok = gwk == "argus_root_worktree"
        if eb == "agent" and mode == "execute":
            if gwk == "argus_root" or (
                bc.get("trust_degraded_workspace_scope") and not worktree_branch_ok
            ):
                return _finish(
                    REVIEW_REVIEW_REQUIRED,
                    ["builder_workspace:argus_root_not_isolated"],
                )
            if bc.get("trust_degraded_unsandboxed") or bc.get("containment_fallback_used"):
                return _finish(
                    REVIEW_REVIEW_REQUIRED,
                    ["builder_containment:unsandboxed_or_sandbox_fallback"],
                )
            if bc.get("filesystem_scope_mode") == "legacy_repo_rw" and not worktree_branch_ok:
                return _finish(
                    REVIEW_REVIEW_REQUIRED,
                    ["builder_containment:legacy_repo_rw_not_product_scoped"],
                )
            if worktree_branch_ok:
                fs_m = bc.get("filesystem_scope_mode")
                if fs_m != "argus_root_worktree_scoped":
                    detail = (
                        "builder_containment:argus_root_worktree_legacy_repo_rw_fallback"
                        if fs_m == "legacy_repo_rw"
                        else "builder_containment:argus_root_worktree_unexpected_filesystem_scope"
                    )
                    return _finish(REVIEW_REVIEW_REQUIRED, [detail])
            if bc.get("containment_applied") != "bwrap":
                return _finish(
                    REVIEW_REVIEW_REQUIRED,
                    ["builder_containment:merge_candidate_requires_bubblewrap_for_agent"],
                )
            if bc.get("no_new_privs_requested") and not bc.get("no_new_privs_applied"):
                return _finish(
                    REVIEW_REVIEW_REQUIRED,
                    ["builder_containment:no_new_privs_not_applied"],
                )
            # Landlock defense-in-depth: same merge gate for both strong FS modes (product_scoped,
            # argus_root_worktree_scoped). Skipped scopes (e.g. legacy_repo_rw) do not request Landlock.
            fs_mode = bc.get("filesystem_scope_mode")
            if fs_mode in ("product_scoped", "argus_root_worktree_scoped"):
                if bc.get("landlock_requested") and bc.get("landlock_applied") is not True:
                    return _finish(
                        REVIEW_REVIEW_REQUIRED,
                        ["builder_containment:landlock_requested_but_not_applied"],
                    )
        return _finish(REVIEW_MERGE_CANDIDATE, ["signals_ok_for_manual_merge_review"])

    # --- review_required: otherwise safe-ish but not merge_candidate ---
    if trust_dirty or bis == "degraded_dirty_tree":
        reasons.append("trust_degraded_dirty_tree_before_branch")
    if outcome == "unknown":
        reasons.append("execution_outcome:unknown")
    if diff_fallback:
        reasons.append("builder_diff_summary:fallback_used_weaker_baseline")
    if gb.get("git_workspace_kind") == "argus_root" or (
        bc_inv.get("trust_degraded_workspace_scope")
        and gb.get("git_workspace_kind") != "argus_root_worktree"
    ):
        reasons.append("builder_workspace:argus_root_not_isolated")
    if isinstance(bis, str) and bis == "skipped_not_nested_product_repo":
        reasons.append("branch_isolation:skipped_not_nested_product_repo")
    if not reasons:
        reasons.append("conservative_review_not_merge_candidate")
    return _finish(REVIEW_REVIEW_REQUIRED, reasons)


def format_builder_branch_review_human(
    reconcile_block: dict[str, Any],
    *,
    product_id: str | None = None,
) -> str:
    """Readable lines for ``review_status`` + reasons (expects status ``latest_reconcile`` fields)."""
    rs = reconcile_block.get("review_status") or "unknown"
    reasons = reconcile_block.get("review_reasons") or []
    lines = [
        f"Merge readiness: {rs}",
        f"  builder_branch: {reconcile_block.get('review_builder_branch')}",
        f"  baseline_commit: {reconcile_block.get('review_baseline_commit')}",
        f"  changed_file_count: {reconcile_block.get('review_changed_file_count')}",
        f"  trust_degraded_dirty_tree: {reconcile_block.get('review_trust_degraded_dirty_tree')}",
        f"  git_workspace_kind: {reconcile_block.get('review_git_workspace_kind')}",
        f"  trust_degraded_workspace_scope: {reconcile_block.get('review_trust_degraded_workspace_scope')}",
    ]
    for r in reasons[:12]:
        lines.append(f"  — {r}")
    if (
        rs == REVIEW_MERGE_CANDIDATE
        and reconcile_block.get("review_builder_branch")
        and product_id
    ):
        lines.append(
            f"  suggested_next: argus builder merge {product_id} --into main "
            "(or another integration branch; see docs: Builder manual merge)"
        )
    return "\n".join(lines) + "\n"
