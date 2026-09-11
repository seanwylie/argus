"""CLI: ``argus autonomy`` (spawn, run, shutdown, start, stop, …)."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from argus.autonomy.runner import (
    AutonomyDaemonConfig,
    autonomy_paths,
    new_autonomy_run_id,
    run_autonomy_cycle,
    save_daemon_config,
)


def _products_dir(repo: Path, override: Path | None) -> Path | None:
    if override is None:
        return None
    return override.resolve()


def run_autonomy_command(args: Any) -> int:
    from argus.cli.repo import repo_root
    from argus.core.serialize import dumps_json

    repo = repo_root()
    sub = args.autonomy_command

    if sub in ("show", "status"):
        from argus.autonomy.controller import autonomy_state_path, load_state
        from argus.autonomy.operator_policy import (
            autonomy_config_path,
            effective_policy,
            load_autonomy_config,
        )
        from argus.autonomy.policy_resolution import resolution_to_jsonable, resolve_policy_layers
        from argus.autonomy.tiers import TIER_LABELS, describe_tier
        from argus.core.serialize import dumps_json

        mode, policy, tier = effective_policy(repo)
        _, overrides, _tier_raw = load_autonomy_config(repo)
        state = load_state(repo)
        resolution = resolve_policy_layers(repo, product_id="*")
        payload = {
            "schema": "argus.autonomy.show.v2",
            "mode": mode.value,
            "tier": int(tier),
            "tier_label": TIER_LABELS.get(int(tier), ""),
            "tier_detail": describe_tier(tier),
            "config_path": str(autonomy_config_path(repo)),
            "state_path": str(autonomy_state_path(repo)),
            "policy": policy.to_jsonable(),
            "guardrail_limits": policy.to_jsonable(),
            "policy_overrides": overrides,
            "state": state,
            "policy_resolution_preview": resolution_to_jsonable(resolution),
        }
        if getattr(args, "json", False):
            print(dumps_json(payload))
        else:
            print(f"Autonomy mode: {mode.value}")
            print(f"Autonomy tier: {int(tier)} — {TIER_LABELS.get(int(tier), '')}")
            if describe_tier(tier).get("tier4_opt_in_required"):
                print("  (tier 4 requires ARGUS_ENABLE_TIER4=1 — see docs/autonomy-rollout.md)")
            print(f"Config: {payload['config_path']}")
            print(f"State:  {payload['state_path']}")
            if overrides:
                print(f"Policy overrides: {overrides}")
            print("Effective policy:")
            for k, v in policy.to_jsonable().items():
                print(f"  {k}: {v}")
            print("Policy precedence preview:", resolution.rationale)
            if state:
                print("Counters:", state)
        return 0

    if sub == "set":
        from argus.autonomy.models import AutonomyMode
        from argus.autonomy.operator_policy import save_autonomy_config
        from argus.autonomy.tiers import tier_from_mode
        from argus.core.serialize import dumps_json

        mode = AutonomyMode(str(getattr(args, "mode", "")).strip().lower())
        save_autonomy_config(repo, mode, tier=int(tier_from_mode(mode)))
        if getattr(args, "json", False):
            print(dumps_json({"ok": True, "mode": mode.value}))
        else:
            print(f"Autonomy mode set to {mode.value} → {repo / 'runs' / 'autonomy' / 'autonomy.json'}")
        return 0

    if sub == "activate":
        from argus.autonomy.activate import run_activation_gate

        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        res = run_activation_gate(repo, products_dir=pdir)
        if getattr(args, "json", False):
            print(dumps_json({"ok": res.ok, "reasons": res.reasons, "mode": res.autonomy_mode}))
        else:
            if res.ok:
                print(f"Autonomy activated: mode={res.autonomy_mode}")
            else:
                print("Activation blocked:", file=sys.stderr)
                for r in res.reasons:
                    print(f"  - {r}", file=sys.stderr)
        return 0 if res.ok else 1

    if sub == "policy":
        from argus.autonomy.operator_policy import effective_policy, load_autonomy_config
        from argus.core.serialize import dumps_json

        mode, policy, tier = effective_policy(repo)
        _, overrides, _tr = load_autonomy_config(repo)
        payload = {
            "schema": "argus.autonomy.policy.v1",
            "mode": mode.value,
            "tier": int(tier),
            "policy": policy.to_jsonable(),
            "policy_overrides": overrides,
        }
        if getattr(args, "json", False):
            print(dumps_json(payload))
        else:
            print(f"Mode: {mode.value}")
            print(dumps_json(policy.to_jsonable(), indent=2))
        return 0

    if sub == "set-tier":
        from argus.autonomy.operator_policy import save_autonomy_config
        from argus.autonomy.tiers import AutonomyTier, mode_for_tier

        t = int(getattr(args, "tier", 0))
        t = max(0, min(4, t))
        tier = AutonomyTier(t)
        mode = mode_for_tier(tier)
        save_autonomy_config(repo, mode, tier=t)
        if getattr(args, "json", False):
            print(dumps_json({"ok": True, "tier": t, "mode": mode.value}))
        else:
            print(f"Autonomy tier set to {t} (mode={mode.value}) → {repo / 'runs' / 'autonomy' / 'autonomy.json'}")
        return 0

    if sub == "explain":
        from argus.autonomy.action_matrix import (
            MATRIX,
            explain_action_contract,
            explain_rollout_action,
        )
        from argus.autonomy.operator_policy import effective_policy
        from argus.core.serialize import dumps_json
        from argus.execution.engine import load_contract_from_path

        _, _, tier = effective_policy(repo)
        ak = str(getattr(args, "action_key", "") or "").strip()
        if not ak:
            print("matrix key or path to action YAML/JSON is required", file=sys.stderr)
            return 2
        p = Path(ak)
        if p.is_file():
            contract, err = load_contract_from_path(p)
            if err or contract is None:
                print(err or "could not load action file", file=sys.stderr)
                return 1
            out = explain_action_contract(contract, current_tier=tier, source_path=str(p))
        else:
            out = explain_rollout_action(ak, current_tier=tier)
        if getattr(args, "json", False):
            print(dumps_json(out))
        else:
            if not out.get("ok"):
                print(out.get("error", "unknown"), file=sys.stderr)
                print("Known keys:", ", ".join(sorted({r.key for r in MATRIX})), file=sys.stderr)
                return 1
            if out.get("source_path"):
                print(f"Contract: {out.get('source_path')}")
                print(f"Inferred matrix key: {out.get('matrix_key_inferred')}")
            print(f"Action class: {out['action']}")
            print(out.get("rationale", ""))
            print(
                f"allowed_under_current_tier={out.get('allowed_under_current_tier')} "
                f"(effective tier {out.get('current_effective_tier')})"
            )
        return 0

    if sub == "spawn":
        from argus.autonomy.spawn_cli import run_spawn_command

        return run_spawn_command(args)

    if sub == "run":
        rid = new_autonomy_run_id()
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        code, manifest = run_autonomy_cycle(
            repo,
            run_id=rid,
            products_dir=pdir,
            product_id=getattr(args, "product_id", None),
            continue_on_error=bool(getattr(args, "continue_on_error", False)),
            execution_enabled=bool(getattr(args, "execution", False)),
        )
        run_dir = repo / "runs" / "autonomy" / rid
        if getattr(args, "json", False):
            print(dumps_json({"exit_code": code, "manifest": manifest, "run_dir": str(run_dir)}))
        else:
            status = "ok" if code == 0 else "failed"
            print(
                f"Autonomy {status} (exit {code})\n"
                f"  run_id: {rid}\n"
                f"  artifacts: {run_dir.relative_to(repo.resolve())}",
                file=sys.stderr,
            )
            for row in manifest.get("stages") or []:
                s = row.get("stage", "?")
                ok = row.get("ok")
                err = row.get("error")
                line = f"  - {s}: {'ok' if ok else 'FAILED'}"
                if err:
                    line += f" — {err}"
                print(line, file=sys.stderr)
        return code

    if sub == "start":
        interval = getattr(args, "interval", None)
        cron = getattr(args, "cron", None)
        if interval is not None and cron:
            print("Use either --interval or --cron, not both.", file=sys.stderr)
            return 2
        if interval is None and not cron:
            print("Specify --interval SECONDS or --cron 'EXPR'.", file=sys.stderr)
            return 2
        pdir = _products_dir(repo, getattr(args, "products_dir", None))
        cfg = AutonomyDaemonConfig(
            repo_root=str(repo.resolve()),
            products_dir=str(pdir) if pdir else None,
            product_id=getattr(args, "product_id", None),
            continue_on_error=bool(getattr(args, "continue_on_error", True)),
            interval_seconds=float(interval) if interval is not None else None,
            cron_expression=str(cron) if cron else None,
            execution_enabled=bool(getattr(args, "execution", False)),
        )
        cfg_path = save_daemon_config(repo, cfg)
        base, pid_path, _ = autonomy_paths(repo)
        if pid_path.is_file():
            try:
                old = int(pid_path.read_text(encoding="utf-8").strip())
                os.kill(old, 0)
                print(
                    f"Daemon already running (pid {old}). Stop it first: argus autonomy stop",
                    file=sys.stderr,
                )
                return 1
            except (ProcessLookupError, OSError, ValueError):
                pid_path.unlink(missing_ok=True)

        env = os.environ.copy()
        env["ARGUS_AUTONOMY_CONFIG"] = str(cfg_path)
        base.mkdir(parents=True, exist_ok=True)
        err_path = base / "daemon.stderr.log"
        err_f = open(err_path, "a", encoding="utf-8")
        proc = subprocess.Popen(
            [sys.executable, "-m", "argus.autonomy"],
            cwd=str(repo.resolve()),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=err_f,
            start_new_session=True,
        )
        err_f.close()
        # Child writes pid; wait briefly
        for _ in range(100):
            if pid_path.is_file():
                break
            time.sleep(0.05)
        if pid_path.is_file():
            dpid = pid_path.read_text(encoding="utf-8").strip()
            print(f"Autonomy scheduler started (pid {dpid})", file=sys.stderr)
        else:
            print(
                f"Scheduler process started (launcher pid {proc.pid}); "
                f"check {base / 'daemon.stderr.log'} if pid file is missing.",
                file=sys.stderr,
            )
        return 0

    if sub == "shutdown":
        from argus.autonomy.shutdown import run_autonomy_shutdown_command

        return run_autonomy_shutdown_command(args)

    if sub == "stop":
        _, pid_path, _ = autonomy_paths(repo)
        if not pid_path.is_file():
            print("No autonomy daemon pid file (runs/autonomy/daemon.pid).", file=sys.stderr)
            return 1
        try:
            pid = int(pid_path.read_text(encoding="utf-8").strip())
        except ValueError:
            print("Invalid pid file.", file=sys.stderr)
            return 1
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pid_path.unlink(missing_ok=True)
            print("Process not running; removed stale pid file.", file=sys.stderr)
            return 0
        except OSError as e:
            print(f"Could not signal process: {e}", file=sys.stderr)
            return 1
        for _ in range(200):
            try:
                os.kill(pid, 0)
                time.sleep(0.05)
            except ProcessLookupError:
                break
        pid_path.unlink(missing_ok=True)
        print("Autonomy scheduler stopped.", file=sys.stderr)
        return 0

    print("Unknown autonomy subcommand.", file=sys.stderr)
    return 2
