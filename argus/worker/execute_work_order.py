"""
Worker-side execution of a single approved work order (v1: dry skeleton + audit trail).

Argus core issues work orders; this module consumes **exactly one** order and records an outcome.
The worker does not choose or re-prioritize work — selection is explicit (id or latest for product+request).
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from argus.core.serialize import dumps_json, to_jsonable
from argus.products.apply_signal_instrumentation import (
    apply_signal_instrumentation,
    write_signal_instrumentation_apply_artifacts,
)
from argus.worker.work_orders import (
    find_work_order,
    latest_work_order_path_for,
    load_latest_work_order_for,
    sanitize_request_type_for_filename,
    work_orders_dir,
)

WORKER_EXECUTION_OUTCOME_SCHEMA: Final = "argus.worker_execution_outcome.v1"

# Mirrors :data:`argus.products.instrumentation_work_orders.REQUEST_TYPE_SIGNAL_INSTRUMENTATION` (avoid import cycles).
REQUEST_TYPE_SIGNAL_INSTRUMENTATION: Final = "signal_instrumentation"

# v1: only steward-approved orders run unless explicitly overridden (CLI/tests).
_DEFAULT_EXECUTABLE: Final = frozenset({"approved"})


def executions_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "worker" / "executions"


def executions_latest_dir(repo_root: Path) -> Path:
    return executions_dir(repo_root) / "latest"


def _iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def new_execution_id(*, started_at_utc: str, work_order_id: str) -> str:
    """Deterministic id from start time + work order id (stable for tests)."""
    woid = str(work_order_id).strip() or "unknown"
    base = f"{started_at_utc}|{woid}".encode("utf-8")
    digest = hashlib.sha256(base).hexdigest()[:12]
    m = re.match(
        r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})",
        str(started_at_utc).strip(),
    )
    if m:
        ts = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}{m.group(5)}{m.group(6)}"
    else:
        ts = "unknown"
    return f"ex_{ts}_{digest}"


def _repo_rel(repo_root: Path, path: Path) -> str:
    try:
        rel = path.resolve().relative_to(Path(repo_root).resolve())
    except ValueError:
        return str(path)
    return rel.as_posix()


def _executable_statuses(*, allow_pending_approval: bool) -> frozenset[str]:
    s = set(_DEFAULT_EXECUTABLE)
    if allow_pending_approval:
        s.add("pending_approval")
    return frozenset(s)


def _blocked_reason_for_status(st: str, *, allowed: frozenset[str]) -> str:
    s = str(st).strip()
    if s == "rejected":
        return (
            "Effective work order status is rejected (steward decision); execution is not allowed. "
            "Use reopen-work-order if this order should return to review."
        )
    if s == "cancelled":
        return (
            "Effective work order status is cancelled; execution is not allowed. "
            "Use reopen-work-order only if the order was cancelled in error."
        )
    if s == "pending_approval":
        return (
            "Effective work order status is pending_approval; approve-work-order (or --allow-pending-approval) "
            "is required before execution."
        )
    return f"status={s!r} not in executable set {sorted(allowed)}"


def resolve_work_order_for_execution(
    repo_root: Path,
    *,
    work_order_id: str | None = None,
    product_id: str | None = None,
    request_type: str | None = None,
) -> tuple[dict[str, Any] | None, Path | None, str | None]:
    """
    Load exactly one work order.

    * Either ``work_order_id`` is set, or both ``product_id`` and ``request_type`` (latest file).

    Returns ``(payload, stamped_path_or_none, error_message_or_none)``.
    """
    root = Path(repo_root).resolve()
    woid = str(work_order_id).strip() if work_order_id else ""
    pid = str(product_id).strip() if product_id else ""
    rt = str(request_type).strip() if request_type else ""

    if woid:
        if pid or rt:
            return None, None, "Specify either --work-order-id or --product-id with --request-type, not both"
        pl = find_work_order(root, woid)
        if not pl:
            return None, None, f"No work order found for id {woid!r}"
        stamped = work_orders_dir(root) / f"{pl.get('work_order_id')}.json"
        return pl, stamped if stamped.is_file() else None, None

    if pid and rt:
        pl = load_latest_work_order_for(root, product_id=pid, request_type=rt)
        if not pl:
            p = latest_work_order_path_for(root, product_id=pid, request_type=rt)
            return None, None, f"No latest work order at {p}"
        w = str(pl.get("work_order_id") or "").strip()
        stamped = work_orders_dir(root) / f"{w}.json"
        return pl, stamped if stamped.is_file() else None, None

    return None, None, "Provide --work-order-id or --product-id and --request-type"


def _dry_implementation_from_work_order(work_order: dict[str, Any]) -> tuple[str, str]:
    """Derive plan/spec summaries from the order (no Planner, no repo mutation)."""
    seed = work_order.get("implementation_seed") if isinstance(work_order.get("implementation_seed"), dict) else {}
    seed_json = json.dumps(to_jsonable(seed), indent=2, sort_keys=True)
    crit = work_order.get("acceptance_criteria") or []
    lines_plan = [
        "Dry skeleton (v1): no patches or validation commands were run.",
        "",
        "Would use implementation_seed as the structured input to the builder:",
        seed_json,
        "",
        "Acceptance criteria to satisfy in a real execution:",
    ]
    for i, c in enumerate(crit, 1):
        lines_plan.append(f"  {i}. {c}")
    plan = "\n".join(lines_plan)

    rt = str(work_order.get("request_type") or "")
    rmode = str(work_order.get("recommended_worker_mode") or "apply_patch")
    spec = (
        f"request_type={rt!r}; recommended_worker_mode={rmode!r}. "
        "Real execution would attach patch application and checks here."
    )
    return plan, spec


def build_execution_outcome(
    *,
    work_order: dict[str, Any] | None,
    source_work_order_path: str,
    started_at_utc: str,
    finished_at_utc: str,
    execution_id: str,
    execution_status: str,
    notes: str,
    allow_pending_approval: bool,
    blocked_reason: str | None = None,
    worker_mode: str = "dry_skeleton",
    implementation_plan_summary: str | None = None,
    implementation_spec_summary: str | None = None,
    files_touched: list[str] | None = None,
    validation_summary: str | None = None,
    instrumentation_apply: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalized ``argus.worker_execution_outcome.v1`` payload."""
    wo = work_order or {}
    woid = str(wo.get("work_order_id") or "").strip()
    pid = str(wo.get("product_id") or "").strip()
    rt = str(wo.get("request_type") or "").strip()

    plan, spec = ("", "")
    ft: list[str] = list(files_touched) if files_touched is not None else []
    vs = ""
    if work_order and execution_status not in ("blocked",) and not blocked_reason:
        if worker_mode == "dry_skeleton" and implementation_plan_summary is None:
            plan, spec = _dry_implementation_from_work_order(work_order)
            vs = validation_summary or "Dry run: no validation commands executed (v1 dry_skeleton)."
        else:
            plan = implementation_plan_summary or ""
            spec = implementation_spec_summary or ""
            vs = validation_summary or ""
            if not ft and instrumentation_apply:
                fw = list(instrumentation_apply.get("files_written") or [])
                fu = list(instrumentation_apply.get("files_updated") or [])
                ft = [str(x) for x in fw + fu]
    elif execution_status == "blocked":
        plan = "Execution did not run."
        spec = "N/A"
        vs = validation_summary or "N/A (blocked before builder)."
    else:
        plan = ""
        spec = ""
        vs = validation_summary or ""

    all_notes = [notes]
    if blocked_reason:
        all_notes.append(blocked_reason)
    if allow_pending_approval:
        all_notes.append("allow_pending_approval was true (non-default).")

    outcome: dict[str, Any] = {
        "schema": WORKER_EXECUTION_OUTCOME_SCHEMA,
        "execution_id": execution_id,
        "work_order_id": woid,
        "product_id": pid,
        "request_type": rt,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "worker_mode": worker_mode,
        "execution_status": execution_status,
        "implementation_plan_summary": plan,
        "implementation_spec_summary": spec,
        "files_touched": ft,
        "validation_summary": vs,
        "notes": "\n".join(x for x in all_notes if str(x).strip()),
        "source_work_order_path": source_work_order_path,
    }
    if instrumentation_apply is not None:
        outcome["instrumentation_apply"] = instrumentation_apply
    return outcome


def render_execution_outcome_markdown(payload: dict[str, Any]) -> str:
    """Human-readable companion for operator review."""
    lines = [
        "# Worker execution outcome",
        "",
        f"- **Schema:** `{payload.get('schema')}`",
        f"- **Execution id:** `{payload.get('execution_id')}`",
        f"- **Work order id:** `{payload.get('work_order_id')}`",
        f"- **Product:** `{payload.get('product_id')}`",
        f"- **Request type:** `{payload.get('request_type')}`",
        f"- **Status:** `{payload.get('execution_status')}`",
        f"- **Worker mode:** `{payload.get('worker_mode')}`",
        f"- **Started (UTC):** {payload.get('started_at_utc')}",
        f"- **Finished (UTC):** {payload.get('finished_at_utc')}",
        f"- **Source work order:** `{payload.get('source_work_order_path')}`",
        "",
        "## Implementation plan summary",
        "",
        str(payload.get("implementation_plan_summary") or "—"),
        "",
        "## Implementation spec summary",
        "",
        str(payload.get("implementation_spec_summary") or "—"),
        "",
        "## Files touched",
        "",
    ]
    for f in payload.get("files_touched") or []:
        lines.append(f"- `{f}`")
    if not (payload.get("files_touched") or []):
        lines.append("—")
    lines.extend(
        [
            "",
            "## Validation summary",
            "",
            str(payload.get("validation_summary") or "—"),
            "",
            "## Notes",
            "",
            str(payload.get("notes") or "—"),
            "",
        ]
    )
    iap = payload.get("instrumentation_apply")
    if isinstance(iap, dict) and iap:
        lines.extend(
            [
                "## Instrumentation apply",
                "",
                "```json",
                json.dumps(to_jsonable(iap), indent=2, sort_keys=True),
                "```",
                "",
            ]
        )
    lines.append("---\n\n*Worker lane v1: one explicit work order in, one outcome out; Argus remains the steward.*\n")
    return "\n".join(lines)


def write_execution_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    write_latest: bool = True,
) -> tuple[Path, Path, Path | None, Path | None]:
    """
    Persist JSON + Markdown:

    - ``runs/worker/executions/<execution_id>.json`` (+ .md)
    - ``runs/worker/executions/latest/<product_id>__<request_type>.json`` (+ .md) when ``write_latest``
    """
    root = Path(repo_root).resolve()
    if str(payload.get("schema") or "") != WORKER_EXECUTION_OUTCOME_SCHEMA:
        raise ValueError(f"payload.schema must be {WORKER_EXECUTION_OUTCOME_SCHEMA!r}")
    eid = str(payload.get("execution_id") or "").strip()
    pid = str(payload.get("product_id") or "").strip()
    rt = str(payload.get("request_type") or "").strip()
    if not eid:
        raise ValueError("payload missing execution_id")
    if not pid or not rt:
        raise ValueError("payload missing product_id or request_type")

    base = executions_dir(root)
    base.mkdir(parents=True, exist_ok=True)

    pl = dict(payload)
    body_json = dumps_json(to_jsonable(pl)) + "\n"
    md = render_execution_outcome_markdown(pl)

    stamped_json = base / f"{eid}.json"
    stamped_md = base / f"{eid}.md"
    stamped_json.write_text(body_json, encoding="utf-8")
    stamped_md.write_text(md, encoding="utf-8")

    latest_json: Path | None = None
    latest_md: Path | None = None
    if write_latest:
        ld = executions_latest_dir(root)
        ld.mkdir(parents=True, exist_ok=True)
        safe_rt = sanitize_request_type_for_filename(rt)
        latest_json = ld / f"{pid}__{safe_rt}.json"
        latest_md = ld / f"{pid}__{safe_rt}.md"
        latest_json.write_text(body_json, encoding="utf-8")
        latest_md.write_text(md, encoding="utf-8")

    return stamped_json, stamped_md, latest_json, latest_md


def execute_work_order(
    repo_root: Path,
    *,
    work_order_id: str | None = None,
    product_id: str | None = None,
    request_type: str | None = None,
    allow_pending_approval: bool = False,
    started_at_utc: str | None = None,
    save_artifacts: bool = True,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Consume one work order and produce ``argus.worker_execution_outcome.v1``.

    *Status rules (v1): only ``approved`` may execute. ``pending_approval`` is **blocked** unless
    ``allow_pending_approval`` is true (explicit escape hatch for tests / controlled environments).
    Other statuses (e.g. ``rejected``, ``cancelled``) are **blocked** with no override in v1.

    Returns a report dict including ``outcome``, ``saved_paths``, ``exit_code`` (0 = completed
    dry run successfully; 1 = blocked or error).
    """
    root = Path(repo_root).resolve()
    started = started_at_utc or _iso_now()
    allowed = _executable_statuses(allow_pending_approval=allow_pending_approval)

    pl, stamped_path, err = resolve_work_order_for_execution(
        root,
        work_order_id=work_order_id,
        product_id=product_id,
        request_type=request_type,
    )

    if err and not pl:
        eid = new_execution_id(started_at_utc=started, work_order_id=str(work_order_id or product_id or "missing"))
        finished = _iso_now()
        src = ""
        if work_order_id:
            src = f"runs/worker/work_orders/{str(work_order_id).strip()}.json"
        outcome = build_execution_outcome(
            work_order=None,
            source_work_order_path=src,
            started_at_utc=started,
            finished_at_utc=finished,
            execution_id=eid,
            execution_status="blocked",
            notes="Work order could not be loaded.",
            allow_pending_approval=allow_pending_approval,
            blocked_reason=err,
        )
        saved: dict[str, str | None] = {}
        if save_artifacts:
            if outcome.get("product_id") and outcome.get("request_type"):
                sj, sm, lj, lm = write_execution_artifacts(root, outcome)
                saved = {
                    "stamped_json": str(sj),
                    "stamped_md": str(sm),
                    "latest_json": str(lj) if lj else None,
                    "latest_md": str(lm) if lm else None,
                }
            else:
                exdir = executions_dir(root)
                exdir.mkdir(parents=True, exist_ok=True)
                out_path = exdir / f"{eid}.json"
                out_path.write_text(dumps_json(to_jsonable(outcome)) + "\n", encoding="utf-8")
                md_path = exdir / f"{eid}.md"
                md_path.write_text(render_execution_outcome_markdown(outcome), encoding="utf-8")
                saved = {
                    "stamped_json": str(out_path),
                    "stamped_md": str(md_path),
                    "latest_json": None,
                    "latest_md": None,
                }

        rep: dict[str, Any] = {
            "schema": "argus.worker.execute_work_order_report.v1",
            "outcome": outcome,
            "saved_paths": saved,
            "exit_code": 1,
            "error": err,
            "inputs": {
                "products_dir": str(products_dir.resolve()) if products_dir else None,
            },
        }
        return rep

    assert pl is not None
    woid = str(pl.get("work_order_id") or "").strip()
    st = str(pl.get("status") or "").strip()

    rel_source = ""
    if stamped_path and stamped_path.is_file():
        rel_source = _repo_rel(root, stamped_path)
    else:
        rel_source = f"runs/worker/work_orders/{woid}.json"

    eid = new_execution_id(started_at_utc=started, work_order_id=woid)

    if st not in allowed:
        finished = _iso_now()
        outcome = build_execution_outcome(
            work_order=pl,
            source_work_order_path=rel_source,
            started_at_utc=started,
            finished_at_utc=finished,
            execution_id=eid,
            execution_status="blocked",
            notes="Work order is not executable under v1 status rules.",
            allow_pending_approval=allow_pending_approval,
            blocked_reason=_blocked_reason_for_status(st, allowed=allowed),
        )
        saved: dict[str, str | None] = {}
        if save_artifacts:
            sj, sm, lj, lm = write_execution_artifacts(root, outcome)
            saved = {
                "stamped_json": str(sj),
                "stamped_md": str(sm),
                "latest_json": str(lj) if lj else None,
                "latest_md": str(lm) if lm else None,
            }
        return {
            "schema": "argus.worker.execute_work_order_report.v1",
            "outcome": outcome,
            "saved_paths": saved,
            "exit_code": 1,
            "error": None,
            "inputs": {
                "products_dir": str(products_dir.resolve()) if products_dir else None,
            },
        }

    rt = str(pl.get("request_type") or "").strip()
    if rt != REQUEST_TYPE_SIGNAL_INSTRUMENTATION:
        finished = _iso_now()
        br = (
            f"request_type={rt!r} has no real builder in this worker version; "
            f"only {REQUEST_TYPE_SIGNAL_INSTRUMENTATION!r} is supported."
        )
        outcome = build_execution_outcome(
            work_order=pl,
            source_work_order_path=rel_source,
            started_at_utc=started,
            finished_at_utc=finished,
            execution_id=eid,
            execution_status="blocked",
            notes="Unsupported request type for worker execution.",
            allow_pending_approval=allow_pending_approval,
            blocked_reason=br,
        )
        saved_ns: dict[str, str | None] = {}
        if save_artifacts:
            sj, sm, lj, lm = write_execution_artifacts(root, outcome)
            saved_ns = {
                "stamped_json": str(sj),
                "stamped_md": str(sm),
                "latest_json": str(lj) if lj else None,
                "latest_md": str(lm) if lm else None,
            }
        return {
            "schema": "argus.worker.execute_work_order_report.v1",
            "outcome": outcome,
            "saved_paths": saved_ns,
            "exit_code": 1,
            "error": None,
            "inputs": {
                "products_dir": str(products_dir.resolve()) if products_dir else None,
            },
        }

    apply_result = apply_signal_instrumentation(
        root,
        work_order=pl,
        execution_id=eid,
        products_dir=products_dir,
        applied_at_utc=None,
        save=save_artifacts,
    )
    finished = str(apply_result.get("applied_at_utc") or _iso_now())

    if save_artifacts and str(apply_result.get("apply_status") or "") != "blocked":
        try:
            write_signal_instrumentation_apply_artifacts(root, apply_result)
        except OSError as e:
            apply_result = dict(apply_result)
            apply_result["notes"] = (str(apply_result.get("notes") or "").strip() + f" Apply artifact write failed: {e}").strip()

    ast = str(apply_result.get("apply_status") or "failed")
    if ast == "blocked":
        ex_status = "success"
        wm = "dry_skeleton"
        notes_exec = "save=false: instrumentation apply skipped; product unchanged."
        plan = str(apply_result.get("validation_summary") or "Instrumentation apply not run (no-save).")
        spec = "N/A"
    elif ast == "success":
        ex_status = "success"
        wm = "signal_instrumentation_apply"
        notes_exec = str(apply_result.get("notes") or "").strip() or "Signal instrumentation apply completed."
        ca = apply_result.get("contract_applied") if isinstance(apply_result.get("contract_applied"), dict) else {}
        plan = (
            f"Applied proposed_signal_contract: dimensions {ca.get('dimensions_targeted')}; "
            f"manifest rows added {ca.get('manifest_entries_added')}."
        )
        spec = (
            f"Primary keys merged: {ca.get('primary_keys_merged')}; "
            f"manifest signal ids: {apply_result.get('signal_dimensions_applied')}."
        )
    elif ast == "partial":
        ex_status = "partial"
        wm = "signal_instrumentation_apply"
        notes_exec = str(apply_result.get("notes") or "").strip() or "Partial instrumentation apply."
        ca = apply_result.get("contract_applied") if isinstance(apply_result.get("contract_applied"), dict) else {}
        plan = f"Partial apply for dimensions {ca.get('dimensions_targeted')}."
        spec = str(apply_result.get("validation_summary") or "")
    else:
        ex_status = "failed"
        wm = "signal_instrumentation_apply"
        notes_exec = str(apply_result.get("notes") or "").strip() or "Instrumentation apply failed."
        plan = "Instrumentation apply did not complete successfully."
        spec = str(apply_result.get("validation_summary") or "")

    outcome = build_execution_outcome(
        work_order=pl,
        source_work_order_path=rel_source,
        started_at_utc=started,
        finished_at_utc=finished,
        execution_id=eid,
        execution_status=ex_status,
        notes=notes_exec,
        allow_pending_approval=allow_pending_approval,
        worker_mode=wm,
        implementation_plan_summary=plan,
        implementation_spec_summary=spec,
        validation_summary=str(apply_result.get("validation_summary") or ""),
        instrumentation_apply=apply_result,
    )

    exit_code = 0 if ex_status in ("success", "partial") else 1
    saved2: dict[str, str | None] = {}
    if save_artifacts:
        sj, sm, lj, lm = write_execution_artifacts(root, outcome)
        saved2 = {
            "stamped_json": str(sj),
            "stamped_md": str(sm),
            "latest_json": str(lj) if lj else None,
            "latest_md": str(lm) if lm else None,
        }
    return {
        "schema": "argus.worker.execute_work_order_report.v1",
        "outcome": outcome,
        "saved_paths": saved2,
        "exit_code": exit_code,
        "error": None,
        "inputs": {
            "products_dir": str(products_dir.resolve()) if products_dir else None,
        },
    }


__all__ = [
    "REQUEST_TYPE_SIGNAL_INSTRUMENTATION",
    "WORKER_EXECUTION_OUTCOME_SCHEMA",
    "build_execution_outcome",
    "execute_work_order",
    "executions_dir",
    "executions_latest_dir",
    "new_execution_id",
    "render_execution_outcome_markdown",
    "resolve_work_order_for_execution",
    "write_execution_artifacts",
]
