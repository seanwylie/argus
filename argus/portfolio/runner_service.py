"""
Cadence wrapper for :func:`run_portfolio_autonomous_session` — bounded loops, stop sentinel, heartbeat artifacts.

Does not replace per-session guardrails inside the autonomous runner; adds service-level caps and visibility.
"""

from __future__ import annotations

import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from argus.core.serialize import dumps_json
from argus.portfolio.artifact_coherence import load_artifact_coherence_operational_snapshot
from argus.portfolio.autonomous_runner import run_portfolio_autonomous_session
from argus.portfolio.substrate_policy_state import load_substrate_policy_state

PORTFOLIO_RUNNER_SERVICE_SCHEMA = "argus.portfolio_runner_service.v1"

STATUS_STARTING = "starting"
STATUS_IDLE = "idle"
STATUS_RUNNING = "running"
STATUS_SLEEPING = "sleeping"
STATUS_STOPPED = "stopped"

# Service stop reasons (codes use prefix runner_service.stop.)
STOP_SENTINEL = "stop_sentinel"
STOP_MAX_RUNS = "max_runs_reached"
STOP_COMPLETED = "completed"
STOP_AUTONOMOUS_SESSION = "autonomous_session_finished"


def portfolio_runner_service_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "runner_service"


def runner_service_stop_sentinel_path(repo_root: Path) -> Path:
    return portfolio_runner_service_dir(repo_root) / "STOP"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _runner_service_artifact_coherence_block(repo_root: Path) -> dict[str, Any]:
    """Compact substrate trust signal from durable coherence artifact only (no recompute)."""
    snap = load_artifact_coherence_operational_snapshot(repo_root)
    if not snap.get("present"):
        return {"present": False}
    return {
        "present": True,
        "overall_status": snap.get("overall_status"),
        "run_id": snap.get("run_id"),
        "evaluated_at_utc": snap.get("evaluated_at_utc"),
        "summary": snap.get("summary"),
    }


def _attach_artifact_coherence(out: dict[str, Any], repo_root: Path) -> None:
    out["artifact_coherence"] = _runner_service_artifact_coherence_block(repo_root)


def _attach_substrate_policy_state(out: dict[str, Any], repo_root: Path) -> None:
    st = load_substrate_policy_state(repo_root)
    out["substrate_policy_state"] = {
        "schema": st.get("schema"),
        "consecutive_degraded_sessions": st.get("consecutive_degraded_sessions"),
        "updated_at_utc": st.get("updated_at_utc"),
        "last_session_id": st.get("last_session_id"),
        "last_session_substrate_overall": st.get("last_session_substrate_overall"),
    }


def _summarize_session(pl: dict[str, Any], *, max_len: int = 4000) -> str:
    s = str(pl.get("session_summary") or "")
    if len(s) > max_len:
        return s[: max_len - 3] + "..."
    return s


def render_runner_service_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio runner service (heartbeat)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Service run id:** `{payload.get('service_run_id')}`",
        f"**Updated (UTC):** {payload.get('updated_at_utc')}",
        "",
        "## Status",
        "",
        f"- **current_status:** `{payload.get('current_status')}`",
        f"- **stop_reason:** `{payload.get('stop_reason')}`",
        f"- **stop_reason_codes:** {payload.get('stop_reason_codes')}",
        f"- **loop_count (sessions completed):** {payload.get('loop_count')}",
        "",
        "## Timing",
        "",
        f"- **service_started_at_utc:** {payload.get('service_started_at_utc')}",
        f"- **service_finished_at_utc:** {payload.get('service_finished_at_utc')}",
        f"- **last_run_started_at_utc:** {payload.get('last_run_started_at_utc')}",
        f"- **last_run_finished_at_utc:** {payload.get('last_run_finished_at_utc')}",
        f"- **next_planned_run_at_utc:** {payload.get('next_planned_run_at_utc')}",
        "",
        "## Last autonomous session",
        "",
        f"- **last_session_id:** `{payload.get('last_session_id')}`",
        f"- **last_autonomous_stop_reason:** `{payload.get('last_autonomous_stop_reason')}`",
        "",
        "### Summary",
        "",
        str(payload.get("last_run_summary") or "—"),
        "",
    ]
    ac = payload.get("artifact_coherence")
    lines.extend(["## Artifact coherence (substrate trust)", ""])
    if isinstance(ac, dict) and ac.get("present"):
        lines.append(f"- **overall_status:** `{ac.get('overall_status')}`")
        lines.append(f"- **run_id:** `{ac.get('run_id')}`")
        lines.append(f"- **evaluated_at_utc:** {ac.get('evaluated_at_utc')}")
        summ = str(ac.get("summary") or "").strip()
        if summ:
            lines.extend(["", str(summ), ""])
    else:
        lines.append("- **present:** false (no durable `runs/debug/artifact_coherence/latest.json` or invalid schema)")
        lines.append("")
    sps = payload.get("substrate_policy_state") or {}
    lines.extend(["## Substrate policy state (degraded streak)", ""])
    if isinstance(sps, dict) and sps.get("schema"):
        lines.append(f"- **consecutive_degraded_sessions:** {sps.get('consecutive_degraded_sessions')}")
        lines.append(f"- **last_session_id:** `{sps.get('last_session_id')}`")
        lines.append(f"- **last_session_substrate_overall:** `{sps.get('last_session_substrate_overall')}`")
        lines.append(f"- **updated_at_utc:** {sps.get('updated_at_utc')}")
        lines.append("")
    else:
        lines.append("- _(none — see `runs/portfolio/autonomous_runner/substrate_policy_state.json`)_")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_runner_service_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    transition_stamp: str | None = None,
) -> tuple[Path | None, Path | None, Path, Path]:
    """
    Write ``latest.{json,md}`` and optionally a stamped copy for a major transition.

    Stamped name: ``{service_run_id}__{transition_stamp}.json`` (and ``.md``) when ``transition_stamp`` is set.
    """
    root = repo_root.resolve()
    pl = dict(payload)
    d = portfolio_runner_service_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    sid = str(pl.get("service_run_id") or "")
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    latest_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    latest_md.write_text(render_runner_service_markdown(pl), encoding="utf-8")
    stamped_json: Path | None = None
    stamped_md: Path | None = None
    if transition_stamp and sid:
        tsafe = transition_stamp.replace("/", "_")[:80]
        stamped_json = d / f"{sid}__{tsafe}.json"
        stamped_md = d / f"{sid}__{tsafe}.md"
        stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
        shutil.copyfile(latest_md, stamped_md)
    return stamped_json, stamped_md, latest_json, latest_md


def _base_payload(
    *,
    service_run_id: str,
    started: str,
    inputs: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": PORTFOLIO_RUNNER_SERVICE_SCHEMA,
        "service_run_id": service_run_id,
        "service_started_at_utc": started,
        "service_finished_at_utc": None,
        "updated_at_utc": started,
        "current_status": STATUS_STARTING,
        "last_run_started_at_utc": None,
        "last_run_finished_at_utc": None,
        "last_session_id": None,
        "last_autonomous_stop_reason": None,
        "next_planned_run_at_utc": None,
        "loop_count": 0,
        "stop_reason": "",
        "stop_reason_codes": [],
        "last_run_summary": "",
        "inputs": inputs,
    }


def run_portfolio_runner_service(
    repo_root: Path,
    *,
    interval_seconds: float = 0.0,
    max_runs: int = 1,
    max_cycles: int = 5,
    limit_per_cycle: int = 5,
    limit_history: int = 30,
    dry_run: bool = False,
    skip_import_failed: bool = False,
    skip_waiting: bool = False,
    products_dir: Path | None = None,
    write_service_artifacts: bool = True,
    write_session_artifacts: bool = True,
    write_stage_artifacts: bool = True,
    check_autonomous_stop_sentinel: bool = True,
    allow_promotion: bool = False,
    promotion_include_bootstrap: bool = False,
    sleep_fn: Callable[[float], None] | None = None,
    include_autonomous_session_payload: bool = False,
) -> dict[str, Any]:
    """
    Run one or more autonomous sessions.

    - **One-shot:** ``interval_seconds <= 0`` — runs a single ``run_portfolio_autonomous_session`` (``max_runs`` ignored).
    - **Cadence:** ``interval_seconds > 0`` — runs up to ``max_runs`` sessions, sleeping between sessions.

    Stop file: ``runs/portfolio/runner_service/STOP`` (checked before each session and after each sleep).

    ``write_service_artifacts`` controls heartbeat under ``runs/portfolio/runner_service/`` only; pass ``False`` for a fully silent-on-disk mode (e.g. tests with ``--no-save``).
    """
    root = repo_root.resolve()
    interval_seconds = max(0.0, float(interval_seconds))
    sleep = sleep_fn or time.sleep
    now = _utc_now()
    service_run_id = now.strftime("%Y%m%dT%H%M%SZ")
    started_iso = _iso(now)

    inputs: dict[str, Any] = {
        "interval_seconds": float(interval_seconds),
        "max_runs": int(max_runs),
        "max_cycles": int(max_cycles),
        "limit_per_cycle": int(limit_per_cycle),
        "limit_history": int(limit_history),
        "dry_run": bool(dry_run),
        "skip_import_failed": bool(skip_import_failed),
        "skip_waiting": bool(skip_waiting),
        "write_service_artifacts": bool(write_service_artifacts),
        "write_session_artifacts": bool(write_session_artifacts),
        "write_stage_artifacts": bool(write_stage_artifacts),
        "check_autonomous_stop_sentinel": bool(check_autonomous_stop_sentinel),
        "allow_promotion": bool(allow_promotion),
        "promotion_include_bootstrap": bool(promotion_include_bootstrap),
        "products_dir": str(products_dir) if products_dir is not None else None,
        "include_autonomous_session_payload": bool(include_autonomous_session_payload),
    }

    sentinel = runner_service_stop_sentinel_path(root)
    one_shot = float(interval_seconds) <= 0.0
    effective_max_runs = 1 if one_shot else max(1, int(max_runs))

    out = _base_payload(service_run_id=service_run_id, started=started_iso, inputs=inputs)
    stop_codes: list[str] = []
    stop_reason = ""
    last_pl: dict[str, Any] | None = None

    def persist(status: str, **extra: Any) -> None:
        out["current_status"] = status
        out["updated_at_utc"] = _iso(_utc_now())
        out.update(extra)
        if write_service_artifacts:
            _attach_artifact_coherence(out, root)
            _attach_substrate_policy_state(out, root)
            write_runner_service_artifacts(root, out)

    def persist_transition(label: str) -> None:
        out["updated_at_utc"] = _iso(_utc_now())
        if write_service_artifacts:
            _attach_artifact_coherence(out, root)
            _attach_substrate_policy_state(out, root)
            write_runner_service_artifacts(root, out, transition_stamp=label)

    if write_service_artifacts:
        out["current_status"] = STATUS_STARTING
        out["updated_at_utc"] = _iso(_utc_now())
        _attach_artifact_coherence(out, root)
        _attach_substrate_policy_state(out, root)
        write_runner_service_artifacts(root, out, transition_stamp="start")

    def sentinel_present() -> bool:
        return sentinel.is_file()

    session_index = 0
    while session_index < effective_max_runs:
        if sentinel_present():
            stop_reason = STOP_SENTINEL
            stop_codes.append("runner_service.stop.sentinel_file")
            break

        session_index += 1
        t0 = _utc_now()
        out["last_run_started_at_utc"] = _iso(t0)
        out["next_planned_run_at_utc"] = None
        persist(STATUS_RUNNING)

        last_pl = run_portfolio_autonomous_session(
            root,
            max_cycles=max_cycles,
            limit_per_cycle=limit_per_cycle,
            limit_history=limit_history,
            dry_run=dry_run,
            skip_import_failed=skip_import_failed,
            skip_waiting=skip_waiting,
            products_dir=products_dir,
            write_session_artifacts=write_session_artifacts,
            write_stage_artifacts=write_stage_artifacts,
            check_stop_sentinel=check_autonomous_stop_sentinel,
            allow_promotion=allow_promotion,
            promotion_include_bootstrap=promotion_include_bootstrap,
        )

        t1 = _utc_now()
        out["last_run_finished_at_utc"] = _iso(t1)
        out["last_session_id"] = last_pl.get("session_id")
        out["last_autonomous_stop_reason"] = last_pl.get("stop_reason")
        out["last_run_summary"] = _summarize_session(last_pl)
        out["loop_count"] = int(out.get("loop_count") or 0) + 1

        if write_service_artifacts:
            persist_transition(f"session_{session_index:03d}_complete")

        # Autonomous session ended with a "hard" stop — surface as service stop (optional)
        ar = str(last_pl.get("stop_reason") or "")
        if ar in (
            "portfolio_refresh_failed",
            "portfolio_cycle_failed",
            "portfolio_lifecycle_failed",
            "operator_summary_failed",
            "operator_narrative_failed",
        ):
            stop_reason = STOP_AUTONOMOUS_SESSION
            stop_codes.append(f"runner_service.stop.after_autonomous.{ar}")
            out["stop_reason"] = stop_reason
            out["stop_reason_codes"] = list(stop_codes)
            persist(STATUS_STOPPED)
            break

        if one_shot:
            stop_reason = STOP_COMPLETED
            stop_codes.append("runner_service.stop.one_shot")
            break

        if session_index >= effective_max_runs:
            stop_reason = STOP_MAX_RUNS
            stop_codes.append("runner_service.stop.max_runs_reached")
            break

        if sentinel_present():
            stop_reason = STOP_SENTINEL
            stop_codes.append("runner_service.stop.sentinel_file")
            break

        next_at = t1 + timedelta(seconds=float(interval_seconds))
        out["next_planned_run_at_utc"] = _iso(next_at)
        persist(STATUS_SLEEPING)
        delay = max(0.0, (next_at - _utc_now()).total_seconds())
        sleep(delay)

        if sentinel_present():
            stop_reason = STOP_SENTINEL
            stop_codes.append("runner_service.stop.sentinel_file_after_sleep")
            break

    if not stop_reason:
        if one_shot:
            stop_reason = STOP_COMPLETED
            stop_codes.append("runner_service.stop.one_shot")
        else:
            stop_reason = STOP_MAX_RUNS
            stop_codes.append("runner_service.stop.max_runs_reached")

    out["stop_reason"] = stop_reason
    out["stop_reason_codes"] = list(stop_codes)
    out["current_status"] = STATUS_STOPPED
    out["service_finished_at_utc"] = _iso(_utc_now())
    out["updated_at_utc"] = out["service_finished_at_utc"]
    out["next_planned_run_at_utc"] = None

    if write_service_artifacts:
        _attach_artifact_coherence(out, root)
        _attach_substrate_policy_state(out, root)
        write_runner_service_artifacts(root, out, transition_stamp="stop")
        # final latest already written by transition_stamp stop

    _attach_artifact_coherence(out, root)
    _attach_substrate_policy_state(out, root)

    final: dict[str, Any] = {
        "schema": PORTFOLIO_RUNNER_SERVICE_SCHEMA,
        "service_run_id": service_run_id,
        "service_started_at_utc": started_iso,
        "service_finished_at_utc": out.get("service_finished_at_utc"),
        "updated_at_utc": out.get("updated_at_utc"),
        "current_status": STATUS_STOPPED,
        "last_run_started_at_utc": out.get("last_run_started_at_utc"),
        "last_run_finished_at_utc": out.get("last_run_finished_at_utc"),
        "last_session_id": out.get("last_session_id"),
        "last_autonomous_stop_reason": out.get("last_autonomous_stop_reason"),
        "next_planned_run_at_utc": None,
        "loop_count": out.get("loop_count") or 0,
        "stop_reason": stop_reason,
        "stop_reason_codes": list(stop_codes),
        "last_run_summary": out.get("last_run_summary") or "",
        "inputs": inputs,
        "artifact_coherence": out.get("artifact_coherence") or {"present": False},
        "substrate_policy_state": out.get("substrate_policy_state")
        or {"schema": None, "consecutive_degraded_sessions": 0},
    }
    if include_autonomous_session_payload and last_pl is not None:
        final["last_autonomous_session"] = last_pl
    return final


__all__ = [
    "PORTFOLIO_RUNNER_SERVICE_SCHEMA",
    "STATUS_IDLE",
    "STATUS_RUNNING",
    "STATUS_SLEEPING",
    "STATUS_STARTING",
    "STATUS_STOPPED",
    "STOP_AUTONOMOUS_SESSION",
    "STOP_COMPLETED",
    "STOP_MAX_RUNS",
    "STOP_SENTINEL",
    "portfolio_runner_service_dir",
    "render_runner_service_markdown",
    "run_portfolio_runner_service",
    "runner_service_stop_sentinel_path",
    "write_runner_service_artifacts",
]
