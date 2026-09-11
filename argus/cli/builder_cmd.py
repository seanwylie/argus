"""CLI handlers for ``argus builder``."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from argus.builder.branch_review import format_builder_branch_review_human
from argus.builder.creation_phase1 import (
    apply_creation_proposal,
    build_creation_proposal,
    render_creation_proposal_markdown,
    write_creation_proposal_artifacts,
    write_creation_result_artifact,
)
from argus.builder.host_readiness import (
    compute_builder_host_readiness,
    exit_code_for_readiness,
    format_builder_host_readiness_human,
)
from argus.builder.invoke import BuilderInvokeError, run_builder_invoke
from argus.builder.merge_local import run_builder_merge_local
from argus.builder.multi_product_view import (
    build_builder_multi_product_view,
    format_builder_multi_product_view_human,
)
from argus.builder.next_expansion_generate import (
    NextExpansionGenerateError,
    generate_and_write_next_expansion,
    generate_next_expansion_payload,
)
from argus.builder.next_expansion_prepare import (
    NextExpansionPrepareError,
    build_prepare_result,
    write_prepare_artifacts,
)
from argus.builder.reconcile import run_builder_reconcile
from argus.builder.set_manual_target import (
    apply_manual_builder_target,
    build_primary_target_bug_fix,
    build_primary_target_signal_instrumentation,
)
from argus.builder.status import (
    compute_builder_status,
    format_builder_operator_summary_human,
    format_builder_status_human,
)
from argus.builder.work_orders import (
    build_work_orders_bundle_from_signal_contract,
    load_latest_work_order_bundle,
    render_cursor_implementation_brief,
    work_orders_output_dir,
    write_work_order_artifacts,
)
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json


def run_builder_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.builder_command == "prepare":
        return cmd_builder_prepare(repo, args)
    if args.builder_command == "set-target":
        return cmd_builder_set_target(repo, args)
    if args.builder_command == "status":
        return cmd_builder_status(repo, args)
    if args.builder_command == "portfolio-view":
        return cmd_builder_portfolio_view(repo, args)
    if args.builder_command == "review":
        return cmd_builder_review(repo, args)
    if args.builder_command == "merge":
        return cmd_builder_merge(repo, args)
    if args.builder_command == "invoke":
        return cmd_builder_invoke(repo, args)
    if args.builder_command == "reconcile":
        return cmd_builder_reconcile(repo, args)
    if args.builder_command == "next-expansion":
        return cmd_builder_next_expansion(repo, args)
    if args.builder_command == "work-orders":
        return cmd_builder_work_orders(repo, args)
    if args.builder_command == "render-brief":
        return cmd_builder_render_brief(repo, args)
    if args.builder_command == "creation-propose":
        return cmd_builder_creation_propose(repo, args)
    if args.builder_command == "creation-apply":
        return cmd_builder_creation_apply(repo, args)
    if args.builder_command == "doctor":
        return cmd_builder_doctor(repo, args)
    return 2


def cmd_builder_doctor(_repo: Path, args: Any) -> int:
    """Static host readiness for Builder agent containment (PATH/env); not a full runtime proof."""
    payload = compute_builder_host_readiness()
    strict = bool(getattr(args, "strict", False))
    code = exit_code_for_readiness(payload, strict=strict)
    if args.json:
        print(dumps_json(payload))
    else:
        print(format_builder_host_readiness_human(payload), end="")
    return code


def cmd_builder_portfolio_view(repo: Path, args: Any) -> int:
    """Multi-product Builder rows from inventory; no execution."""
    pdir = getattr(args, "products_dir", None)
    view = build_builder_multi_product_view(repo, products_dir=pdir)
    if args.json:
        print(dumps_json(view))
    else:
        print(format_builder_multi_product_view_human(view), end="")
    return 0


def cmd_builder_status(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    payload = compute_builder_status(repo, pid, products_dir=pdir)
    if args.json:
        print(dumps_json(payload))
    elif getattr(args, "brief", False):
        print(format_builder_operator_summary_human(payload), end="")
    else:
        print(format_builder_operator_summary_human(payload), end="")
        print()
        print(format_builder_status_human(payload))
    return 0


def cmd_builder_review(repo: Path, args: Any) -> int:
    """Local merge-readiness lines (no git merge; see ``builder_branch_review`` on reconcile)."""
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    payload = compute_builder_status(repo, pid, products_dir=pdir)
    lr = payload.get("latest_reconcile") or {}
    if not lr.get("present"):
        print(
            f"No reconcile record — run: argus builder reconcile {pid}",
            file=sys.stderr,
        )
        return 1
    if args.json:
        print(
            dumps_json(
                {
                    "review_status": lr.get("review_status"),
                    "review_reasons": lr.get("review_reasons"),
                    "builder_branch": lr.get("review_builder_branch"),
                    "baseline_commit": lr.get("review_baseline_commit"),
                    "changed_file_count": lr.get("review_changed_file_count"),
                    "trust_degraded_dirty_tree": lr.get("review_trust_degraded_dirty_tree"),
                }
            )
        )
    else:
        print(format_builder_branch_review_human(lr, product_id=pid), end="")
    return 0


def cmd_builder_merge(repo: Path, args: Any) -> int:
    """Local ``git merge`` in nested product repo only when review is merge_candidate."""
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    into = getattr(args, "merge_into", None)
    if into is not None and str(into).strip() == "":
        into = None
    dry_run = bool(getattr(args, "merge_dry_run", False))
    no_record = bool(getattr(args, "merge_no_record", False))
    record, code = run_builder_merge_local(
        repo,
        pid,
        products_dir=pdir,
        into_branch=str(into).strip() if into else None,
        dry_run=dry_run,
        no_record=no_record,
    )
    if getattr(args, "json", False):
        print(dumps_json(record))
    else:
        ms = record.get("merge_status")
        print(f"builder merge: {ms}", file=sys.stderr)
        print(f"  product_id: {record.get('product_id')}", file=sys.stderr)
        print(f"  review_status_at_merge_time: {record.get('review_status_at_merge_time')}", file=sys.stderr)
        print(f"  builder_branch: {record.get('builder_branch')}", file=sys.stderr)
        print(f"  target_branch: {record.get('target_branch')}", file=sys.stderr)
        print(f"  git_cwd: {record.get('git_cwd')}", file=sys.stderr)
        if record.get("refusal_reason"):
            print(f"  refusal_reason: {record.get('refusal_reason')}", file=sys.stderr)
        if record.get("error"):
            print(f"  error: {record.get('error')}", file=sys.stderr)
        if not no_record and record.get("product_id"):
            mdir = repo / "runs" / "builder" / "merge" / str(record.get("product_id")) / "latest.json"
            print(f"  record: {mdir}", file=sys.stderr)
    return code


def cmd_builder_next_expansion(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    try:
        if args.no_save:
            payload = generate_next_expansion_payload(repo, pid, products_dir=pdir)
            if args.json:
                print(dumps_json(payload))
            else:
                pt = payload.get("primary_target") or {}
                print(
                    f"Next target (not written): {pt.get('id')} ({pt.get('target_type')})",
                    file=sys.stderr,
                )
            return 0
        _payload, path = generate_and_write_next_expansion(repo, pid, products_dir=pdir)
    except NextExpansionGenerateError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(dumps_json(_payload))
    else:
        pt = _payload.get("primary_target") or {}
        print(f"Wrote {path}", file=sys.stderr)
        print(f"Next target: {pt.get('id')} ({pt.get('target_type')})", file=sys.stderr)
    return 0


def cmd_builder_prepare(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    under = str(getattr(args, "output", "product") or "product")
    try:
        if args.no_save:
            res = build_prepare_result(repo, pid, under=under, products_dir=pdir)
            if args.json:
                print(dumps_json(res.task))
            else:
                print(
                    f"Would write {res.paths.prompt_md}\nWould write {res.paths.task_json}",
                    file=sys.stderr,
                )
            return 0
        res = write_prepare_artifacts(repo, pid, under=under, products_dir=pdir)
    except NextExpansionPrepareError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(dumps_json(res.task))
    print(f"Wrote {res.paths.prompt_md}", file=sys.stderr)
    print(f"Wrote {res.paths.task_json}", file=sys.stderr)
    return 0


def cmd_builder_set_target(repo: Path, args: Any) -> int:
    """Write content/next_expansion.json primary_target for bug_fix / signal_instrumentation."""
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    kind = str(getattr(args, "kind", "") or "").strip()
    tid = str(getattr(args, "target_id", "") or "").strip()
    allow_paths = getattr(args, "allow_paths", None)
    if not allow_paths:
        print("At least one --allow-path is required", file=sys.stderr)
        return 2
    dry_run = bool(getattr(args, "dry_run", False))
    stamp = not bool(getattr(args, "no_stamp_time", False))

    try:
        if kind == "bug_fix":
            stmt = getattr(args, "bug_statement", None)
            if not (stmt and str(stmt).strip()):
                print("--bug-statement is required when --kind bug_fix", file=sys.stderr)
                return 2
            if getattr(args, "signal_statement", None):
                print("--signal-statement is not used with --kind bug_fix", file=sys.stderr)
                return 2
            pt = build_primary_target_bug_fix(
                target_id=tid,
                allowed_paths_exact=allow_paths,
                bug_statement=str(stmt).strip(),
                group_id=None,
                allowed_path_patterns=getattr(args, "path_patterns", None) or None,
                success_condition=getattr(args, "success_condition", None),
                stop_condition=getattr(args, "stop_condition", None),
            )
        elif kind == "signal_instrumentation":
            stmt = getattr(args, "signal_statement", None)
            if not (stmt and str(stmt).strip()):
                print("--signal-statement is required when --kind signal_instrumentation", file=sys.stderr)
                return 2
            if getattr(args, "bug_statement", None):
                print("--bug-statement is not used with --kind signal_instrumentation", file=sys.stderr)
                return 2
            pt = build_primary_target_signal_instrumentation(
                target_id=tid,
                allowed_paths_exact=allow_paths,
                signal_statement=str(stmt).strip(),
                group_id=None,
                allowed_path_patterns=getattr(args, "path_patterns", None) or None,
                success_condition=getattr(args, "success_condition", None),
                stop_condition=getattr(args, "stop_condition", None),
                expected_product_paths_exist=getattr(args, "expect_paths", None) or None,
                instrumentation_touch_paths=getattr(args, "touch_paths", None) or None,
            )
        else:
            print(f"unsupported --kind {kind!r}", file=sys.stderr)
            return 2

        res = apply_manual_builder_target(
            repo,
            pid,
            products_dir=pdir,
            primary_target=pt,
            dry_run=dry_run,
            stamp_as_of_utc=stamp,
        )
    except NextExpansionPrepareError as e:
        print(str(e), file=sys.stderr)
        return 1

    want_json = bool(getattr(args, "json", False))
    do_prepare = bool(getattr(args, "prepare", False))
    prepare_under = str(getattr(args, "prepare_output", "product") or "product")

    if want_json or dry_run:
        print(dumps_json(res.payload))
    if dry_run:
        if do_prepare:
            print(
                "argus builder set-target: --prepare ignored with --dry-run "
                "(next_expansion.json was not written)",
                file=sys.stderr,
            )
        print(
            f"(dry-run) validated {res.path}; not written",
            file=sys.stderr,
        )
        return 0
    if not want_json:
        print(f"Wrote {res.path}", file=sys.stderr)

    if do_prepare:
        try:
            pres = write_prepare_artifacts(
                repo, pid, under=prepare_under, products_dir=pdir
            )
        except NextExpansionPrepareError as e:
            print(
                f"set-target: prepare failed after target write succeeded: {e}",
                file=sys.stderr,
            )
            print(
                f"set-target: next_expansion.json is at {res.path}; fix the error and run: "
                f"argus builder prepare {pid}",
                file=sys.stderr,
            )
            return 1
        print(f"Wrote {pres.paths.prompt_md}", file=sys.stderr)
        print(f"Wrote {pres.paths.task_json}", file=sys.stderr)

    return 0


def cmd_builder_invoke(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    if bool(getattr(args, "execute", False)) and bool(getattr(args, "dry_run", False)):
        print("Cannot use --execute and --dry-run together", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    pp = getattr(args, "prompt_path", None)
    tp = getattr(args, "task_path", None)
    try:
        record = run_builder_invoke(
            repo,
            pid,
            products_dir=pdir,
            prompt_path=pp,
            task_path=tp,
            prepare_first=bool(getattr(args, "prepare_first", False)),
            prepare_under=str(getattr(args, "prepare_output", "product") or "product"),
            execute=bool(getattr(args, "execute", False)),
            explicit_dry_run=bool(getattr(args, "dry_run", False)),
            no_record=bool(getattr(args, "no_record", False)),
            execution_backend=str(getattr(args, "backend", "cursor") or "cursor"),
            agent_sandbox=getattr(args, "agent_sandbox", None),
            allow_unsandboxed=bool(getattr(args, "allow_unsandboxed", False)),
            agent_network_mode=getattr(args, "agent_network_mode", None),
        )
    except BuilderInvokeError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(dumps_json(record))
    else:
        print(f"Prompt: {record['source_prompt_path']}", file=sys.stderr)
        print(f"Task:   {record['source_task_path']}", file=sys.stderr)
        be = record.get("execution_backend") or "cursor"
        print(f"Backend: {be}", file=sys.stderr)
        print(f"Mode:   {record['mode']} (invocation_status={record['invocation_status']})", file=sys.stderr)
        if record.get("prepared_contract_status") == "stale":
            sw = record.get("prepared_contract_warning") or record.get("error") or ""
            if sw.strip():
                print(f"argus builder invoke: {sw}", file=sys.stderr)
        ppd = record.get("project_permission_decision")
        if isinstance(ppd, dict) and ppd.get("execution_proceeds") is False:
            print(
                f"argus builder invoke: blocked by project policy "
                f"({ppd.get('aggregate_decision')}): {ppd.get('reason') or 'see record'}",
                file=sys.stderr,
            )
        bc = record.get("builder_containment") if isinstance(record.get("builder_containment"), dict) else {}
        if be == "agent" and bc:
            print(
                f"Containment: requested={bc.get('containment_requested')} "
                f"applied={bc.get('containment_applied')}",
                file=sys.stderr,
            )
            nm = bc.get("network_mode")
            if nm:
                na = bc.get("network_applied")
                print(
                    f"Network: mode={nm} applied={na}"
                    + (f" ({bc.get('network_reason')})" if bc.get("network_reason") else ""),
                    file=sys.stderr,
                )
            if record.get("mode") == "execute" and (
                bc.get("trust_degraded_unsandboxed") or bc.get("containment_fallback_used")
            ):
                print(
                    "argus builder invoke: WARNING — agent ran unsandboxed or sandbox fallback (degraded trust).",
                    file=sys.stderr,
                )
            if record.get("mode") == "execute" and bc.get("trust_degraded_network_open"):
                print(
                    "argus builder invoke: NOTICE — network mode allow_all (explicit unrestricted; trust flag set).",
                    file=sys.stderr,
                )
        print(f"Command: {record.get('command')}", file=sys.stderr)
        pw = record.get("prompt_file_warning")
        if pw:
            print(f"argus builder invoke: WARNING — {pw}", file=sys.stderr)
        if not getattr(args, "no_record", False):
            inv_dir = repo / "runs" / "builder" / "invoke" / pid
            print(f"Record: {inv_dir / 'latest.json'}", file=sys.stderr)
    if record.get("mode") == "execute":
        if record.get("invocation_status") == "failed":
            return 1
        ex = record.get("exit_code")
        if ex is not None and ex != 0:
            return 1
    return 0


def cmd_builder_reconcile(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pdir = getattr(args, "products_dir", None)
    pp = getattr(args, "prompt_path", None)
    tp = getattr(args, "task_path", None)
    irp = getattr(args, "invoke_record_path", None)
    record, code = run_builder_reconcile(
        repo,
        pid,
        products_dir=pdir,
        prompt_path=pp,
        task_path=tp,
        invoke_record_path=irp,
        skip_signals=bool(getattr(args, "skip_signals", False)),
        skip_findings=bool(getattr(args, "skip_findings", False)),
        no_record=bool(getattr(args, "no_record", False)),
        emit_escalation=not bool(getattr(args, "no_escalation", False)),
        quiet=bool(getattr(args, "json", False)),
        prepare_next=bool(getattr(args, "prepare_next", False)),
        prepare_under=str(getattr(args, "prepare_output", "product") or "product"),
        generate_next_expansion=bool(getattr(args, "generate_next_expansion", False)),
    )
    if args.json:
        print(dumps_json(record))
    else:
        print(f"target_transition_status={record['target_transition_status']}", file=sys.stderr)
        print(f"prior={record['prior_resolved_target']} current={record['current_target']}", file=sys.stderr)
        if not getattr(args, "no_record", False):
            print(f"Record: {repo / 'runs' / 'builder' / 'reconcile' / pid / 'latest.json'}", file=sys.stderr)
        print(
            f"signals: {record['signals_collect'].get('status')} "
            f"findings: {record['findings_generate'].get('status')}",
            file=sys.stderr,
        )
        gr = record.get("generate_next_expansion") or {}
        print(
            f"generate_next_expansion: {gr.get('status')} ({gr.get('reason')})",
            file=sys.stderr,
        )
        pn = record.get("prepare_next") or {}
        print(
            f"prepare_next: {pn.get('status')} ({pn.get('reason')})",
            file=sys.stderr,
        )
        bem = record.get("builder_escalation_emit") or {}
        if bem.get("emitted") and bem.get("packet_id"):
            print(
                f"Escalation: wrote {bem.get('packet_id')} → {bem.get('path_repo')}",
                file=sys.stderr,
            )
        elif bem.get("reason") == "dedupe_recent_packet":
            dup = bem.get("duplicate_of") or {}
            print(
                f"Escalation: dedupe skip (see {dup.get('path_repo', 'runs/escalations/latest')})",
                file=sys.stderr,
            )
        sc = record.get("builder_scope_check") or {}
        if sc.get("schema") == "argus.builder_scope_check.v2":
            print(
                f"builder_scope_check: {sc.get('status')} "
                f"breach={sc.get('scope_breach')} "
                f"path={sc.get('path_scope_breach')} semantic={sc.get('semantic_scope_breach')}",
                file=sys.stderr,
            )
            ps = sc.get("path_scope") if isinstance(sc.get("path_scope"), dict) else {}
            if ps.get("argus_core_breach"):
                print(
                    "builder_scope_check: argus/ modified (forbidden for product-scoped Builder)",
                    file=sys.stderr,
                )
        elif sc.get("scope_breach") is not None:
            print(
                f"builder_scope_check: breach={sc.get('scope_breach')} (path-only v1)",
                file=sys.stderr,
            )
        eo = record.get("execution_outcome") or {}
        if eo.get("outcome"):
            print(
                f"execution_outcome: {eo.get('outcome')} ({eo.get('schema')})",
                file=sys.stderr,
            )
        bds = record.get("builder_diff_summary") or {}
        if bds.get("source"):
            print(
                f"builder_diff_summary: {bds.get('changed_file_count', 0)} file(s) "
                f"(source={bds.get('source')}; truncated={bds.get('diff_truncated')})",
                file=sys.stderr,
            )
    return code


def cmd_builder_work_orders(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    bf = getattr(args, "backend", None)
    bundle = build_work_orders_bundle_from_signal_contract(repo, pid, backend_filter=bf)
    if args.json:
        print(dumps_json(bundle))
        return 0
    if not args.no_save:
        write_work_order_artifacts(repo, bundle)
        d = work_orders_output_dir(repo, pid)
        print(f"Wrote builder work orders under {d}/")
    n = len(bundle.get("work_orders") or [])
    print(f"Generated {n} work order(s). See {work_orders_output_dir(repo, pid)}/latest.md — use `argus builder render-brief {pid}` for a Cursor brief.")
    return 0


def cmd_builder_render_brief(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    wid = getattr(args, "work_order_id", None)
    bundle = load_latest_work_order_bundle(repo, pid)
    if bundle is None:
        print(
            f"No work order bundle at runs/builder/work_orders/{pid}/latest.json — run: "
            f"argus builder work-orders {pid}",
            file=sys.stderr,
        )
        return 1
    orders = [w for w in (bundle.get("work_orders") or []) if isinstance(w, dict)]
    if not orders:
        print("Latest bundle has no work orders.", file=sys.stderr)
        return 1
    chosen = None
    if wid:
        for w in orders:
            if str(w.get("work_order_id")) == str(wid).strip():
                chosen = w
                break
        if chosen is None:
            print(f"work_order_id not found: {wid!r}", file=sys.stderr)
            return 1
    else:
        chosen = orders[0]

    brief = render_cursor_implementation_brief(chosen)
    if args.json:
        print(dumps_json({"work_order_id": chosen.get("work_order_id"), "brief_markdown": brief}))
        return 0
    if not args.no_save:
        out = repo / "runs" / "builder" / "work_orders" / pid / "latest_brief.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(brief, encoding="utf-8")
        print(f"Wrote {out}")
    print(brief)
    return 0


def cmd_builder_creation_propose(repo: Path, args: Any) -> int:
    cid = str(getattr(args, "candidate_id", "") or "").strip()
    if not cid:
        print("--candidate-id is required", file=sys.stderr)
        return 2
    opid = getattr(args, "product_id", None)
    opid_s = str(opid).strip() if opid else None
    tmpl = getattr(args, "template_type", None)
    try:
        prop = build_creation_proposal(
            repo,
            candidate_id=cid,
            operator_product_id=opid_s,
            template_type=tmpl,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(dumps_json(prop))
    else:
        print(render_creation_proposal_markdown(prop))
    if not args.no_save:
        jp, mp = write_creation_proposal_artifacts(repo, prop)
        print(f"Wrote {jp}", file=sys.stderr)
        print(f"Wrote {mp}", file=sys.stderr)
    return 0


def cmd_builder_creation_apply(repo: Path, args: Any) -> int:
    raw = getattr(args, "proposal_path", None)
    path = Path(str(raw)) if raw else repo / "runs" / "builder" / "creation_phase1" / "latest_proposal.json"
    path = path.expanduser()
    if not path.is_file():
        print(f"No proposal file at {path}", file=sys.stderr)
        return 1
    try:
        prop = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Could not read proposal JSON: {e}", file=sys.stderr)
        return 1
    pdir = getattr(args, "products_dir", None)
    result = apply_creation_proposal(
        repo,
        prop,
        write=bool(args.write),
        products_dir=pdir,
    )
    if args.json:
        print(dumps_json(result))
    else:
        if result.get("dry_run"):
            print(result.get("message") or "dry-run")
            for line in result.get("would_create_paths") or []:
                print(f"  would create: {line}")
        else:
            print(result.get("message") or "")
    if not args.no_save:
        wp = write_creation_result_artifact(repo, result)
        print(f"Wrote {wp}", file=sys.stderr)
    if not result.get("ok"):
        return 1
    return 0
