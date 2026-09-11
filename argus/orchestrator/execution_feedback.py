"""Durable orchestration step outcomes under ``runs/execution/<product_id>/`` for signal observation."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.state_models import (
    ACTION_STATUS_EXECUTED,
    ACTION_STATUS_FAILED,
    ACTION_STATUS_QUEUED_UNHANDLED,
    ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA,
)

# Files matching this prefix are written only by the orchestration step executor.
_FEEDBACK_PREFIX = "orchestration_feedback_"


def _safe_filename_part(s: str) -> str:
    t = "".join(c if c.isalnum() or c in "-_" else "_" for c in s.strip())
    t = re.sub(r"_+", "_", t).strip("_")
    return (t[:120] if t else "action")


def write_orchestration_execution_feedback(
    repo_root: Path,
    product_id: str,
    action_id: str,
    result: dict[str, Any],
    *,
    completed_at_utc: str | None = None,
) -> Path:
    """
    Persist ``argus.orchestration_execution_feedback.v1`` next to other execution JSON.

    Execution outcome signals are emitted on ``signals collect`` via
    :func:`argus.signals.adapters.execution_outcomes.generate_signals_from_execution_outcomes`
    (wired through :class:`~argus.signals.adapters.execution.ExecutionAdapter`, always when
    files exist).
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    aid = str(action_id).strip()
    st = str(result.get("action_status") or "")
    detail = result.get("execution_detail")
    if not isinstance(detail, dict):
        detail = {}
    err = result.get("execution_error")
    err_s: str | None = None
    if err is not None and str(err).strip():
        err_s = str(err).strip()

    now = datetime.now(timezone.utc)
    ts = completed_at_utc.strip() if isinstance(completed_at_utc, str) and completed_at_utc.strip() else ""
    if not ts:
        ts = now.isoformat()

    success = st == ACTION_STATUS_EXECUTED

    out_dir = root / "runs" / "execution" / pid
    out_dir.mkdir(parents=True, exist_ok=True)
    compact = now.strftime("%Y%m%dT%H%M%SZ")
    fname = f"{_FEEDBACK_PREFIX}{_safe_filename_part(aid)}_{compact}.json"
    path = out_dir / fname
    rel = str(path.relative_to(root)).replace("\\", "/")

    body: dict[str, Any] = {
        "schema": ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA,
        "product_id": pid,
        "action_id": aid,
        "orchestration_action_status": st,
        "success": success,
        "finished_at_utc": ts,
        "observed_at_utc": ts,
        "execution_detail": detail,
        "execution_error": err_s,
        "provenance": {
            "source": "orchestration_step_executor",
            "kind": "in_process",
            "source_ref": rel,
        },
    }
    path.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return path


def is_orchestration_feedback_filename(name: str) -> bool:
    """True if ``name`` is a feedback artifact written by :func:`write_orchestration_execution_feedback`."""
    return name.startswith(_FEEDBACK_PREFIX) and name.endswith(".json")


def _parse_feedback_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    if raw.get("schema") != ORCHESTRATION_EXECUTION_FEEDBACK_SCHEMA:
        return None
    return raw


def _finished_at(raw: dict[str, Any], path: Path) -> datetime | None:
    fin = raw.get("finished_at_utc") or raw.get("observed_at_utc")
    if isinstance(fin, str) and fin.strip():
        try:
            return datetime.fromisoformat(fin.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return None


def _by_action_id_latest_in_window(
    rows_window: list[tuple[datetime, dict[str, Any], Path]],
    root: Path,
) -> dict[str, dict[str, Any]]:
    """Latest observation per ``action_id`` (max timestamp; tie-break by path string)."""
    groups: dict[str, list[tuple[datetime, dict[str, Any], Path]]] = defaultdict(list)
    for t, raw, path in rows_window:
        aid = str(raw.get("action_id") or "").strip()
        if not aid:
            continue
        groups[aid].append((t, raw, path))
    out: dict[str, dict[str, Any]] = {}
    for aid in sorted(groups.keys()):
        items = groups[aid]
        items.sort(key=lambda x: (x[0], str(x[2])), reverse=True)
        _t, latest_raw, latest_path = items[0]
        err = latest_raw.get("execution_error")
        err_short = str(err).strip()[:500] if err is not None and str(err).strip() else None
        row: dict[str, Any] = {
            "orchestration_action_status": str(latest_raw.get("orchestration_action_status") or ""),
            "finished_at_utc": latest_raw.get("finished_at_utc") or latest_raw.get("observed_at_utc"),
            "execution_error": err_short,
        }
        try:
            row["path_repo_relative"] = str(latest_path.relative_to(root)).replace("\\", "/")
        except ValueError:
            row["path_repo_relative"] = str(latest_path)
        out[aid] = row
    return out


def _per_action_outcome_aggregates(
    rows_window: list[tuple[datetime, dict[str, Any], Path]],
) -> tuple[dict[str, int], dict[str, int], dict[str, str]]:
    """
    Within the lookback window only: fail / queued_unhandled counts per ``action_id``,
    and latest ``finished_at`` among **executed** rows per ``action_id`` (by event time ``t``).
    """
    fail_n: dict[str, int] = defaultdict(int)
    uh_n: dict[str, int] = defaultdict(int)
    last_exec_t: dict[str, datetime] = {}

    for t, raw, _path in rows_window:
        aid = str(raw.get("action_id") or "").strip()
        if not aid:
            continue
        st = str(raw.get("orchestration_action_status") or "")
        if st == ACTION_STATUS_FAILED:
            fail_n[aid] += 1
        elif st == ACTION_STATUS_QUEUED_UNHANDLED:
            uh_n[aid] += 1
        elif st == ACTION_STATUS_EXECUTED:
            prev = last_exec_t.get(aid)
            if prev is None or t > prev:
                last_exec_t[aid] = t

    fail_out = {k: fail_n[k] for k in sorted(fail_n)}
    uh_out = {k: uh_n[k] for k in sorted(uh_n)}
    # ISO timestamps for inspectable facts (deterministic ordering by action_id).
    success_out = {k: last_exec_t[k].isoformat() for k in sorted(last_exec_t)}
    return fail_out, uh_out, success_out


def load_orchestration_feedback_summary(
    repo_root: Path,
    product_id: str,
    *,
    now: datetime,
    lookback_hours: float = 72.0,
) -> dict[str, Any]:
    """
    Deterministic summary of recent ``orchestration_feedback_*.json`` for orchestration evaluation.

    ``by_action_id`` maps each ``action_id`` to its **latest** observation within the lookback
    window (newest ``finished_at_utc``; tie-break file path).

    Returns keys: ``latest`` (globally most recent), ``by_action_id``, ``failed_action_ids``,
    ``queued_unhandled_action_ids``, ``recent_failed``, ``recent_queued_unhandled``,
    ``recent_fail_count_by_action_id``, ``recent_queued_unhandled_count_by_action_id``,
    ``last_success_finished_at_utc_by_action_id`` (per-action aggregates within lookback),
    ``files_considered``.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    base = root / "runs" / "execution" / pid
    empty: dict[str, Any] = {
        "latest": None,
        "by_action_id": {},
        "failed_action_ids": [],
        "queued_unhandled_action_ids": [],
        "recent_failed": False,
        "recent_queued_unhandled": False,
        "recent_fail_count_by_action_id": {},
        "recent_queued_unhandled_count_by_action_id": {},
        "last_success_finished_at_utc_by_action_id": {},
        "files_considered": 0,
    }
    if not base.is_dir():
        return dict(empty)

    paths = [p for p in base.glob(f"{_FEEDBACK_PREFIX}*.json") if p.is_file()]
    if not paths:
        return dict(empty)

    rows: list[tuple[datetime, dict[str, Any], Path]] = []
    for path in paths:
        raw = _parse_feedback_file(path)
        if raw is None:
            continue
        t = _finished_at(raw, path)
        if t is None:
            continue
        rows.append((t, raw, path))

    if not rows:
        return dict(empty)

    rows.sort(key=lambda x: x[0], reverse=True)
    cutoff = now - timedelta(hours=lookback_hours)
    rows_window = [(t, r, p) for t, r, p in rows if t >= cutoff]
    by_action_id = _by_action_id_latest_in_window(rows_window, root) if rows_window else {}
    fail_counts, uh_counts, last_success = _per_action_outcome_aggregates(rows_window)

    failed_action_ids = sorted(
        a
        for a, v in by_action_id.items()
        if str(v.get("orchestration_action_status") or "") == ACTION_STATUS_FAILED
    )
    queued_unhandled_action_ids = sorted(
        a
        for a, v in by_action_id.items()
        if str(v.get("orchestration_action_status") or "") == ACTION_STATUS_QUEUED_UNHANDLED
    )
    recent_failed = bool(failed_action_ids)
    recent_queued_unhandled = bool(queued_unhandled_action_ids)

    latest_t, latest_raw, latest_path = rows[0]
    err = latest_raw.get("execution_error")
    err_short = str(err).strip()[:500] if err is not None and str(err).strip() else None
    prov = latest_raw.get("provenance")
    src_ref: str | None = None
    if isinstance(prov, dict):
        sr = prov.get("source_ref")
        if sr is not None and str(sr).strip():
            src_ref = str(sr).strip()
    latest_out: dict[str, Any] = {
        "action_id": str(latest_raw.get("action_id") or ""),
        "orchestration_action_status": str(latest_raw.get("orchestration_action_status") or ""),
        "finished_at_utc": latest_raw.get("finished_at_utc") or latest_raw.get("observed_at_utc"),
        "execution_error": err_short,
        "source_ref": src_ref,
    }
    try:
        latest_out["path_repo_relative"] = str(latest_path.relative_to(root)).replace("\\", "/")
    except ValueError:
        latest_out["path_repo_relative"] = str(latest_path)

    return {
        "latest": latest_out,
        "by_action_id": by_action_id,
        "failed_action_ids": failed_action_ids,
        "queued_unhandled_action_ids": queued_unhandled_action_ids,
        "recent_failed": recent_failed,
        "recent_queued_unhandled": recent_queued_unhandled,
        "recent_fail_count_by_action_id": fail_counts,
        "recent_queued_unhandled_count_by_action_id": uh_counts,
        "last_success_finished_at_utc_by_action_id": last_success,
        "files_considered": len(rows),
    }


__all__ = [
    "is_orchestration_feedback_filename",
    "load_orchestration_feedback_summary",
    "write_orchestration_execution_feedback",
]
