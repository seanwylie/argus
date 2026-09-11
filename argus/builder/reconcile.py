"""
Builder v0.6–v0.9: post-execution reconciliation — refresh signals/findings, compare targets, record observations.

Optional ``--generate-next-expansion`` rewrites ``content/next_expansion.json`` via the content-catalog generator
before comparing prior vs current targets.

Optional ``--prepare-next`` regenerates the Builder contract via :func:`write_prepare_artifacts` only when
the declared target advanced (``target_transition_status == "changed"``). That path delegates to
:mod:`argus.builder.next_expansion_prepare` (markdown + task JSON); outcome classification is not
implemented there.

Adds ``execution_outcome`` via :mod:`argus.builder.contract_registry` (``resolve_reconcile_outcome_kind``
→ ``derive_execution_outcome_for_kind``), using scope + invoke + per-kind evidence (e.g. ``content_slot``
filesystem signals; ``bug_fix`` diff summary + invoke; ``signal_instrumentation`` optional on-disk
postconditions and touch-path hints). Does not judge content quality. ``prepare-next`` is blocked when
scope breaches or when outcome is not ``completed``/``unknown`` (see
:mod:`argus.builder.content_slot_outcome`, :mod:`argus.builder.bug_fix_outcome`,
:mod:`argus.builder.signal_instrumentation_outcome`).
"""

from __future__ import annotations

import contextlib
import io
import json
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from argus.builder.branch_review import compute_builder_branch_review
from argus.builder.content_slot_outcome import outcome_blocks_prepare_next
from argus.builder.contract_registry import (
    OutcomeDeriveContext,
    derive_execution_outcome_for_kind,
    get_kind_spec,
    resolve_reconcile_outcome_kind,
)
from argus.builder.escalation_bridge import (
    BUILDER_ESCALATION_EMIT_SCHEMA,
    emit_builder_escalation_if_needed,
)
from argus.builder.execution_contract import build_builder_scope_check
from argus.builder.git_branch_isolation import reconcile_branch_snapshot
from argus.builder.git_diff_capture import (
    build_reconcile_diff_summary,
    ensure_product_git_workspace,
)
from argus.builder.invoke import (
    BUILDER_TASK_FILENAME,
    ResolvedArtifacts,
    invoke_record_dir,
    try_resolve_prepared_artifacts,
)
from argus.builder.next_expansion_generate import (
    NextExpansionGenerateError,
    generate_and_write_next_expansion,
)
from argus.builder.next_expansion_prepare import (
    NextExpansionPrepareError,
    next_expansion_path,
    write_prepare_artifacts,
)
from argus.cli.findings_cmd import cmd_findings_generate
from argus.cli.signals_cmd import cmd_signals_collect
from argus.core.serialize import dumps_json

BUILDER_RECONCILE_RECORD_SCHEMA = "argus.builder_reconcile_record.v1"


def reconcile_record_dir(repo_root: Path, product_id: str) -> Path:
    return (repo_root / "runs" / "builder" / "reconcile" / product_id).resolve()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


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


def _compute_target_transition(
    prior: dict[str, Any] | None,
    current: dict[str, Any] | None,
    *,
    current_path_exists: bool,
) -> str:
    if not current_path_exists:
        return "unavailable"
    if current is None:
        return "unavailable"
    if prior is None:
        return "unknown"
    if _targets_equal(prior, current):
        return "unchanged"
    return "changed"


def _run_subcommand(fn: Callable[[], int], *, quiet: bool) -> int:
    if not quiet:
        return int(fn())
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return int(fn())


def _prepare_next_result(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    prepare_next: bool,
    prepare_under: str,
    transition: str,
    current_target: dict[str, Any] | None,
    scope_check: dict[str, Any] | None = None,
    skip_prepare_on_scope_breach: bool = True,
    execution_outcome: dict[str, Any] | None = None,
    skip_prepare_on_bad_execution_outcome: bool = True,
) -> dict[str, Any]:
    """
    Optionally regenerate Builder prompt/task after reconcile when the declared target advanced.

    Only runs ``write_prepare_artifacts`` when ``--prepare-next`` is set and
    ``target_transition_status == "changed"`` (opt-in; does not invoke Cursor).
    """
    tid = current_target.get("id") if current_target else None
    ttype = current_target.get("target_type") if current_target else None
    out: dict[str, Any] = {
        "attempted": False,
        "status": "skipped",
        "reason": None,
        "target_id": tid,
        "target_type": ttype,
        "artifacts": None,
        "error": None,
    }
    if not prepare_next:
        out["reason"] = "flag_not_set"
        return out

    if skip_prepare_on_scope_breach and scope_check and scope_check.get("scope_breach"):
        # v2: path and/or semantic breach; v1: only path breach when git scope check ran (status ok).
        if scope_check.get("schema") == "argus.builder_scope_check.v2" or (
            scope_check.get("status") == "ok"
        ):
            out["reason"] = "scope_breach_blocks_prepare_next"
            out["detail"] = "; ".join(scope_check.get("breach_reasons") or []) or "scope_breach"
            return out

    if (
        skip_prepare_on_bad_execution_outcome
        and execution_outcome
        and outcome_blocks_prepare_next(execution_outcome)
    ):
        out["reason"] = "execution_outcome_blocks_prepare_next"
        out["detail"] = "; ".join(execution_outcome.get("reasons") or []) or "bad_execution_outcome"
        out["execution_outcome"] = execution_outcome.get("outcome")
        return out

    if current_target is None or transition == "unavailable":
        out["reason"] = "no_current_target"
        return out

    if transition == "unchanged":
        out["reason"] = "target_unchanged"
        return out

    if transition == "unknown":
        out["reason"] = "target_transition_unknown"
        return out

    if transition != "changed":
        out["reason"] = f"unexpected_transition_{transition}"
        return out

    out["attempted"] = True
    try:
        res = write_prepare_artifacts(
            repo_root,
            product_id,
            under=prepare_under,
            products_dir=products_dir,
        )
        out["status"] = "ok"
        out["reason"] = "target_changed_regenerated_contract"
        out["artifacts"] = {
            "prompt_path": str(res.paths.prompt_md),
            "task_path": str(res.paths.task_json),
        }
    except NextExpansionPrepareError as e:
        out["status"] = "failed"
        out["error"] = str(e)
        out["reason"] = "prepare_failed"

    return out


def _generate_next_expansion_result(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    generate_next_expansion: bool,
) -> dict[str, Any]:
    """
    Rewrite ``next_expansion.json`` using :func:`generate_and_write_next_expansion`.

    Eligibility is data-driven: any product shipping a supported content catalog works, and a
    product without one surfaces as ``generation_failed`` with the loader's message.
    """
    out: dict[str, Any] = {
        "attempted": False,
        "status": "skipped",
        "reason": "flag_not_set",
        "artifact_path": None,
        "target_id": None,
        "target_type": None,
        "error": None,
    }
    if not generate_next_expansion:
        return out

    ne_guard = next_expansion_path(repo_root, product_id, products_dir=products_dir)
    ne_before = _read_json(ne_guard) if ne_guard.is_file() else None
    pt_guard = ne_before.get("primary_target") if isinstance(ne_before, dict) else None
    tt_guard = str(pt_guard.get("target_type") or "").strip() if isinstance(pt_guard, dict) else ""
    gspec = get_kind_spec(tt_guard)
    if gspec and gspec.skip_generate_next_expansion_heuristic:
        out["attempted"] = True
        out["status"] = "skipped"
        out["reason"] = f"generate_next_expansion_skipped_for_{tt_guard}_manual_target"
        out["error"] = None
        out["artifact_path"] = str(ne_guard)
        return out

    out["attempted"] = True
    try:
        payload, path = generate_and_write_next_expansion(
            repo_root, product_id, products_dir=products_dir
        )
        pt = payload.get("primary_target")
        if not isinstance(pt, dict) or not pt.get("id"):
            out["status"] = "failed"
            out["reason"] = "no_target_produced"
            out["error"] = "generator returned no primary_target.id"
            return out
        out["status"] = "ok"
        out["reason"] = "generation_succeeded"
        out["artifact_path"] = str(path)
        out["target_id"] = pt.get("id")
        out["target_type"] = pt.get("target_type")
    except NextExpansionGenerateError as e:
        out["status"] = "failed"
        out["reason"] = "generation_failed"
        out["error"] = str(e)

    return out


def _step_result(
    *,
    attempted: bool,
    fn: Callable[[], int],
    quiet: bool,
) -> dict[str, Any]:
    if not attempted:
        return {"attempted": False, "status": "skipped", "exit_code": None, "error": None}
    try:
        code = _run_subcommand(fn, quiet=quiet)
        return {
            "attempted": True,
            "status": "ok" if code == 0 else "failed",
            "exit_code": code,
            "error": None if code == 0 else f"exit_code={code}",
        }
    except Exception as e:
        return {
            "attempted": True,
            "status": "failed",
            "exit_code": None,
            "error": str(e)[:4000],
        }


def run_builder_reconcile(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
    prompt_path: Path | None = None,
    task_path: Path | None = None,
    invoke_record_path: Path | None = None,
    skip_signals: bool = False,
    skip_findings: bool = False,
    no_record: bool = False,
    emit_escalation: bool = True,
    quiet: bool = False,
    prepare_next: bool = False,
    prepare_under: str = "product",
    generate_next_expansion: bool = False,
) -> tuple[dict[str, Any], int]:
    """
    Refresh signals/findings (unless skipped), optionally regenerate ``next_expansion.json``,
    compare prior vs current ``primary_target``, optionally prepare contract, write record.

    Returns ``(record, exit_code)`` where ``exit_code`` is non-zero if any attempted step failed.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    resolved: Optional[ResolvedArtifacts] = None
    task_file: Path | None = None
    if prompt_path is not None and task_path is not None:
        resolved = try_resolve_prepared_artifacts(
            repo_root,
            product_id,
            products_dir=products_dir,
            prompt_path=prompt_path,
            task_path=task_path,
        )
        if resolved is not None:
            task_file = resolved.task_json
    elif task_path is not None:
        task_file = task_path.expanduser().resolve()
    else:
        resolved = try_resolve_prepared_artifacts(
            repo_root,
            product_id,
            products_dir=products_dir,
        )
        if resolved is not None:
            task_file = resolved.task_json

    task_data = _read_json(task_file) if task_file and task_file.is_file() else None
    prior_from_task = _norm_target(
        task_data.get("resolved_target") if task_data else None
    )

    inv_path: Path
    if invoke_record_path is None:
        inv_path = invoke_record_dir(repo_root, product_id) / "latest.json"
    else:
        inv_path = invoke_record_path.expanduser().resolve()

    invoke_data = _read_json(inv_path) if inv_path.is_file() else None

    ensure_product_git_workspace(repo_root, product_id, products_dir=products_dir)
    builder_diff_summary = build_reconcile_diff_summary(
        repo_root,
        product_id,
        products_dir=products_dir,
        invoke_data=invoke_data,
    )
    diff_paths = builder_diff_summary.get("changed_files_argus_relative")
    scope_check = build_builder_scope_check(
        repo_root,
        product_id=product_id,
        products_dir=products_dir,
        task_data=task_data,
        changed_paths_repo_relative=diff_paths
        if builder_diff_summary.get("source")
        else None,
    )
    prior_from_invoke = _norm_target(
        invoke_data.get("resolved_target") if invoke_data else None
    )

    prior_resolved = prior_from_task or prior_from_invoke

    ne_path = next_expansion_path(repo_root, product_id, products_dir=products_dir)

    observations: list[str] = []
    if resolved is None and task_path is None and prompt_path is None:
        observations.append(
            f"No {BUILDER_TASK_FILENAME} pair found under products/{product_id}/generated/ "
            f"or runs/builder/prepare/{product_id}/ — prior target taken only from invoke record if present."
        )
    if invoke_data is None:
        observations.append(
            f"No invoke record at {inv_path} — run `argus builder invoke {product_id}` to record a handoff."
        )

    observations.append(
        f"builder_diff_summary: {builder_diff_summary.get('changed_file_count', 0)} file(s) changed "
        f"(source={builder_diff_summary.get('source')})"
    )

    signals_ns = Namespace(
        product_id=product_id,
        json=False,
        no_save=False,
        merge_adapter_layer=False,
        products_dir=products_dir,
    )
    findings_ns = Namespace(
        product_id=product_id,
        json=False,
        no_save=False,
        fresh_signals=False,
        products_dir=products_dir,
    )

    def run_signals() -> int:
        return cmd_signals_collect(repo_root, signals_ns)

    def run_findings() -> int:
        return cmd_findings_generate(repo_root, findings_ns)

    signals_result = _step_result(
        attempted=not skip_signals,
        fn=run_signals,
        quiet=quiet,
    )
    findings_result = _step_result(
        attempted=not skip_findings,
        fn=run_findings,
        quiet=quiet,
    )

    gen_result = _generate_next_expansion_result(
        repo_root=repo_root,
        product_id=product_id,
        products_dir=products_dir,
        generate_next_expansion=generate_next_expansion,
    )

    current_path_exists = ne_path.is_file()
    ne_raw = _read_json(ne_path) if current_path_exists else None
    pt = ne_raw.get("primary_target") if ne_raw else None
    current_target = _norm_target(pt) if isinstance(pt, dict) else None

    transition = _compute_target_transition(
        prior_resolved,
        current_target,
        current_path_exists=current_path_exists,
    )

    ec_task = task_data.get("execution_contract") if isinstance(task_data, dict) else None
    contract_kind = str((ec_task or {}).get("contract_kind") or "").strip()
    rt_type = str(
        (task_data.get("resolved_target") or {}).get("target_type") or ""
    ).strip() if isinstance(task_data, dict) else ""
    outcome_kind = resolve_reconcile_outcome_kind(contract_kind, rt_type)
    outcome_ctx = OutcomeDeriveContext(
        repo_root=repo_root,
        product_id=product_id,
        products_dir=products_dir,
        scope_check=scope_check,
        invoke_data=invoke_data,
        task_data=task_data,
        builder_diff_summary=builder_diff_summary,
        prior_resolved_target=prior_resolved,
    )
    execution_outcome = derive_execution_outcome_for_kind(outcome_kind, outcome_ctx)

    if scope_check.get("scope_breach"):
        if scope_check.get("schema") == "argus.builder_scope_check.v2":
            ps = scope_check.get("path_scope") if isinstance(scope_check.get("path_scope"), dict) else {}
            if ps.get("argus_core_breach"):
                observations.append(
                    "builder_scope_check: Argus core (argus/) was modified — product-scoped Builder "
                    "must not change Argus; treat Argus changes as a separate explicit task."
                )
            parts: list[str] = []
            if scope_check.get("path_scope_breach"):
                parts.append("path scope (git diff vs contract allow-list)")
            if scope_check.get("semantic_scope_breach"):
                parts.append("semantic scope (next_expansion primary_target vs builder_task resolved_target)")
            msg = (
                "builder_scope_check: breach — "
                + (", ".join(parts) if parts else "see breach_reasons")
                + "."
            )
            observations.append(msg)
        else:
            observations.append(
                "builder_scope_check: scope breach detected — review unexpected paths and paths outside the product tree."
            )

    eo = execution_outcome.get("outcome")
    if eo:
        observations.append(
            f"execution_outcome: {eo} — "
            + ("; ".join(execution_outcome.get("reasons") or []) or "see execution_outcome field")
        )

    record: dict[str, Any] = {
        "schema": BUILDER_RECONCILE_RECORD_SCHEMA,
        "product_id": product_id,
        "reconciled_at_utc": now,
        "source_task_path": str(task_file) if task_file else None,
        "source_invoke_record_path": str(inv_path) if inv_path.is_file() else None,
        "prior_resolved_target": prior_resolved,
        "signals_collect": signals_result,
        "findings_generate": findings_result,
        "generate_next_expansion": gen_result,
        "current_next_expansion_path": str(ne_path) if current_path_exists else None,
        "current_target": current_target,
        "target_transition_status": transition,
        "flags": {
            "skip_signals": skip_signals,
            "skip_findings": skip_findings,
            "quiet_refresh_output": quiet,
            "generate_next_expansion": generate_next_expansion,
            "prepare_next": prepare_next,
            "prepare_under": prepare_under if prepare_next else None,
        },
        "observations": observations,
        "disclaimer": (
            "This record does not assert Builder task success or correct implementation; "
            "it only reports refresh outcomes, on-disk target snapshots, builder_diff_summary "
            "(bounded git evidence), and execution_outcome (content_slot, bug_fix, or signal_instrumentation). "
            "Truncated diffs are not full truth; dirty pre-invoke trees reduce trust."
        ),
        "builder_scope_check": scope_check,
        "builder_diff_summary": builder_diff_summary,
        "git_branch_context": reconcile_branch_snapshot(
            repo_root,
            product_id,
            products_dir=products_dir,
            invoke_data=invoke_data,
        ),
        "execution_outcome": execution_outcome,
        "builder_branch_review": compute_builder_branch_review(
            invoke_data=invoke_data,
            scope_check=scope_check,
            execution_outcome=execution_outcome,
            builder_diff_summary=builder_diff_summary,
        ),
    }

    prepare_next_result = _prepare_next_result(
        repo_root=repo_root,
        product_id=product_id,
        products_dir=products_dir,
        prepare_next=prepare_next,
        prepare_under=prepare_under,
        transition=transition,
        current_target=current_target,
        scope_check=scope_check,
        execution_outcome=execution_outcome,
    )
    record["prepare_next"] = prepare_next_result

    if no_record or not emit_escalation:
        record["builder_escalation_emit"] = {
            "schema": BUILDER_ESCALATION_EMIT_SCHEMA,
            "emitted": False,
            "reason": "no_reconcile_record" if no_record else "flag_no_escalation",
            "triggering_rules": [],
        }
    else:
        record["builder_escalation_emit"] = emit_builder_escalation_if_needed(
            repo_root,
            product_id,
            record,
            invoke_data=invoke_data,
        )

    exit_code = 0
    if signals_result.get("attempted") and signals_result.get("status") == "failed":
        exit_code = 1
    if findings_result.get("attempted") and findings_result.get("status") == "failed":
        exit_code = 1
    if prepare_next_result.get("attempted") and prepare_next_result.get("status") == "failed":
        exit_code = 1
    if gen_result.get("attempted") and gen_result.get("status") == "failed":
        exit_code = 1

    if not no_record:
        out_dir = reconcile_record_dir(repo_root, product_id)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "latest.json").write_text(dumps_json(record), encoding="utf-8")

    return record, exit_code
