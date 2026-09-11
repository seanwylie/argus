"""
Apply ``runs/execution/<product>/*.json`` outcomes to experiments.

Execution records may include ``experiment_id`` and/or ``action_id``. Optional
``runs/experiments/action_to_experiment.json`` maps ``action_id`` → ``experiment_id``.

Deterministic rules:
- **Success**: ``proposed`` → ``active``; ``active`` → ``completed`` (sets ``end_at``).
- **Failure**: increments ``execution_failure_streak``; at **3** consecutive failures → ``failed``.
- Terminal experiments are not transitioned; last-execution metadata still updates.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, loads_json
from argus.experiments.models import Experiment, ExperimentStatus
from argus.experiments.registry import can_transition, is_terminal
from argus.experiments.store import experiments_dir, load_experiment, save_experiment
from argus.signals.adapters.execution import EXECUTION_ROOT

STATE_NAME = "_execution_apply_state.json"
ACTION_MAP_NAME = "action_to_experiment.json"
SCHEMA_STATE = "argus.execution_apply_state.v1"
FAILURE_THRESHOLD = 3


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _state_path(repo_root: Path) -> Path:
    return experiments_dir(repo_root) / STATE_NAME


def _action_map_path(repo_root: Path) -> Path:
    return experiments_dir(repo_root) / ACTION_MAP_NAME


def pending_unapplied_execution_outcomes_for_product(repo_root: Path, product_id: str) -> bool:
    """
    True when ``runs/execution/<product_id>/`` contains at least one non-feedback ``*.json`` not
    yet listed in apply state. Skips ``orchestration_feedback_*.json`` (step-executor audit rows;
    not experiment-apply inputs).
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    if not pid:
        return False
    processed = _load_processed(root)
    base = root / EXECUTION_ROOT / pid
    if not base.is_dir():
        return False
    for path in sorted(base.glob("*.json")):
        if path.name.startswith("_"):
            continue
        if path.name.startswith("orchestration_feedback_"):
            continue
        try:
            rel = str(path.resolve().relative_to(root)).replace("\\", "/")
        except ValueError:
            continue
        if rel not in processed:
            return True
    return False


def _load_processed(repo_root: Path) -> set[str]:
    p = _state_path(repo_root)
    if not p.is_file():
        return set()
    try:
        raw = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(raw, dict):
        return set()
    pr = raw.get("processed")
    if not isinstance(pr, list):
        return set()
    return {str(x) for x in pr}


def _save_processed(repo_root: Path, processed: set[str]) -> None:
    p = _state_path(repo_root)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema": SCHEMA_STATE, "processed": sorted(processed)}
    p.write_text(dumps_json(payload), encoding="utf-8")


def _load_action_map(repo_root: Path) -> dict[str, str]:
    p = _action_map_path(repo_root)
    if not p.is_file():
        return {}
    try:
        raw = loads_json(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def _list_execution_files(repo_root: Path, product_id: str | None = None) -> list[Path]:
    root = repo_root.resolve()
    if product_id is not None:
        pid = str(product_id).strip()
        if not pid:
            return []
        base = root / EXECUTION_ROOT / pid
        if not base.is_dir():
            return []
        out: list[Path] = []
        for p in sorted(base.glob("*.json")):
            if p.name.startswith("_"):
                continue
            try:
                p.relative_to(root)
            except ValueError:
                continue
            out.append(p)
        return out
    base = root / EXECUTION_ROOT
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob("*.json")):
        if p.name.startswith("_"):
            continue
        try:
            p.relative_to(root)
        except ValueError:
            continue
        out.append(p)
    return out


def _sort_key(repo_root: Path, path: Path) -> tuple[float, str]:
    root = repo_root.resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    ts = None
    if isinstance(raw, dict):
        fin = raw.get("finished_at_utc") or raw.get("observed_at_utc")
        if isinstance(fin, str) and fin.strip():
            try:
                dt = datetime.fromisoformat(fin.replace("Z", "+00:00"))
                ts = dt.timestamp()
            except ValueError:
                ts = None
    if ts is None:
        try:
            ts = path.stat().st_mtime
        except OSError:
            ts = 0.0
    rel = str(path.resolve().relative_to(root)).replace("\\", "/")
    return (ts, rel)


def resolve_experiment_id(raw: dict[str, Any], action_map: dict[str, str]) -> str | None:
    eid = raw.get("experiment_id")
    if eid is not None and str(eid).strip():
        return str(eid).strip()
    aid = raw.get("action_id")
    if aid is not None and str(aid).strip():
        return action_map.get(str(aid).strip())
    return None


def _apply_to_experiment(
    exp: Experiment,
    *,
    success: bool,
    run_id: str | None,
    action_id: str | None,
    observed_at: str,
) -> Experiment:
    """Return updated experiment (may be unchanged status if terminal)."""
    if is_terminal(exp.status):
        return replace(
            exp,
            last_execution_run_id=run_id,
            last_execution_action_id=action_id,
            last_execution_at_utc=observed_at,
            last_execution_success=success,
        )

    streak = exp.execution_failure_streak
    if success:
        streak = 0
    else:
        streak = streak + 1

    new_status = exp.status
    end_at = exp.end_at

    if success:
        if exp.status == ExperimentStatus.PROPOSED and can_transition(
            ExperimentStatus.PROPOSED,
            ExperimentStatus.ACTIVE,
        ):
            new_status = ExperimentStatus.ACTIVE
        elif exp.status == ExperimentStatus.ACTIVE and can_transition(
            ExperimentStatus.ACTIVE,
            ExperimentStatus.COMPLETED,
        ):
            new_status = ExperimentStatus.COMPLETED
            end_at = end_at or _utc_iso()
    else:
        if streak >= FAILURE_THRESHOLD and can_transition(exp.status, ExperimentStatus.FAILED):
            new_status = ExperimentStatus.FAILED
            end_at = end_at or _utc_iso()

    return replace(
        exp,
        status=new_status,
        end_at=end_at,
        last_execution_run_id=run_id,
        last_execution_action_id=action_id,
        last_execution_at_utc=observed_at,
        last_execution_success=success,
        execution_failure_streak=streak,
    )


@dataclass
class ExecutionApplyReport:
    """Summary of one ``apply_execution_outcomes`` run."""

    files_seen: int
    files_applied: int
    experiments_updated: list[str]
    skipped_no_link: int
    schema: str = "argus.execution_apply_report.v1"
    #: When set, only files under ``runs/execution/<product_id>/`` were considered.
    product_id: str | None = None


def apply_execution_outcomes(repo_root: Path, *, product_id: str | None = None) -> ExecutionApplyReport:
    """
    Process new execution JSON files (chronological), update linked experiments, extend processed set.

    When ``product_id`` is set, only files under ``runs/execution/<product_id>/`` are inspected
    (orchestration ``execution_outcomes_apply`` per product). When ``None``, all products'
    execution JSON files are considered (signals collect and other repo-wide hooks).
    """
    repo_root = repo_root.resolve()
    processed = _load_processed(repo_root)
    action_map = _load_action_map(repo_root)
    files = _list_execution_files(repo_root, product_id=product_id)
    files.sort(key=lambda p: _sort_key(repo_root, p))

    updated: list[str] = []
    applied = 0
    skipped = 0

    for path in files:
        rel = str(path.resolve().relative_to(repo_root)).replace("\\", "/")
        if rel in processed:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            processed.add(rel)
            _save_processed(repo_root, processed)
            continue
        if not isinstance(raw, dict):
            processed.add(rel)
            _save_processed(repo_root, processed)
            continue

        eid = resolve_experiment_id(raw, action_map)
        if not eid:
            skipped += 1
            processed.add(rel)
            _save_processed(repo_root, processed)
            continue

        success = raw.get("success")
        err = raw.get("error") or raw.get("execution_error")
        if isinstance(err, str) and err.strip():
            success = False
        elif success is None:
            success = True
        success = bool(success)

        run_id = raw.get("run_id")
        run_id_s = str(run_id) if run_id is not None and str(run_id).strip() else None
        action_id = raw.get("action_id")
        action_id_s = str(action_id) if action_id is not None and str(action_id).strip() else None

        fin = raw.get("finished_at_utc") or raw.get("observed_at_utc")
        if isinstance(fin, str) and fin.strip():
            observed_at = fin.strip()
        else:
            try:
                observed_at = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
            except OSError:
                observed_at = _utc_iso()

        try:
            exp = load_experiment(repo_root, eid)
        except FileNotFoundError:
            skipped += 1
            processed.add(rel)
            _save_processed(repo_root, processed)
            continue

        new_exp = _apply_to_experiment(
            exp,
            success=success,
            run_id=run_id_s,
            action_id=action_id_s,
            observed_at=observed_at,
        )
        save_experiment(repo_root, new_exp)
        updated.append(eid)
        applied += 1
        processed.add(rel)
        _save_processed(repo_root, processed)

    return ExecutionApplyReport(
        files_seen=len(files),
        files_applied=applied,
        experiments_updated=sorted(set(updated)),
        skipped_no_link=skipped,
        product_id=product_id,
    )
