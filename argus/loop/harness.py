"""
Full Argus pipeline harness: one deterministic pass through the canonical stages.

Stages (in order): discovery, doctrine_load, capability_load, signals, findings,
decisions, history_snapshot, trends, ideas, experiments, advisors, planning,
plan_actions, approval_evaluation, execution, dashboard.

Artifacts: ``runs/loop/<run_id>/`` with ``summary.json`` consolidated report.

Includes a **planning** stage (weekly snapshot under the run dir + ``runs/planning/actions.json``).
**Escalation** is not integrated here; ``summary.json`` includes a ``chain`` hint so operators
can run ``uv run argus escalation generate <product_id>`` after the harness (see that field).
"""

from __future__ import annotations

import time
import traceback
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.actions.executor import dry_run
from argus.advisors.consensus import run_consensus
from argus.approval.rules import evaluate_auto_approval
from argus.capabilities.evaluate import evaluate_capabilities, write_evaluation_artifact
from argus.core.serialize import dumps_json, to_jsonable
from argus.dashboard.render import write_dashboard_html
from argus.doctrine.load import load_doctrine_for_product
from argus.experiments.evaluate import run_evaluations
from argus.experiments.prioritize import prioritize_experiments
from argus.history.snapshot import build_portfolio_snapshot, new_snapshot_id_and_time
from argus.history.storage import write_snapshot_json
from argus.idea_generation.pipeline import run_pipeline
from argus.orchestrator.loop import new_run_id
from argus.orchestrator.runner import (
    LoopContext,
    run_decisions,
    run_discover,
    run_findings,
    run_signals,
)
from argus.planning.plan_actions import build_planning_actions
from argus.planning.render import render_markdown
from argus.planning.weekly import build_weekly_plan
from argus.products.inventory import build_inventory
from argus.trends.analyze import analyze_all_with_history
from argus.trends.models import TrendsRunPayload
from argus.trends.render import format_full_text, payload_to_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stage_out(run_dir: Path, name: str, payload: dict[str, Any]) -> None:
    d = run_dir / "stages" / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "output.json").write_text(dumps_json(payload), encoding="utf-8")


def _append_event(run_dir: Path, payload: dict[str, Any]) -> None:
    path = run_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps_json(payload, indent=None) + "\n")


def run_full_loop_harness(
    repo: Path,
    *,
    products_dir: Path | None = None,
    product_id: str | None = None,
    dry_run_execution: bool = True,
    continue_on_error: bool = True,
) -> tuple[int, dict[str, Any]]:
    """
    Run the extended pipeline; write ``summary.json`` under ``runs/loop/<run_id>/``.

    ``dry_run_execution`` — only static dry-run analysis for generated action contracts
    (no subprocess execution).
    """
    root = repo.resolve()
    run_id = new_run_id()
    run_dir = root / "runs" / "loop" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    pre = build_inventory(root, products_dir=products_dir)
    targets = [product_id] if product_id else sorted(pre.valid.keys())
    if not targets:
        summary = {
            "schema": "argus.loop.full_summary.v1",
            "run_id": run_id,
            "repo_root": str(root),
            "dry_run_execution": dry_run_execution,
            "product_filter": product_id,
            "started_at_utc": _now_iso(),
            "finished_at_utc": _now_iso(),
            "exit_code": 1,
            "ok": False,
            "error": "no valid products in inventory",
            "stages": [],
            "chain": {
                "escalation": "separate",
                "next_commands": [],
            },
        }
        (run_dir / "summary.json").write_text(dumps_json(summary), encoding="utf-8")
        return 1, summary
    if product_id and product_id not in pre.valid:
        summary = {
            "schema": "argus.loop.full_summary.v1",
            "run_id": run_id,
            "repo_root": str(root),
            "dry_run_execution": dry_run_execution,
            "product_filter": product_id,
            "started_at_utc": _now_iso(),
            "finished_at_utc": _now_iso(),
            "exit_code": 2,
            "ok": False,
            "error": f"product {product_id!r} not in valid inventory",
            "stages": [],
            "chain": {
                "escalation": "separate",
                "next_commands": [],
            },
        }
        (run_dir / "summary.json").write_text(dumps_json(summary), encoding="utf-8")
        return 2, summary

    ctx = LoopContext(
        repo=root,
        run_dir=run_dir,
        products_dir=products_dir.resolve() if products_dir else None,
        target_product_ids=targets,
    )

    started = _now_iso()
    manifest: dict[str, Any] = {
        "schema": "argus.loop_full_run.v1",
        "run_id": run_id,
        "repo_root": str(root),
        "product_filter": product_id,
        "products_dir": str(products_dir) if products_dir else None,
        "target_product_ids": targets,
        "started_at_utc": started,
        "finished_at_utc": None,
        "dry_run_execution": dry_run_execution,
        "continue_on_error": continue_on_error,
        "stages": [],
        "ok": None,
    }
    (run_dir / "manifest.json").write_text(dumps_json(manifest), encoding="utf-8")
    _append_event(run_dir, {"event": "loop_full_start", "timestamp_utc": started, "run_id": run_id})

    stages: list[dict[str, Any]] = []
    findings_by_pid: dict[str, list] | None = None
    failed = False

    def _record(
        name: str,
        ok: bool,
        *,
        ms: float,
        error: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        row: dict[str, Any] = {
            "stage": name,
            "ok": ok,
            "duration_ms": round(ms, 3),
            "error": error,
        }
        if extra:
            row["extra"] = extra
        stages.append(row)
        manifest["stages"].append(
            {"stage": name, "ok": ok, "duration_ms": row["duration_ms"], "error": error}
        )

    def _run(name: str, fn: Any) -> None:
        nonlocal failed, findings_by_pid
        if failed and not continue_on_error:
            return
        t0 = time.perf_counter()
        try:
            fn()
            _record(name, True, ms=(time.perf_counter() - t0) * 1000.0)
        except Exception as e:
            failed = True
            tb = traceback.format_exc()
            _record(name, False, ms=(time.perf_counter() - t0) * 1000.0, error=f"{type(e).__name__}: {e}")
            _stage_out(
                run_dir,
                name,
                {"ok": False, "error": str(e), "traceback": tb},
            )
            _append_event(
                run_dir,
                {"event": "stage_failed", "stage": name, "error": str(e), "timestamp_utc": _now_iso()},
            )

    # 1 discovery
    def s_discover() -> None:
        nonlocal failed
        dr = run_discover(ctx, run_dir)
        if not dr.ok:
            failed = True
            raise RuntimeError(dr.error or "discover failed")

    _run("discovery", s_discover)

    # 2 doctrine load
    def s_doctrine() -> None:
        assert ctx.inventory is not None
        loaded: dict[str, Any] = {}
        errors: list[str] = []
        for pid in ctx.target_product_ids:
            rec = ctx.inventory.valid.get(pid)
            if rec is None:
                continue
            doc, err = load_doctrine_for_product(root, pid, product_root=rec.node.product_root)
            if err:
                errors.append(f"{pid}: {err}")
            loaded[pid] = {"present": doc is not None, "error": err}
        _stage_out(
            run_dir,
            "doctrine_load",
            {"products": loaded, "errors": errors},
        )
        if errors:
            raise RuntimeError("; ".join(errors[:3]))

    _run("doctrine_load", s_doctrine)

    # 3 capability load
    def s_cap() -> None:
        ev = evaluate_capabilities(root)
        path = write_evaluation_artifact(root, ev)
        _stage_out(
            run_dir,
            "capability_load",
            {
                "evaluation_path": str(path.relative_to(root)),
                "missing_count": len(ev.missing_capabilities),
            },
        )

    _run("capability_load", s_cap)

    # 4 signals
    def s_sig() -> None:
        nonlocal failed
        sr = run_signals(ctx, run_dir)
        if not sr.ok:
            failed = True
            raise RuntimeError(sr.error or "signals failed")

    _run("signals", s_sig)

    # 5 findings
    def s_find() -> None:
        nonlocal failed, findings_by_pid
        fr = run_findings(ctx, run_dir)
        if not fr.ok:
            failed = True
            raise RuntimeError(fr.error or "findings failed")
        if fr.extra:
            findings_by_pid = fr.extra.get("findings_by_product")

    _run("findings", s_find)

    # 6 decisions
    def s_dec() -> None:
        nonlocal failed
        if not findings_by_pid:
            raise RuntimeError("no findings; cannot run decisions")
        dres = run_decisions(ctx, run_dir, findings_by_pid)
        if not dres.ok:
            failed = True
            raise RuntimeError(dres.error or "decisions failed")

    _run("decisions", s_dec)

    # 7 history snapshot
    def s_hist() -> None:
        sid, iso = new_snapshot_id_and_time(label=f"loop_full_{run_id}")
        snap = build_portfolio_snapshot(
            root,
            snapshot_id=sid,
            observed_at_utc=iso,
            label=f"loop_full_{run_id}",
            products_dir=products_dir,
        )
        path = write_snapshot_json(root, snap)
        _stage_out(
            run_dir,
            "history_snapshot",
            {
                "snapshot_id": snap.snapshot_id,
                "written_path": str(path.relative_to(root)),
                "product_count": len(snap.products),
            },
        )

    _run("history_snapshot", s_hist)

    # 8 trends
    def s_trends() -> None:
        summaries = analyze_all_with_history(root)
        payload = TrendsRunPayload(
            generated_at_utc=_now_iso(),
            repo_root=str(root),
            command="loop_full",
            summaries=summaries,
        )
        text_body = format_full_text(payload)
        d = root / "runs" / "trends"
        d.mkdir(parents=True, exist_ok=True)
        latest_json = d / "latest.json"
        latest_txt = d / "latest.txt"
        latest_json.write_text(payload_to_json(payload), encoding="utf-8")
        latest_txt.write_text(text_body, encoding="utf-8")
        _stage_out(
            run_dir,
            "trends",
            {"summaries": len(summaries), "trends_latest": "runs/trends/latest.json"},
        )

    _run("trends", s_trends)

    # 9 ideas (one ``run_pipeline`` per target in ``ctx.target_product_ids``)
    def s_ideas() -> None:
        assert ctx.inventory is not None
        seed = f"loop_full_{run_id}"
        per_product: list[dict[str, Any]] = []
        for pid in ctx.target_product_ids:
            if pid not in ctx.inventory.valid:
                continue
            path, bundle = run_pipeline(root, pid, seed=seed)
            per_product.append(
                {
                    "product_id": pid,
                    "idea_count": len(bundle.ideas),
                    "bundle_path": str(path.relative_to(root)),
                }
            )
        _stage_out(
            run_dir,
            "ideas",
            {
                "seed": seed,
                "products": per_product,
                "total_ideas": sum(p["idea_count"] for p in per_product),
                "latest_bundle": "runs/ideas/latest.json",
            },
        )

    _run("ideas", s_ideas)

    # 10 experiments (evaluate + prioritize includes propose)
    def s_exp() -> None:
        inv = build_inventory(root, products_dir=products_dir)
        run_evaluations(root, product_id=product_id, apply_updates=True)
        pr = prioritize_experiments(root, product_id=product_id, inventory=inv)
        proposal_count = sum(len(v) for v in pr.by_product.values())
        _stage_out(
            run_dir,
            "experiments",
            {
                "prioritization_products": len(pr.by_product),
                "proposal_count": proposal_count,
            },
        )

    _run("experiments", s_exp)

    # 11 advisors
    def s_adv() -> None:
        assert ctx.inventory is not None
        for pid in ctx.target_product_ids:
            if pid not in ctx.inventory.valid:
                continue
            run_consensus(root, pid)
        _stage_out(run_dir, "advisors", {"products": list(ctx.target_product_ids)})

    _run("advisors", s_adv)

    # 12 planning
    def s_plan() -> None:
        plan = build_weekly_plan(root, products_dir=products_dir)
        md = render_markdown(plan)
        pdir = run_dir / "stages" / "planning"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "weekly.md").write_text(md, encoding="utf-8")
        (pdir / "weekly.json").write_text(dumps_json(to_jsonable(plan)), encoding="utf-8")
        _stage_out(
            run_dir,
            "planning",
            {"planning_window": plan.planning_window, "markdown_relpath": "stages/planning/weekly.md"},
        )

    _run("planning", s_plan)

    # 13 action generation (+ persist canonical bundle)
    bundle_holder: dict[str, Any] = {}

    def s_actions() -> None:
        bundle = build_planning_actions(root, products_dir=products_dir)
        bundle_holder["bundle"] = bundle
        base = root / "runs" / "planning"
        base.mkdir(parents=True, exist_ok=True)
        (base / "actions.json").write_text(dumps_json(to_jsonable(bundle)), encoding="utf-8")
        ad = run_dir / "stages" / "plan_actions"
        ad.mkdir(parents=True, exist_ok=True)
        (ad / "actions.json").write_text(dumps_json(to_jsonable(bundle)), encoding="utf-8")
        _stage_out(
            run_dir,
            "plan_actions",
            {"action_count": len(bundle.actions)},
        )

    _run("plan_actions", s_actions)

    # 14 approval evaluation (sample contracts)
    def s_appr() -> None:
        bundle = bundle_holder.get("bundle")
        if bundle is None:
            bundle = build_planning_actions(root, products_dir=products_dir)
        samples: list[dict[str, Any]] = []
        for c in bundle.actions[:24]:
            ev = evaluate_auto_approval(c, repo_root=root)
            samples.append(
                {
                    "action_id": c.action_id,
                    "product_id": c.product_id,
                    "auto_approve": ev.auto_approve,
                    "reasons": ev.reasons[:6],
                }
            )
        _stage_out(run_dir, "approval_evaluation", {"sampled": len(samples)})
        (run_dir / "stages" / "approval_evaluation" / "samples.json").write_text(
            dumps_json(samples), encoding="utf-8"
        )

    _run("approval_evaluation", s_appr)

    # 15 execution
    def s_exec() -> None:
        bundle = bundle_holder.get("bundle")
        if bundle is None:
            bundle = build_planning_actions(root, products_dir=products_dir)
        inv = build_inventory(root, products_dir=products_dir)
        results: list[dict[str, Any]] = []
        lim = min(32, len(bundle.actions))
        for c in bundle.actions[:lim]:
            dr = dry_run(c, repo_root=root, inventory=inv)
            results.append(
                {
                    "action_id": c.action_id,
                    "product_id": c.product_id,
                    "ok": dr.ok,
                    "validation_errors": dr.validation_errors[:4],
                    "dangerous_flags": dr.dangerous_flags[:4],
                }
            )
        mode = "dry_run_analysis"
        if not dry_run_execution:
            mode = "dry_run_only_policy"
        _stage_out(
            run_dir,
            "execution",
            {"mode": mode, "contracts_checked": len(results), "dry_run_execution": dry_run_execution},
        )
        (run_dir / "stages" / "execution" / "dry_run_results.json").write_text(
            dumps_json(results), encoding="utf-8"
        )

    _run("execution", s_exec)

    # 16 dashboard
    def s_dash() -> None:
        path, _payload = write_dashboard_html(root, None, products_dir=products_dir)
        _stage_out(
            run_dir,
            "dashboard",
            {"path": str(path.relative_to(root))},
        )

    _run("dashboard", s_dash)

    finished = _now_iso()
    manifest["finished_at_utc"] = finished
    manifest["ok"] = not failed
    (run_dir / "manifest.json").write_text(dumps_json(manifest), encoding="utf-8")

    exit_code = 0 if not failed else 1
    summary: dict[str, Any] = {
        "schema": "argus.loop.full_summary.v1",
        "run_id": run_id,
        "repo_root": str(root),
        "dry_run_execution": dry_run_execution,
        "product_filter": product_id,
        "products_dir": str(products_dir) if products_dir else None,
        "target_product_ids": targets,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "exit_code": exit_code,
        "ok": not failed,
        "run_dir_repo_relative": f"runs/loop/{run_id}",
        "stages": stages,
        "chain": {
            "escalation": "separate",
            "next_commands": [f"uv run argus escalation generate {pid}" for pid in targets],
        },
    }
    (run_dir / "summary.json").write_text(dumps_json(summary), encoding="utf-8")
    try:
        from argus.run.summary import write_run_summary_artifacts

        write_run_summary_artifacts(root, run_id)
    except Exception as e:
        warnings.warn(
            f"loop full: could not write summary.txt ({type(e).__name__}: {e})",
            UserWarning,
            stacklevel=1,
        )
    _append_event(run_dir, {"event": "loop_full_finish", "timestamp_utc": finished, "ok": not failed})
    return exit_code, summary
