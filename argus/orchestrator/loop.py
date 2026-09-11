"""
One-shot **analysis** loop: ``discover`` → ``signals`` → ``findings`` → ``decisions``.

This is the implementation behind ``argus loop run``. It updates canonical artifact dirs
(``runs/signals/``, ``runs/findings/``, ``runs/decisions/``) like ``portfolio refresh`` for the
target products.

**Orchestrator boundary:** weekly planning (``argus planning weekly``), escalation packets
(``argus escalation generate``), experiments, advisors, simulation, and execution are **out of
scope** for this loop—run them as separate commands. See ``argus loop full`` for a broader harness
that includes planning snapshots (still not escalation).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.runner import (
    LoopContext,
    run_decisions,
    run_discover,
    run_findings,
    run_signals,
)
from argus.orchestrator.stages import LOOP_STAGE_ORDER, LoopStage, StageResult
from argus.products.inventory import build_inventory


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_run_id() -> str:
    """Unique id for ``runs/loop/<run_id>/`` (sortable UTC time + random suffix)."""
    return f"{_utc_ts()}_{secrets.token_hex(4)}"


def _append_run_event(run_dir: Path, payload: dict[str, Any]) -> None:
    path = run_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps_json(payload, indent=None) + "\n")


def _stage_result_to_manifest(sr: StageResult) -> dict[str, Any]:
    d: dict[str, Any] = {
        "stage": sr.stage.value,
        "ok": sr.ok,
        "started_at_utc": sr.started_at_utc,
        "finished_at_utc": sr.finished_at_utc,
        "error": sr.error,
        "output_relpath": sr.output_relpath,
    }
    return d


def run_analysis_loop(
    repo: Path,
    *,
    products_dir: Path | None = None,
    product_id: str | None = None,
    continue_on_error: bool = False,
) -> tuple[int, dict[str, Any]]:
    """
    Run the full analysis pipeline with artifacts under ``runs/loop/<run_id>/``.

    Updates canonical locations under ``runs/signals/``, ``runs/findings/``, ``runs/decisions/``
    like ``portfolio refresh`` (latest bundles for participating products).

    Returns ``(exit_code, manifest)``. Exit code is 0 only if every stage succeeded.
    With ``continue_on_error``, failed stages are recorded and later stages are skipped.
    """
    root = repo.resolve()
    run_id = new_run_id()
    run_dir = root / "runs" / "loop" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    pre_inv = build_inventory(root, products_dir=products_dir)
    if product_id:
        target_product_ids = [product_id]
    else:
        target_product_ids = sorted(pre_inv.valid.keys())

    ctx = LoopContext(
        repo=root,
        run_dir=run_dir,
        products_dir=products_dir,
        target_product_ids=target_product_ids,
    )

    started = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema": "argus.loop_run.v1",
        "run_id": run_id,
        "repo_root": str(root),
        "product_filter": product_id,
        "products_dir": str(products_dir) if products_dir else None,
        "target_product_ids": target_product_ids,
        "started_at_utc": started,
        "finished_at_utc": None,
        "continue_on_error": continue_on_error,
        "stages": [],
        "ok": None,
    }
    (run_dir / "manifest.json").write_text(dumps_json(manifest), encoding="utf-8")

    _append_run_event(
        run_dir,
        {
            "event": "loop_start",
            "timestamp_utc": started,
            "run_id": run_id,
            "target_product_ids": target_product_ids,
        },
    )

    results: list[StageResult] = []
    findings_by_pid: dict[str, list] | None = None
    failed = False

    def _persist_manifest() -> None:
        manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
        manifest["stages"] = [_stage_result_to_manifest(r) for r in results]
        manifest["ok"] = not failed and all(r.ok for r in results)
        (run_dir / "manifest.json").write_text(dumps_json(manifest), encoding="utf-8")

    for stage in LOOP_STAGE_ORDER:
        if failed:
            break

        if stage == LoopStage.DISCOVER:
            r = run_discover(ctx, run_dir)
        elif stage == LoopStage.SIGNALS:
            r = run_signals(ctx, run_dir)
        elif stage == LoopStage.FINDINGS:
            r = run_findings(ctx, run_dir)
            if r.ok:
                findings_by_pid = r.extra.get("findings_by_product") if r.extra else None
        elif stage == LoopStage.DECISIONS:
            if findings_by_pid is None:
                r = StageResult(
                    stage=stage,
                    ok=False,
                    started_at_utc=datetime.now(timezone.utc).isoformat(),
                    finished_at_utc=datetime.now(timezone.utc).isoformat(),
                    error="Findings stage did not produce data; cannot run decisions.",
                )
            else:
                r = run_decisions(ctx, run_dir, findings_by_pid)
        else:
            continue

        results.append(r)
        _append_run_event(
            run_dir,
            {
                "event": "stage_complete",
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "stage": stage.value,
                "ok": r.ok,
                "error": r.error,
            },
        )
        if not r.ok:
            failed = True
            _persist_manifest()
            if not continue_on_error:
                return 1, manifest
        else:
            _persist_manifest()

    exit_code = 0 if not failed and all(r.ok for r in results) else 1
    _persist_manifest()
    return exit_code, manifest
