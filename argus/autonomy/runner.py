"""
Autonomous loop: signals → findings → decisions → experiments → advisors →
simulation → planning → execution (when allowed).

Artifacts: ``runs/autonomy/<run_id>/`` (manifest, stages/, events.jsonl).
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import sys
import time
import traceback
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.advisors.consensus import run_consensus
from argus.core.serialize import dumps_json, to_jsonable
from argus.experiments.evaluate import run_evaluations
from argus.experiments.execution_apply import apply_execution_outcomes
from argus.orchestrator.runner import (
    LoopContext,
    run_decisions,
    run_discover,
    run_findings,
    run_signals,
)
from argus.planning.render import render_markdown
from argus.planning.weekly import build_weekly_plan
from argus.products.inventory import build_inventory
from argus.simulation.simulate import run_simulation


def _utc_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_autonomy_run_id() -> str:
    return f"auto_{_utc_ts()}_{secrets.token_hex(4)}"


def _append_event(run_dir: Path, payload: dict[str, Any]) -> None:
    path = run_dir / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(dumps_json(payload, indent=None) + "\n")


def _write_stage(
    run_dir: Path,
    stage: str,
    ok: bool,
    started: str,
    finished: str,
    *,
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    sd = run_dir / "stages" / stage
    sd.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema": "argus.autonomy.stage.v1",
        "stage": stage,
        "ok": ok,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "error": error,
    }
    if extra:
        payload.update(extra)
    (sd / "output.json").write_text(dumps_json(payload), encoding="utf-8")


def _stage_ok(
    run_dir: Path,
    stage: str,
    started: str,
    finished: str,
    **extra: Any,
) -> None:
    _write_stage(run_dir, stage, True, started, finished, error=None, extra=extra)


def _stage_fail(
    run_dir: Path,
    stage: str,
    started: str,
    err: str,
    **extra: Any,
) -> None:
    finished = datetime.now(timezone.utc).isoformat()
    _write_stage(run_dir, stage, False, started, finished, error=err, extra=extra)


def cron_next_sleep_seconds(expr: str, *, now: datetime | None = None) -> float:
    """
    Seconds until the next cron boundary (limited subset).

    Supports 5-field ``min hour dom month dow``:

    - ``*/N * * * *`` — every N minutes (N divides 60, 1–59)
    - ``M * * * *`` — at minute M every hour
    - ``M H * * *`` — daily at H:M UTC
    """
    now = now or datetime.now(timezone.utc)
    if not expr or not str(expr).strip():
        return 3600.0
    parts = expr.strip().split()
    if len(parts) != 5:
        raise ValueError("cron expression must have 5 fields: min hour dom month dow")

    minute_s, hour_s, dom_s, month_s, dow_s = parts

    def _sleep_to(target: datetime) -> float:
        delta = (target - now).total_seconds()
        return max(1.0, delta)

    # */N * * * *
    if minute_s.startswith("*/"):
        n = int(minute_s[2:])
        if n <= 0 or 60 % n != 0:
            raise ValueError("*/N only for N that divides 60")
        step = n * 60
        epoch = int(now.timestamp())
        nxt = ((epoch // step) + 1) * step
        return max(1.0, float(nxt - epoch))

    # M * * * * (hourly at minute M)
    if hour_s == "*" and dom_s == "*" and month_s == "*" and dow_s == "*":
        m = int(minute_s)
        if not (0 <= m <= 59):
            raise ValueError("minute out of range")
        target = now.replace(minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(hours=1)
        return _sleep_to(target)

    # M H * * * (daily UTC)
    if dom_s == "*" and month_s == "*" and dow_s == "*":
        m = int(minute_s)
        h = int(hour_s)
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        return _sleep_to(target)

    raise ValueError(f"unsupported cron pattern: {expr!r}")


@dataclass
class AutonomyDaemonConfig:
    """Persisted config for ``argus autonomy start``."""

    schema: str = "argus.autonomy_daemon_config.v1"
    repo_root: str = ""
    products_dir: str | None = None
    product_id: str | None = None
    continue_on_error: bool = True
    interval_seconds: float | None = None
    cron_expression: str | None = None
    execution_enabled: bool = False
    """When True, execution stage runs apply + records intent for subprocess execution policy."""

    def schedule_mode(self) -> str:
        if self.cron_expression:
            return "cron"
        return "interval"


def autonomy_paths(repo: Path) -> tuple[Path, Path, Path]:
    root = repo.resolve()
    base = root / "runs" / "autonomy"
    return base, base / "daemon.pid", base / "config.json"


def save_daemon_config(repo: Path, cfg: AutonomyDaemonConfig) -> Path:
    base, _pid, cfg_path = autonomy_paths(repo)
    base.mkdir(parents=True, exist_ok=True)
    cfg.repo_root = str(repo.resolve())
    cfg_path.write_text(dumps_json(asdict(cfg)), encoding="utf-8")
    return cfg_path


def load_daemon_config(path: Path) -> AutonomyDaemonConfig:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("invalid config")
    return AutonomyDaemonConfig(
        schema=str(raw.get("schema") or "argus.autonomy_daemon_config.v1"),
        repo_root=str(raw.get("repo_root", "")),
        products_dir=raw.get("products_dir"),
        product_id=raw.get("product_id"),
        continue_on_error=bool(raw.get("continue_on_error", True)),
        interval_seconds=(
            float(raw["interval_seconds"])
            if raw.get("interval_seconds") is not None
            else None
        ),
        cron_expression=(
            None if raw.get("cron_expression") in (None, "") else str(raw["cron_expression"])
        ),
        execution_enabled=bool(raw.get("execution_enabled", False)),
    )


def _sleep_seconds_for_config(cfg: AutonomyDaemonConfig) -> float:
    if cfg.cron_expression:
        return cron_next_sleep_seconds(cfg.cron_expression)
    if cfg.interval_seconds and cfg.interval_seconds > 0:
        return float(cfg.interval_seconds)
    return 3600.0


def run_autonomy_cycle(
    repo: Path,
    *,
    run_id: str,
    products_dir: Path | None = None,
    product_id: str | None = None,
    continue_on_error: bool = False,
    execution_enabled: bool = False,
) -> tuple[int, dict[str, Any]]:
    """
    Run the full autonomy pipeline once. Writes ``runs/autonomy/<run_id>/``.

    ``execution_enabled`` enables the execution *stage* (apply execution outcomes +
    policy note); subprocess action runs are never started automatically.
    """
    root = repo.resolve()
    run_dir = root / "runs" / "autonomy" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    pdir = products_dir.resolve() if products_dir else None
    pre = build_inventory(root, products_dir=pdir)
    if product_id:
        targets = [product_id]
    else:
        targets = sorted(pre.valid.keys())

    ctx = LoopContext(
        repo=root,
        run_dir=run_dir,
        products_dir=pdir,
        target_product_ids=targets,
    )

    started = datetime.now(timezone.utc).isoformat()
    manifest: dict[str, Any] = {
        "schema": "argus.autonomy_run.v1",
        "run_id": run_id,
        "repo_root": str(root),
        "product_filter": product_id,
        "products_dir": str(pdir) if pdir else None,
        "target_product_ids": targets,
        "started_at_utc": started,
        "finished_at_utc": None,
        "continue_on_error": continue_on_error,
        "execution_enabled": execution_enabled,
        "stages": [],
        "ok": None,
    }
    (run_dir / "manifest.json").write_text(dumps_json(manifest), encoding="utf-8")

    _append_event(
        run_dir,
        {"event": "autonomy_start", "timestamp_utc": started, "run_id": run_id},
    )

    stages_log: list[dict[str, Any]] = []
    failed = False
    findings_by_pid: dict[str, list] | None = None

    def _record(name: str, ok: bool, err: str | None = None, **extra: Any) -> None:
        stages_log.append(
            {
                "stage": name,
                "ok": ok,
                "error": err,
                **extra,
            }
        )

    # --- discover (reuse orchestrator) ---
    t0 = datetime.now(timezone.utc).isoformat()
    dr = run_discover(ctx, run_dir)
    _record("discover", dr.ok, dr.error)
    _append_event(
        run_dir,
        {
            "event": "stage",
            "stage": "discover",
            "ok": dr.ok,
            "error": dr.error,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    if not dr.ok:
        failed = True
        _stage_fail(run_dir, "discover", t0, dr.error or "discover failed")
        return _finish_manifest(run_dir, manifest, stages_log, failed=True)
    _stage_ok(run_dir, "discover", dr.started_at_utc, dr.finished_at_utc)

    # --- signals ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        sr = run_signals(ctx, run_dir)
        _record("signals", sr.ok, sr.error)
        if not sr.ok:
            failed = True
            _stage_fail(run_dir, "signals", t0, sr.error or "signals failed")
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)
        else:
            _stage_ok(run_dir, "signals", sr.started_at_utc, sr.finished_at_utc)

    # --- findings ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        fr = run_findings(ctx, run_dir)
        _record("findings", fr.ok, fr.error)
        if fr.ok and fr.extra:
            findings_by_pid = fr.extra.get("findings_by_product")
        if not fr.ok:
            failed = True
            _stage_fail(run_dir, "findings", t0, fr.error or "findings failed")
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)
        else:
            _stage_ok(run_dir, "findings", fr.started_at_utc, fr.finished_at_utc)

    # --- decisions ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        if findings_by_pid is None:
            msg = "No findings; skipping decisions."
            _record("decisions", False, msg)
            _stage_fail(run_dir, "decisions", t0, msg)
            failed = True
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)
        else:
            dres = run_decisions(ctx, run_dir, findings_by_pid)
            _record("decisions", dres.ok, dres.error)
            if not dres.ok:
                failed = True
                _stage_fail(run_dir, "decisions", t0, dres.error or "decisions failed")
                if not continue_on_error:
                    return _finish_manifest(run_dir, manifest, stages_log, failed=True)
            else:
                _stage_ok(run_dir, "decisions", dres.started_at_utc, dres.finished_at_utc)

    # --- experiments ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        try:
            evs = run_evaluations(root, product_id=product_id, apply_updates=True)
            fin = datetime.now(timezone.utc).isoformat()
            _stage_ok(
                run_dir,
                "experiments",
                t0,
                fin,
                evaluations=len(evs),
                experiment_ids=[e.experiment_id for e in evs],
            )
            _record("experiments", True, None, evaluations=len(evs))
        except Exception as e:
            failed = True
            _stage_fail(run_dir, "experiments", t0, f"{type(e).__name__}: {e}")
            _record("experiments", False, str(e))
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)

    # --- advisors (consensus per product) ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        adv_errors: list[str] = []
        try:
            assert ctx.inventory is not None
            for pid in ctx.target_product_ids:
                if pid not in ctx.inventory.valid:
                    continue
                try:
                    run_consensus(root, pid)
                except Exception as e:
                    adv_errors.append(f"{pid}: {e}")
            fin = datetime.now(timezone.utc).isoformat()
            if adv_errors:
                _stage_fail(
                    run_dir,
                    "advisors",
                    t0,
                    "; ".join(adv_errors[:8]),
                    partial_errors=adv_errors,
                )
                _record("advisors", False, "; ".join(adv_errors[:3]))
                failed = True
            else:
                _stage_ok(
                    run_dir,
                    "advisors",
                    t0,
                    fin,
                    products=len(ctx.target_product_ids),
                )
                _record("advisors", True, None, products=len(ctx.target_product_ids))
        except Exception as e:
            failed = True
            _stage_fail(run_dir, "advisors", t0, f"{type(e).__name__}: {e}")
            _record("advisors", False, str(e))
        if failed and not continue_on_error:
            return _finish_manifest(run_dir, manifest, stages_log, failed=True)

    # --- simulation ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        try:
            assert ctx.inventory is not None
            sim_results: list[dict[str, Any]] = []
            for pid in ctx.target_product_ids:
                if pid not in ctx.inventory.valid:
                    continue
                node = ctx.inventory.valid[pid].node
                res = run_simulation(root, node, experiment=None)
                sim_results.append({"product_id": pid, "scenarios": len(res.scenarios)})
            fin = datetime.now(timezone.utc).isoformat()
            _stage_ok(
                run_dir,
                "simulation",
                t0,
                fin,
                products_simulated=len(sim_results),
                summary=sim_results[:50],
            )
            _record("simulation", True, None, count=len(sim_results))
        except Exception as e:
            failed = True
            _stage_fail(run_dir, "simulation", t0, f"{type(e).__name__}: {e}")
            _record("simulation", False, str(e))
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)

    # --- planning (weekly plan artifact) ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        try:
            plan = build_weekly_plan(root, products_dir=pdir)
            md = render_markdown(plan)
            plan_dir = run_dir / "stages" / "planning"
            plan_dir.mkdir(parents=True, exist_ok=True)
            (plan_dir / "weekly.md").write_text(md, encoding="utf-8")
            (plan_dir / "weekly.json").write_text(dumps_json(to_jsonable(plan)), encoding="utf-8")
            fin = datetime.now(timezone.utc).isoformat()
            _stage_ok(
                run_dir,
                "planning",
                t0,
                fin,
                planning_window=plan.planning_window,
                markdown_relpath="stages/planning/weekly.md",
            )
            _record("planning", True, None)
        except Exception as e:
            failed = True
            _stage_fail(run_dir, "planning", t0, f"{type(e).__name__}: {e}")
            _record("planning", False, str(e))
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)

    # --- execution (apply outcomes; optional policy note) ---
    if not failed or continue_on_error:
        t0 = datetime.now(timezone.utc).isoformat()
        try:
            rep = apply_execution_outcomes(root)
            exec_ok = os.environ.get("ARGUS_EXECUTION_ENABLED", "").strip().lower() in (
                "1",
                "true",
                "yes",
            )
            allow = execution_enabled and exec_ok
            fin = datetime.now(timezone.utc).isoformat()
            _stage_ok(
                run_dir,
                "execution",
                t0,
                fin,
                apply_execution_outcomes={
                    "files_applied": rep.files_applied,
                    "files_seen": rep.files_seen,
                    "skipped_no_link": rep.skipped_no_link,
                    "experiments_updated": rep.experiments_updated,
                },
                subprocess_execution_policy="enabled only via argus execution run (not auto-started)",
                autonomy_execution_gate_ok=allow,
            )
            _record("execution", True, None, apply=rep.files_applied)
        except Exception as e:
            failed = True
            _stage_fail(run_dir, "execution", t0, f"{type(e).__name__}: {e}")
            _record("execution", False, str(e))
            if not continue_on_error:
                return _finish_manifest(run_dir, manifest, stages_log, failed=True)

    return _finish_manifest(run_dir, manifest, stages_log, failed=failed)


def _finish_manifest(
    run_dir: Path,
    manifest: dict[str, Any],
    stages_log: list[dict[str, Any]],
    *,
    failed: bool,
) -> tuple[int, dict[str, Any]]:
    manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    manifest["stages"] = stages_log
    manifest["ok"] = not failed
    (run_dir / "manifest.json").write_text(dumps_json(manifest), encoding="utf-8")
    _append_event(
        run_dir,
        {
            "event": "autonomy_end",
            "timestamp_utc": manifest["finished_at_utc"],
            "ok": not failed,
        },
    )
    return (1 if failed else 0), manifest


def daemon_main() -> int:
    """Entry for ``python -m argus.autonomy`` (background scheduler)."""
    cfg_path = os.environ.get("ARGUS_AUTONOMY_CONFIG")
    if not cfg_path:
        print("ARGUS_AUTONOMY_CONFIG not set", file=sys.stderr)
        return 2
    path = Path(cfg_path)
    if not path.is_file():
        print(f"Config not found: {path}", file=sys.stderr)
        return 2
    cfg = load_daemon_config(path)
    repo = Path(cfg.repo_root).resolve()
    _, pid_path, _ = autonomy_paths(repo)

    def _write_pid() -> None:
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def _stop() -> None:
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, lambda *_: _stop())
    signal.signal(signal.SIGINT, lambda *_: _stop())
    _write_pid()

    pdir = Path(cfg.products_dir).resolve() if cfg.products_dir else None

    while True:
        run_id = new_autonomy_run_id()
        run_dir = repo / "runs" / "autonomy" / run_id
        try:
            code, _ = run_autonomy_cycle(
                repo,
                run_id=run_id,
                products_dir=pdir,
                product_id=cfg.product_id,
                continue_on_error=cfg.continue_on_error,
                execution_enabled=cfg.execution_enabled,
            )
            (run_dir / "daemon_note.txt").write_text(
                f"daemon_cycle exit_code={code}\n",
                encoding="utf-8",
            )
        except Exception:
            tb = traceback.format_exc()
            (repo / "runs" / "autonomy" / "daemon_error.log").write_text(
                tb + "\n",
                encoding="utf-8",
            )
        try:
            sleep_s = _sleep_seconds_for_config(cfg)
            time.sleep(sleep_s)
        except Exception:
            time.sleep(3600.0)
