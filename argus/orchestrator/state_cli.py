"""CLI: ``argus orchestration`` — state, advance, run-progression, Cursor review (prompt/ingest)."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.advancement import advance_orchestration, run_orchestration_batch_advance
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.portfolio_priorities import (
    build_portfolio_priorities,
    write_portfolio_priorities_payload,
)
from argus.orchestrator.portfolio_priority_trends import (
    build_portfolio_priority_trends,
    write_portfolio_priority_trends_artifact,
)
from argus.orchestrator.progression import progression_summary_lines, run_orchestration_progression
from argus.orchestrator.replay import render_orchestration_replay_markdown, run_orchestration_replay
from argus.orchestrator.review_ingest import (
    ingest_orchestration_cursor_review,
    load_ingest_json,
    orchestration_review_operator_summary,
)
from argus.orchestrator.state_pass import emit_orchestration_batch, write_orchestration_state
from argus.products.inventory import build_inventory


def run_orchestration_command(args: Any, repo_root: Path) -> int:
    sub = getattr(args, "orchestration_command", None)
    if sub == "phase1-mapping":
        return _run_orchestration_phase1_mapping(args, repo_root)
    if sub == "replay":
        return _run_orchestration_replay(args, repo_root)
    if sub == "advance":
        return _run_orchestration_advance(args, repo_root)
    if sub == "cursor-prompt":
        return _run_orchestration_cursor_prompt(args, repo_root)
    if sub == "cursor-ingest":
        return _run_orchestration_cursor_ingest(args, repo_root)
    if sub == "run-progression":
        return _run_orchestration_run_progression(args, repo_root)
    if sub == "batch-advance":
        return _run_orchestration_batch_advance(args, repo_root)
    if sub == "portfolio-priorities":
        return _run_portfolio_priorities(args, repo_root)
    if sub == "portfolio-priority-trends":
        return _run_portfolio_priority_trends(args, repo_root)
    if sub != "state":
        print("Unknown orchestration subcommand.", file=sys.stderr)
        return 2

    root = repo_root.resolve()
    products_dir = getattr(args, "products_dir", None)
    pdir = products_dir.resolve() if products_dir is not None else (root / "products")

    inv_all = bool(getattr(args, "inventory_all", False))
    product_ids = getattr(args, "product_ids", None)

    if inv_all and product_ids:
        print("Use either --all or --product-id, not both.", file=sys.stderr)
        return 2
    if not inv_all and not product_ids:
        print("Specify --all or one or more --product-id values.", file=sys.stderr)
        return 2

    if inv_all:
        inv = build_inventory(root, products_dir=pdir)
        pid_list = sorted(inv.valid.keys())
        if not pid_list:
            print(f"No valid products under {pdir} (see products validate).", file=sys.stderr)
            return 1
    else:
        assert product_ids is not None
        pid_list = list(dict.fromkeys(product_ids))

    no_write = bool(getattr(args, "no_write", False))
    want_json = bool(getattr(args, "json", False))

    if len(pid_list) == 1:
        pid = pid_list[0]
        payload = evaluate_product_orchestration(root, pid)
        if not no_write:
            write_orchestration_state(root, pid)

        if want_json:
            out = dict(payload)
            out["orchestration_review_operator"] = orchestration_review_operator_summary(root, pid)
            print(dumps_json(out))
        else:
            print(f"product_id={pid}")
            print(f"orchestration_status={payload.get('orchestration_status')}")
            print(f"orchestration_status_reason={payload.get('orchestration_status_reason')}")
            print(f"overall_status={payload.get('overall_status')}")
            print(f"escalation_eligible={payload.get('escalation_eligible')}")
            print(f"next_action={payload.get('next_action')}")
            for row in payload.get("eligible_actions") or []:
                print(f"  eligible: {row.get('action_id')} — {row.get('reason')}")
            for b in payload.get("blockers") or []:
                print(f"  blocker: {b.get('kind')}: {b.get('detail')}")
            ors = orchestration_review_operator_summary(root, pid)
            if ors.get("orchestration_review_present"):
                rel = ors.get("orchestration_review_path_repo_relative") or "runs/orchestration/review/…"
                line = f"cursor orchestration review: present ({rel})"
                if ors.get("orchestration_review_matches_state_fingerprint") is False:
                    line += " — stale vs current state; re-ingest after `orchestration state`"
                print(line)
            elig = payload.get("eligible_actions") or []
            if elig:
                print(
                    f"hint: record next-step intent (no subprocess): "
                    f"uv run argus orchestration advance --product-id {pid}",
                    file=sys.stderr,
                )
            if not no_write:
                rel = root / "runs" / "orchestration" / "latest" / f"{pid}.json"
                print(f"wrote {rel.relative_to(root)}", file=sys.stderr)

        return 0

    index_payload, idx_path = emit_orchestration_batch(root, pid_list, write=not no_write)

    if want_json:
        print(dumps_json(index_payload))
    else:
        prods = index_payload.get("products") or []
        if isinstance(prods, list):
            for row in prods:
                if isinstance(row, dict):
                    pid = row.get("product_id", "")
                    st = row.get("orchestration_status", "")
                    print(f"{pid}\t{st}\t{row.get('orchestration_status_reason', '')}")
        if not no_write and idx_path is not None:
            print(f"wrote {idx_path.relative_to(root)}", file=sys.stderr)
            for row in prods:
                if isinstance(row, dict) and row.get("artifact_path"):
                    print(f"wrote {row['artifact_path']}", file=sys.stderr)

    return 0


def _run_portfolio_priorities(args: Any, repo_root: Path) -> int:
    root = repo_root.resolve()
    products_dir = getattr(args, "products_dir", None)
    pdir = products_dir.resolve() if products_dir is not None else (root / "products")
    inv_all = bool(getattr(args, "inventory_all", False))
    product_ids = getattr(args, "product_ids", None)
    if inv_all and product_ids:
        print("Use either --all or --product-id, not both.", file=sys.stderr)
        return 2
    if not inv_all and not product_ids:
        print("Specify --all or one or more --product-id values.", file=sys.stderr)
        return 2
    if inv_all:
        inv = build_inventory(root, products_dir=pdir)
        pid_list = sorted(inv.valid.keys())
        if not pid_list:
            print(f"No valid products under {pdir} (see products validate).", file=sys.stderr)
            return 1
    else:
        assert product_ids is not None
        pid_list = list(dict.fromkeys(product_ids))
    no_write = bool(getattr(args, "no_write", False))
    want_json = bool(getattr(args, "json", False))
    payload = build_portfolio_priorities(root, pid_list)
    if not no_write:
        write_portfolio_priorities_payload(root, payload)
    if want_json:
        print(dumps_json(payload))
    else:
        top = payload.get("recommended_product_id")
        na = payload.get("recommended_next_action")
        print(f"recommended_product_id={top!r} recommended_next_action={na!r}")
        for row in payload.get("products") or []:
            if isinstance(row, dict):
                print(
                    f"{row.get('rank')}\t{row.get('product_id')}\t"
                    f"score={row.get('priority_score')}\t{row.get('orchestration_status')}"
                )
        if not no_write:
            rel = root / "runs" / "orchestration" / "latest" / "portfolio_priorities.json"
            print(f"wrote {rel.relative_to(root)}", file=sys.stderr)
    return 0


def _run_portfolio_priority_trends(args: Any, repo_root: Path) -> int:
    root = repo_root.resolve()
    window = int(getattr(args, "trend_window", 5) or 5)
    if window < 1:
        print("--window must be >= 1", file=sys.stderr)
        return 2
    no_write = bool(getattr(args, "no_write", False))
    want_json = bool(getattr(args, "json", False))
    payload = build_portfolio_priority_trends(root, window_size=window)
    if not no_write:
        write_portfolio_priority_trends_artifact(root, window_size=window)
    if want_json:
        print(dumps_json(payload))
    else:
        print(f"window={payload.get('window_size')} generations_considered={payload.get('generations_considered')}")
        print(payload.get("churn_summary", ""))
        for row in payload.get("products") or []:
            if isinstance(row, dict):
                print(
                    f"{row.get('product_id')}\tlatest_rank={row.get('latest_rank')}\t"
                    f"avg={row.get('average_rank')}\tfirst×{row.get('times_ranked_first')}\t"
                    f"rising={row.get('rising')} falling={row.get('falling')} stable={row.get('stable')}"
                )
                print(f"  {row.get('trend_summary')}")
        if not no_write:
            rel = root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json"
            print(f"wrote {rel.relative_to(root)}", file=sys.stderr)
    return 0


def _run_orchestration_batch_advance(args: Any, repo_root: Path) -> int:
    root = repo_root.resolve()
    products_dir = getattr(args, "products_dir", None)
    pdir = products_dir.resolve() if products_dir is not None else (root / "products")
    inv_all = bool(getattr(args, "inventory_all", False))
    product_ids = getattr(args, "product_ids", None)
    if inv_all and product_ids:
        print("Use either --all or --product-id, not both.", file=sys.stderr)
        return 2
    if not inv_all and not product_ids:
        print("Specify --all or one or more --product-id values.", file=sys.stderr)
        return 2
    if inv_all:
        inv = build_inventory(root, products_dir=pdir)
        pid_list = sorted(inv.valid.keys())
        if not pid_list:
            print(f"No valid products under {pdir} (see products validate).", file=sys.stderr)
            return 1
    else:
        assert product_ids is not None
        pid_list = list(dict.fromkeys(product_ids))
    no_write = bool(getattr(args, "no_write", False))
    want_json = bool(getattr(args, "json", False))
    do_execute = not bool(getattr(args, "no_execute", False))
    path, body = run_orchestration_batch_advance(
        root,
        pid_list,
        execute=do_execute,
        write_state_and_index=not no_write,
    )
    if want_json:
        print(dumps_json(body))
    else:
        sel = body.get("selected_product_id")
        print(f"selected_product_id={sel!r}")
        if sel:
            adv = body.get("advancement_payload") or {}
            if isinstance(adv, dict):
                print(f"action_status={adv.get('action_status')}")
                print(f"selected_action={adv.get('selected_action')}")
        print(f"wrote {path.relative_to(root)}", file=sys.stderr)
        if not do_execute:
            print(
                "note: without execute, advancement records intent only; "
                "omit --no-execute to run the in-process step executor when queued.",
                file=sys.stderr,
            )
    return 0


def _run_orchestration_advance(args: Any, repo_root: Path) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("--product-id is required", file=sys.stderr)
        return 2
    root = repo_root.resolve()
    no_refresh = bool(getattr(args, "no_refresh_state", False))
    want_json = bool(getattr(args, "json", False))
    do_execute = bool(getattr(args, "execute", False))
    path, payload = advance_orchestration(
        root,
        pid,
        refresh_state=not no_refresh,
        execute=do_execute,
    )
    if want_json:
        print(dumps_json(payload))
    else:
        print(f"product_id={pid}")
        print(f"snapshot_orchestration_status={payload.get('snapshot_orchestration_status')}")
        print(f"action_status={payload.get('action_status')}")
        print(f"selected_action={payload.get('selected_action')}")
        print(f"selected_at_utc={payload.get('selected_at_utc')}")
        if payload.get("executed_at_utc"):
            print(f"executed_at_utc={payload.get('executed_at_utc')}")
        if payload.get("execution_detail") is not None:
            print(f"execution_detail={dumps_json(payload.get('execution_detail'))}")
        if payload.get("execution_error"):
            print(f"execution_error={payload.get('execution_error')}")
        print(f"transition_reason={payload.get('transition_reason')}")
        print(f"wrote {path.relative_to(root)}", file=sys.stderr)
        if not no_refresh:
            rel = root / "runs" / "orchestration" / "latest" / f"{pid}.json"
            print(f"refreshed {rel.relative_to(root)}", file=sys.stderr)
        if not do_execute:
            print(
                "note: without --execute, advance records intent only; "
                "run signals/audit/refine/loop commands separately.",
                file=sys.stderr,
            )
        elif payload.get("action_status") == "queued_unhandled":
            print(
                "note: selected_action has no in-process executor yet (queued_unhandled).",
                file=sys.stderr,
            )
    return 0


def _run_orchestration_cursor_prompt(args: Any, repo_root: Path) -> int:
    from argus.orchestrator.review_prompt import build_orchestration_review_prompt

    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("--product-id is required", file=sys.stderr)
        return 2
    try:
        text = build_orchestration_review_prompt(repo_root, pid)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(text)
    return 0


def _run_orchestration_run_progression(args: Any, repo_root: Path) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("--product-id is required", file=sys.stderr)
        return 2
    root = repo_root.resolve()
    max_steps = int(getattr(args, "max_steps", 8) or 8)
    no_refresh = bool(getattr(args, "no_refresh_state", False))
    want_json = bool(getattr(args, "json", False))
    do_execute = bool(getattr(args, "execute", True))
    write_artifact = not bool(getattr(args, "no_write_artifact", False))
    try:
        result = run_orchestration_progression(
            root,
            pid,
            max_steps=max_steps,
            refresh_state=not no_refresh,
            execute=do_execute,
            write_artifact=write_artifact,
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if want_json:
        print(dumps_json(result.to_jsonable()))
    else:
        for line in progression_summary_lines(result, repo_root=root):
            print(line)
        if do_execute:
            print(
                "note: progression runs in-process step execution per advance (see advance --execute).",
                file=sys.stderr,
            )
        else:
            print(
                "note: progression records advancement intent only (--no-execute); "
                "run signals/audit/refine separately.",
                file=sys.stderr,
            )
    return 0


def _run_orchestration_cursor_ingest(args: Any, repo_root: Path) -> int:
    root = repo_root.resolve()
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("--product-id is required", file=sys.stderr)
        return 2
    path = Path(str(getattr(args, "file", "") or ""))
    if not path.is_file():
        print(f"Not a file: {path}", file=sys.stderr)
        return 1
    try:
        raw = load_ingest_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"Invalid JSON: {e}", file=sys.stderr)
        return 1
    try:
        out = ingest_orchestration_cursor_review(
            root,
            pid,
            raw,
            overwrite=not bool(getattr(args, "no_overwrite", False)),
        )
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(dumps_json(out))
    else:
        print(f"product_id={pid}")
        print(f"wrote {root / 'runs' / 'orchestration' / 'review' / f'{pid}.json'}", file=sys.stderr)
    return 0


def _run_orchestration_phase1_mapping(args: Any, repo_root: Path) -> int:
    """Governance: registry ↔ ORCHESTRATION_ACTION_PHASE1_KEYS consistency + mapping table."""
    from argus.orchestrator.orchestration_phase1 import (
        OrchestrationPhase1MappingError,
        orchestration_phase1_mapping_report,
        validate_orchestration_registry_phase1_mapping,
    )
    from argus.orchestrator.step_executor import STEP_EXECUTION_REGISTRY

    try:
        validate_orchestration_registry_phase1_mapping(STEP_EXECUTION_REGISTRY.keys())
    except OrchestrationPhase1MappingError as e:
        print(str(e), file=sys.stderr)
        return 1
    payload = orchestration_phase1_mapping_report()
    if getattr(args, "json", False):
        print(dumps_json(payload))
        return 0
    print(f"Mapping table: {payload.get('mapping_table')}")
    print(f"Actions: {payload.get('action_count')}")
    for row in payload.get("actions") or []:
        aid = row.get("action_id")
        keys = row.get("phase1_keys") or []
        print(f"  {aid}: {', '.join(keys)}")
    return 0


def _run_orchestration_replay(args: Any, repo_root: Path) -> int:
    root = repo_root.resolve()
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("--product-id is required", file=sys.stderr)
        return 2
    payload = run_orchestration_replay(
        root,
        pid,
        write_artifacts=not bool(getattr(args, "no_save", False)),
    )
    if getattr(args, "json", False):
        print(dumps_json(payload))
    else:
        if not getattr(args, "no_save", False):
            d = root / "runs" / "orchestration" / "replay" / pid
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_orchestration_replay_markdown(payload))
    return 0
