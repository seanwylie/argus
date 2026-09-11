"""CLI: ``argus portfolio`` (end-to-end refresh and summary)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json
from argus.observability.signal_contract import (
    evaluate_signal_contract,
    render_signal_contract_markdown,
    write_signal_contract_artifact,
)
from argus.portfolio.artifact_coherence import (
    coherence_strict_should_fail,
    format_artifact_coherence_cli_summary,
    run_artifact_coherence,
)
from argus.portfolio.autonomous_runner import (
    render_portfolio_autonomous_session_markdown,
    run_portfolio_autonomous_session,
)
from argus.portfolio.autonomy_memory import (
    render_portfolio_autonomy_memory_markdown,
    run_portfolio_autonomy_memory,
)
from argus.portfolio.builder_activity import (
    build_portfolio_builder_activity_payload,
    public_builder_activity_payload,
    write_portfolio_builder_activity_artifacts,
)
from argus.portfolio.chronic_portfolio_unblock_report import (
    render_chronic_portfolio_unblock_markdown,
    run_chronic_portfolio_unblock_report,
)
from argus.portfolio.cli import run_portfolio_allocate_command
from argus.portfolio.cycle import (
    render_portfolio_cycle_markdown,
    run_portfolio_cycle,
)
from argus.portfolio.delta_report import (
    render_portfolio_delta_report_markdown,
    run_portfolio_delta_report,
)
from argus.portfolio.escalation_inbox import (
    record_escalation_acknowledge,
    record_escalation_priority,
    record_escalation_resolve,
    record_escalation_snooze,
    render_escalation_inbox_markdown,
    run_escalation_inbox,
)
from argus.portfolio.github_onboarding import (
    render_github_onboarding_markdown,
    run_github_onboarding,
)
from argus.portfolio.history import (
    render_portfolio_history_markdown,
    run_portfolio_history,
)
from argus.portfolio.intervention import (
    render_portfolio_intervention_markdown,
    run_portfolio_intervention,
)
from argus.portfolio.intervention_actions import (
    record_intervention_acknowledge,
    record_intervention_escalate,
    record_intervention_ignore,
    record_intervention_resolve,
    record_intervention_snooze,
)
from argus.portfolio.intervention_inbox import (
    render_intervention_inbox_markdown,
    run_intervention_inbox,
)
from argus.portfolio.lifecycle import (
    render_portfolio_lifecycle_markdown,
    run_portfolio_lifecycle,
)
from argus.portfolio.operator_queue import (
    build_operator_queue_payload,
    render_operator_queue_markdown,
    write_operator_queue,
)
from argus.portfolio.outcomes import (
    render_portfolio_outcomes_markdown,
    run_portfolio_outcomes,
)
from argus.portfolio.patterns import (
    render_portfolio_patterns_markdown,
    run_portfolio_patterns,
)
from argus.portfolio.product_readiness import (
    build_product_readiness_payload,
    render_product_readiness_markdown,
)
from argus.portfolio.progression import (
    render_portfolio_progression_markdown,
    run_portfolio_progression,
)
from argus.portfolio.quiescence import (
    render_portfolio_quiescence_markdown,
    run_portfolio_quiescence,
)
from argus.portfolio.refresh import run_portfolio_refresh
from argus.portfolio.replay import (
    render_portfolio_replay_markdown,
    run_portfolio_replay,
)
from argus.portfolio.runner_quality_report import (
    render_runner_quality_report_markdown,
    run_runner_quality_report,
)
from argus.portfolio.runner_service import (
    render_runner_service_markdown,
    run_portfolio_runner_service,
)
from argus.portfolio.scheduler import (
    render_portfolio_scheduler_session_markdown,
    run_portfolio_scheduler_session,
)
from argus.portfolio.strategy import (
    render_portfolio_strategy_markdown,
    run_portfolio_strategy,
)
from argus.portfolio.temporal_worst_freshness_report import (
    render_temporal_worst_freshness_report_markdown,
    run_temporal_worst_freshness_report,
)


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def cmd_portfolio_refresh(repo: Path, args: Any) -> int:
    code, payload = run_portfolio_refresh(
        repo,
        products_dir=_products_dir(repo, args.products_dir),
        per_product_reports=bool(args.per_product),
        no_save=bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if payload.get("ok"):
            if payload.get("portfolio_state") == "empty_portfolio":
                print("Portfolio refresh: zero-state — validated inventory is empty (no products yet).")
                print("This is expected before the first product exists; Argus is idle, not broken.")
                og = payload.get("operator_guidance") or {}
                summ = str(og.get("summary") or "").strip()
                if summ:
                    print(summ)
                for line in og.get("next_actions") or []:
                    print(f"  • {line}")
            else:
                print("Portfolio refresh complete.")
            paths = payload.get("paths")
            if paths:
                for k, v in sorted(paths.items()):
                    print(f"  {k}: {v}")
            elif not payload.get("saved", True):
                print("  (--no-save: no files written)")
        elif payload.get("error") == "no_ranked_portfolio":
            print(
                "Refresh incomplete: no ranked portfolio rows (products exist but no decision candidates).",
                file=sys.stderr,
            )
            print(payload.get("hint") or "", file=sys.stderr)
        elif payload.get("error") == "invalid_products":
            print("Refresh aborted: invalid product manifests.", file=sys.stderr)
            inv = payload.get("invalid") or []
            for x in inv[:12]:
                print(f"  {x.get('product_id')}: {x.get('config_path')}", file=sys.stderr)
                for e in x.get("errors") or []:
                    print(f"    {e}", file=sys.stderr)
        else:
            print(payload.get("hint") or payload.get("error") or "refresh failed", file=sys.stderr)
            summ = repo / "runs" / "portfolio" / "latest" / "summary.txt"
            if summ.is_file():
                print(f"\nSee also: {summ}", file=sys.stderr)
    return code


def cmd_portfolio_show(repo: Path, args: Any) -> int:
    latest = repo / "runs" / "portfolio" / "latest"
    summary = latest / "summary.txt"
    refresh_js = latest / "refresh.json"
    if summary.is_file():
        if args.json:
            if refresh_js.is_file():
                print(refresh_js.read_text(encoding="utf-8"))
            else:
                print(dumps_json({"summary_text": summary.read_text(encoding="utf-8")}))
        else:
            print(summary.read_text(encoding="utf-8"))
        return 0
    print(
        "No portfolio refresh output yet. Run: argus portfolio refresh",
        file=sys.stderr,
    )
    return 1


def cmd_portfolio_builder_activity(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = build_portfolio_builder_activity_payload(repo, products_dir=pd)
    if args.no_save:
        if args.json:
            print(dumps_json(public_builder_activity_payload(payload)))
        else:
            print(
                f"products: {payload.get('product_count')} · "
                f"candidates: {payload.get('builder_candidates_considered')} · "
                f"run_id: {payload.get('run_id')}"
            )
            print(dumps_json(public_builder_activity_payload(payload)))
        return 0
    stamped, latest, latest_md = write_portfolio_builder_activity_artifacts(
        repo,
        products_dir=pd,
        payload=payload,
    )
    if args.json:
        print(dumps_json(public_builder_activity_payload(payload)))
    else:
        print(f"Wrote {stamped}")
        print(f"Wrote {latest}")
        print(f"Wrote {latest_md}")
        print(
            f"products in rollup: {payload.get('product_count')} · "
            f"run_id: {payload.get('run_id')} · use --json for full payload"
        )
    return 0


def cmd_portfolio_operator_queue(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = build_operator_queue_payload(repo, products_dir=pd)
    if args.no_save:
        if args.json:
            print(dumps_json(payload))
        else:
            print(render_operator_queue_markdown(payload))
        return 0
    jpath, mpath = write_operator_queue(repo, products_dir=pd, payload=payload)
    if args.json:
        print(dumps_json(payload))
    else:
        print(f"Wrote {jpath}")
        print(f"Wrote {mpath}")
        print()
        print(render_operator_queue_markdown(payload))
    return 0


def cmd_portfolio_quiescence(repo: Path, args: Any) -> int:
    payload = run_portfolio_quiescence(repo, write_artifacts=not bool(args.no_save))
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "quiescence"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_quiescence_markdown(payload))
    return 0


def cmd_portfolio_intervention(repo: Path, args: Any) -> int:
    payload = run_portfolio_intervention(repo, write_artifacts=not bool(args.no_save))
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "intervention"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_intervention_markdown(payload))
    return 0


def cmd_portfolio_intervention_inbox(repo: Path, args: Any) -> int:
    payload = run_intervention_inbox(
        repo,
        recent_intervention_limit=int(getattr(args, "recent_limit", 15)),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "intervention_inbox"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_intervention_inbox_markdown(payload))
    return 0


def _after_intervention_action(repo: Path, args: Any) -> int:
    payload = run_intervention_inbox(repo, write_artifacts=not bool(args.no_save))
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "intervention_inbox"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_intervention_inbox_markdown(payload))
    return 0


def cmd_portfolio_intervention_ack(repo: Path, args: Any) -> int:
    record_intervention_acknowledge(repo, args.item_id, note=args.note)
    return _after_intervention_action(repo, args)


def cmd_portfolio_intervention_resolve(repo: Path, args: Any) -> int:
    try:
        record_intervention_resolve(repo, args.item_id, note=args.note)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return _after_intervention_action(repo, args)


def cmd_portfolio_intervention_ignore(repo: Path, args: Any) -> int:
    try:
        record_intervention_ignore(repo, args.item_id, note=args.note)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return _after_intervention_action(repo, args)


def cmd_portfolio_intervention_snooze(repo: Path, args: Any) -> int:
    record_intervention_snooze(repo, args.item_id, days=float(args.days), note=args.note)
    return _after_intervention_action(repo, args)


def cmd_portfolio_intervention_escalate(repo: Path, args: Any) -> int:
    record_intervention_escalate(repo, args.item_id, note=args.note)
    return _after_intervention_action(repo, args)


def cmd_portfolio_escalation_inbox(repo: Path, args: Any) -> int:
    payload = run_escalation_inbox(
        repo,
        recent_session_limit=int(getattr(args, "recent_session_limit", 12)),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "escalation_inbox"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_escalation_inbox_markdown(payload))
    return 0


def _after_escalation_action(repo: Path, args: Any) -> int:
    payload = run_escalation_inbox(repo, write_artifacts=not bool(args.no_save))
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "escalation_inbox"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_escalation_inbox_markdown(payload))
    return 0


def cmd_portfolio_escalation_ack(repo: Path, args: Any) -> int:
    record_escalation_acknowledge(repo, args.item_id, note=args.note)
    return _after_escalation_action(repo, args)


def cmd_portfolio_escalation_resolve(repo: Path, args: Any) -> int:
    note = args.note
    reason = getattr(args, "reason", None)
    if reason:
        prefix = f"reason:{reason}"
        note = prefix if not note else f"{prefix} | {note}"
    try:
        record_escalation_resolve(repo, args.item_id, note=note)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    return _after_escalation_action(repo, args)


def cmd_portfolio_escalation_snooze(repo: Path, args: Any) -> int:
    record_escalation_snooze(repo, args.item_id, days=float(args.days), note=args.note)
    return _after_escalation_action(repo, args)


def cmd_portfolio_escalation_escalate(repo: Path, args: Any) -> int:
    record_escalation_priority(repo, args.item_id, note=args.note)
    return _after_escalation_action(repo, args)


def cmd_portfolio_outcomes(repo: Path, args: Any) -> int:
    payload = run_portfolio_outcomes(
        repo,
        limit_history=int(args.limit_history),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "outcomes"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_outcomes_markdown(payload))
    return 0


def cmd_portfolio_strategy(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_strategy(
        repo,
        limit_history=int(args.limit_history),
        products_dir=pd,
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not isinstance(payload, dict):
            print("No strategy payload.", file=sys.stderr)
            return 1
        if not bool(args.no_save):
            d = repo / "runs" / "portfolio" / "strategy"
            print(f"Wrote portfolio strategy artifacts under {d}/")
        print(render_portfolio_strategy_markdown(payload))
    return 0


def cmd_portfolio_autonomy_memory(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_autonomy_memory(
        repo,
        limit_history=int(args.limit_history),
        products_dir=pd,
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "autonomy_memory"
            print(f"Wrote portfolio autonomy memory under {d}/")
        print(render_portfolio_autonomy_memory_markdown(payload))
    return 0


def cmd_portfolio_runner_quality(repo: Path, args: Any) -> int:
    payload = run_runner_quality_report(
        repo,
        limit_history=int(args.limit_history),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "debug" / "runner_quality"
            print(f"Wrote runner quality report under {d}/")
        print(render_runner_quality_report_markdown(payload))
    return 0


def cmd_portfolio_temporal_worst_freshness(repo: Path, args: Any) -> int:
    pids = getattr(args, "twf_products", None) or []
    if not pids:
        print("Specify at least one --product", file=sys.stderr)
        return 2
    payload = run_temporal_worst_freshness_report(
        repo,
        product_ids=list(pids),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "debug" / "temporal_worst_freshness"
            print(f"Wrote temporal worst-freshness debug under {d}/")
        print(render_temporal_worst_freshness_report_markdown(payload))
    return 0


def cmd_portfolio_chronic_unblock(repo: Path, args: Any) -> int:
    products = getattr(args, "chronic_products", None) or None
    if products == []:
        products = None
    payload = run_chronic_portfolio_unblock_report(
        repo,
        write_artifacts=not bool(args.no_save),
        product_ids=products,
        queue_rank_cap=int(args.queue_rank_cap),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "debug" / "chronic_portfolio_unblock"
            print(f"Wrote chronic portfolio unblock map under {d}/")
        print(render_chronic_portfolio_unblock_markdown(payload))
    return 0


def cmd_portfolio_onboard_github(repo: Path, args: Any) -> int:
    from argus.products.scaffold import TEMPLATE_TYPES

    template = str(getattr(args, "template_type", "micro_saas") or "micro_saas")
    if template not in TEMPLATE_TYPES:
        print(f"unknown --template {template!r}", file=sys.stderr)
        return 2
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_github_onboarding(
        repo,
        github_url=str(args.github_url),
        product_id=getattr(args, "onboard_product_id", None),
        branch=getattr(args, "branch", None),
        template_type=template,
        products_dir=pd,
        force=bool(getattr(args, "force", False)),
        dry_run=bool(getattr(args, "dry_run", False)),
        run_bootstrap=not bool(getattr(args, "no_bootstrap", False)),
        minimal_bootstrap=not bool(getattr(args, "full_bootstrap", False)),
        write_artifacts=not bool(getattr(args, "no_save", False)),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "products" / "github_onboarding"
            print(f"Wrote GitHub onboarding artifacts under {d}/")
        print(render_github_onboarding_markdown(payload))
    return 0 if payload.get("ok") else 1


def cmd_portfolio_product_readiness(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = build_product_readiness_payload(repo, pid, products_dir=pd)
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "debug" / "product_readiness"
            d.mkdir(parents=True, exist_ok=True)
            (d / "latest.json").write_text(dumps_json(payload) + "\n", encoding="utf-8")
            (d / "latest.md").write_text(render_product_readiness_markdown(payload), encoding="utf-8")
            print(f"Wrote product readiness debug under {d}/")
        print(render_product_readiness_markdown(payload))
    return 0


def cmd_portfolio_signal_contract(repo: Path, args: Any) -> int:
    pid = str(getattr(args, "product_id", "") or "").strip()
    if not pid:
        print("product_id is required", file=sys.stderr)
        return 2
    payload = evaluate_signal_contract(repo, pid)
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            wp = write_signal_contract_artifact(repo, pid, payload)
            wdir = wp.parent
            (wdir / "latest.md").write_text(render_signal_contract_markdown(payload), encoding="utf-8")
            print(f"Wrote signal contract evaluation under {wdir}/")
        print(render_signal_contract_markdown(payload))
    return 0


def cmd_portfolio_lifecycle(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_lifecycle(
        repo,
        products_dir=pd,
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not bool(args.no_save):
            d = repo / "runs" / "portfolio" / "lifecycle"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_lifecycle_markdown(payload))
    return 0


def cmd_portfolio_patterns(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_patterns(
        repo,
        limit_history=int(args.limit_history),
        products_dir=pd,
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "patterns"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_patterns_markdown(payload))
    return 0


def cmd_portfolio_replay(repo: Path, args: Any) -> int:
    payload = run_portfolio_replay(repo, write_artifacts=not bool(args.no_save))
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "replay"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_replay_markdown(payload))
    return 0


def cmd_portfolio_history(repo: Path, args: Any) -> int:
    payload = run_portfolio_history(
        repo,
        limit_history=int(args.limit_history),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "history"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_history_markdown(payload))
    return 0


def cmd_portfolio_run_service(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    ns = bool(args.no_save)
    payload = run_portfolio_runner_service(
        repo,
        interval_seconds=float(getattr(args, "interval_seconds", 0.0) or 0.0),
        max_runs=int(args.max_runs),
        max_cycles=int(args.max_cycles),
        limit_per_cycle=int(args.limit_per_cycle),
        limit_history=int(args.limit_history),
        dry_run=bool(args.dry_run),
        skip_import_failed=bool(getattr(args, "skip_import_failed", False)),
        skip_waiting=bool(getattr(args, "skip_waiting", False)),
        products_dir=pd,
        write_service_artifacts=not ns,
        write_session_artifacts=not ns,
        write_stage_artifacts=not ns,
        allow_promotion=bool(getattr(args, "allow_promotion", False)),
        promotion_include_bootstrap=bool(getattr(args, "promotion_bootstrap", False)),
        include_autonomous_session_payload=bool(getattr(args, "include_autonomous_session", False)),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not ns:
            d = repo / "runs" / "portfolio" / "runner_service"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_runner_service_markdown(payload))
    return 0


def cmd_portfolio_run_autonomous(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_autonomous_session(
        repo,
        max_cycles=int(args.max_cycles),
        limit_per_cycle=int(args.limit_per_cycle),
        limit_history=int(args.limit_history),
        dry_run=bool(args.dry_run),
        skip_import_failed=bool(args.skip_import_failed),
        skip_waiting=bool(args.skip_waiting),
        products_dir=pd,
        write_session_artifacts=not bool(args.no_save),
        write_stage_artifacts=not bool(args.no_save),
        allow_promotion=bool(getattr(args, "allow_promotion", False)),
        promotion_include_bootstrap=bool(getattr(args, "promotion_bootstrap", False)),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "autonomous_runner"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_autonomous_session_markdown(payload))
    return 0


def cmd_portfolio_schedule(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_scheduler_session(
        repo,
        max_cycles=int(args.max_cycles),
        limit_per_cycle=int(args.limit_per_cycle),
        dry_run=bool(args.dry_run),
        skip_import_failed=bool(args.skip_import_failed),
        skip_waiting=bool(args.skip_waiting),
        products_dir=pd,
        write_session_artifacts=not bool(args.no_save),
        write_stage_artifacts=True,
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "scheduler"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_scheduler_session_markdown(payload))
    return 0


def cmd_portfolio_artifact_coherence(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_artifact_coherence(
        repo,
        products_dir=pd,
        write_artifacts=not bool(args.no_write_artifact),
    )
    strict_mode_arg = getattr(args, "strict_mode", None)
    if bool(getattr(args, "strict", False)) and strict_mode_arg is None:
        strict_mode = "all"
    else:
        strict_mode = strict_mode_arg
    strict_exit = bool(getattr(args, "strict", False)) or strict_mode is not None
    if strict_exit and strict_mode is None:
        strict_mode = "all"

    cli_evaluation = {
        "strict_mode": strict_mode if strict_exit else None,
        "strict_exit_requested": strict_exit,
        "would_exit_nonzero": (
            coherence_strict_should_fail(
                payload.get("overall_status"), strict_mode=strict_mode or "all"
            )
            if strict_exit
            else None
        ),
    }
    out = dict(payload)
    out["cli_evaluation"] = cli_evaluation

    if args.json:
        print(dumps_json(out))
    else:
        print(format_artifact_coherence_cli_summary(payload), end="")
        if strict_exit and strict_mode:
            sm_desc = (
                "require overall_status==valid"
                if strict_mode == "all"
                else "exit non-zero only when overall_status==invalid"
            )
            print(f"Strict mode: {strict_mode} ({sm_desc}).")
            w = cli_evaluation.get("would_exit_nonzero")
            print(f"Exit policy: would_exit_nonzero={w}")
    if strict_exit and strict_mode:
        if coherence_strict_should_fail(payload.get("overall_status"), strict_mode=strict_mode):
            return 1
    return 0


def cmd_portfolio_cycle(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_cycle(
        repo,
        limit=int(args.limit),
        products_dir=pd,
        dry_run=bool(args.dry_run),
        execute=True,
        skip_import_failed=bool(args.skip_import_failed),
        skip_waiting=bool(args.skip_waiting),
        write_cycle_artifacts=not bool(args.no_save),
        write_stage_artifacts=True,
        write_artifact_coherence=bool(getattr(args, "artifact_coherence", False)),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "cycle"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_cycle_markdown(payload))
    return 0


def cmd_portfolio_delta_report(repo: Path, args: Any) -> int:
    payload = run_portfolio_delta_report(repo, write_artifacts=not bool(args.no_save))
    if args.json:
        print(dumps_json(payload))
    else:
        if not args.no_save:
            d = repo / "runs" / "portfolio" / "delta_report"
            print(f"Wrote {d / 'latest.json'}")
            print(f"Wrote {d / 'latest.md'}")
            print()
        print(render_portfolio_delta_report_markdown(payload))
    return 0


def cmd_portfolio_progress(repo: Path, args: Any) -> int:
    pd = _products_dir(repo, getattr(args, "products_dir", None))
    payload = run_portfolio_progression(
        repo,
        limit=int(args.limit),
        products_dir=pd,
        dry_run=bool(args.dry_run),
        execute=not bool(args.no_execute),
        skip_import_failed=bool(args.skip_import_failed),
        skip_waiting=bool(args.skip_waiting),
        write_artifacts=not bool(args.no_save),
    )
    if args.json:
        print(dumps_json(payload))
    else:
        print(render_portfolio_progression_markdown(payload))
    return 0


def run_portfolio_subcommand(args: Any) -> int:
    repo = repo_root()
    if args.portfolio_command == "refresh":
        return cmd_portfolio_refresh(repo, args)
    if args.portfolio_command == "show":
        return cmd_portfolio_show(repo, args)
    if args.portfolio_command == "allocate":
        return run_portfolio_allocate_command(args)
    if args.portfolio_command == "operator-queue":
        return cmd_portfolio_operator_queue(repo, args)
    if args.portfolio_command == "builder-activity":
        return cmd_portfolio_builder_activity(repo, args)
    if args.portfolio_command == "progress":
        return cmd_portfolio_progress(repo, args)
    if args.portfolio_command == "quiescence":
        return cmd_portfolio_quiescence(repo, args)
    if args.portfolio_command == "delta-report":
        return cmd_portfolio_delta_report(repo, args)
    if args.portfolio_command == "intervention":
        return cmd_portfolio_intervention(repo, args)
    if args.portfolio_command == "intervention-inbox":
        return cmd_portfolio_intervention_inbox(repo, args)
    if args.portfolio_command == "intervention-ack":
        return cmd_portfolio_intervention_ack(repo, args)
    if args.portfolio_command == "intervention-resolve":
        return cmd_portfolio_intervention_resolve(repo, args)
    if args.portfolio_command == "intervention-ignore":
        return cmd_portfolio_intervention_ignore(repo, args)
    if args.portfolio_command == "intervention-snooze":
        return cmd_portfolio_intervention_snooze(repo, args)
    if args.portfolio_command == "intervention-escalate":
        return cmd_portfolio_intervention_escalate(repo, args)
    if args.portfolio_command == "escalation-inbox":
        return cmd_portfolio_escalation_inbox(repo, args)
    if args.portfolio_command == "escalation-ack":
        return cmd_portfolio_escalation_ack(repo, args)
    if args.portfolio_command == "escalation-resolve":
        return cmd_portfolio_escalation_resolve(repo, args)
    if args.portfolio_command == "escalation-snooze":
        return cmd_portfolio_escalation_snooze(repo, args)
    if args.portfolio_command == "escalation-escalate":
        return cmd_portfolio_escalation_escalate(repo, args)
    if args.portfolio_command == "schedule":
        return cmd_portfolio_schedule(repo, args)
    if args.portfolio_command == "run-service":
        return cmd_portfolio_run_service(repo, args)
    if args.portfolio_command == "run-autonomous":
        return cmd_portfolio_run_autonomous(repo, args)
    if args.portfolio_command == "artifact-coherence":
        return cmd_portfolio_artifact_coherence(repo, args)
    if args.portfolio_command == "cycle":
        return cmd_portfolio_cycle(repo, args)
    if args.portfolio_command == "history":
        return cmd_portfolio_history(repo, args)
    if args.portfolio_command == "replay":
        return cmd_portfolio_replay(repo, args)
    if args.portfolio_command == "outcomes":
        return cmd_portfolio_outcomes(repo, args)
    if args.portfolio_command == "patterns":
        return cmd_portfolio_patterns(repo, args)
    if args.portfolio_command == "strategy":
        return cmd_portfolio_strategy(repo, args)
    if args.portfolio_command == "lifecycle":
        return cmd_portfolio_lifecycle(repo, args)
    if args.portfolio_command == "autonomy-memory":
        return cmd_portfolio_autonomy_memory(repo, args)
    if args.portfolio_command == "runner-quality":
        return cmd_portfolio_runner_quality(repo, args)
    if args.portfolio_command == "chronic-unblock":
        return cmd_portfolio_chronic_unblock(repo, args)
    if args.portfolio_command == "temporal-worst-freshness":
        return cmd_portfolio_temporal_worst_freshness(repo, args)
    if args.portfolio_command == "product-readiness":
        return cmd_portfolio_product_readiness(repo, args)
    if args.portfolio_command == "signal-contract":
        return cmd_portfolio_signal_contract(repo, args)
    if args.portfolio_command == "onboard-github":
        return cmd_portfolio_onboard_github(repo, args)
    return 2
