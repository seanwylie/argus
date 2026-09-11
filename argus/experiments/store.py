"""Filesystem persistence under ``runs/experiments/``."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, loads_json, to_jsonable
from argus.experiments.models import Experiment, ExperimentStatus, ExperimentType


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_experiment_id() -> str:
    return f"exp_{_utc_compact()}_{secrets.token_hex(4)}"


def experiments_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "experiments"


def experiment_path(repo_root: Path, experiment_id: str) -> Path:
    return experiments_dir(repo_root) / f"{experiment_id}.json"


def experiment_from_dict(d: dict[str, Any]) -> Experiment:
    sm = d.get("success_metrics") or []
    if not isinstance(sm, list):
        sm = []
    end = d.get("end_at")
    return Experiment(
        id=str(d["id"]),
        product_id=str(d["product_id"]),
        hypothesis=str(d.get("hypothesis", "")),
        type=ExperimentType(str(d["type"])),
        description=str(d.get("description", "")),
        expected_outcome=str(d.get("expected_outcome", "")),
        success_metrics=[str(x) for x in sm],
        start_at=str(d.get("start_at", "")),
        end_at=(None if end in (None, "") else str(end)),
        status=ExperimentStatus(str(d.get("status", ExperimentStatus.PROPOSED.value))),
        confidence=float(d.get("confidence", 0.5)),
        created_at=str(d.get("created_at", "")),
        schema=str(d.get("schema", "argus.experiment.v1")),
        last_evaluation_verdict=(
            None if d.get("last_evaluation_verdict") in (None, "") else str(d["last_evaluation_verdict"])
        ),
        last_evaluation_at=(
            None if d.get("last_evaluation_at") in (None, "") else str(d["last_evaluation_at"])
        ),
        last_evaluation_summary=(
            None if d.get("last_evaluation_summary") in (None, "") else str(d["last_evaluation_summary"])
        ),
        last_execution_action_id=(
            None
            if d.get("last_execution_action_id") in (None, "")
            else str(d["last_execution_action_id"])
        ),
        last_execution_run_id=(
            None if d.get("last_execution_run_id") in (None, "") else str(d["last_execution_run_id"])
        ),
        last_execution_at_utc=(
            None if d.get("last_execution_at_utc") in (None, "") else str(d["last_execution_at_utc"])
        ),
        last_execution_success=(
            None if d.get("last_execution_success") is None else bool(d["last_execution_success"])
        ),
        execution_failure_streak=int(d.get("execution_failure_streak", 0)),
        source_proposal_id=(
            None if d.get("source_proposal_id") in (None, "") else str(d["source_proposal_id"])
        ),
    )


def save_experiment(repo_root: Path, exp: Experiment) -> Path:
    path = experiment_path(repo_root, exp.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(to_jsonable(exp)), encoding="utf-8")
    return path


def load_experiment(repo_root: Path, experiment_id: str) -> Experiment:
    p = experiment_path(repo_root, experiment_id)
    if not p.is_file():
        raise FileNotFoundError(experiment_id)
    raw = loads_json(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("experiment file must be a JSON object")
    return experiment_from_dict(raw)


def list_experiment_paths(repo_root: Path) -> list[Path]:
    d = experiments_dir(repo_root)
    if not d.is_dir():
        return []
    return sorted(d.glob("exp_*.json"))


def list_experiments(repo_root: Path, *, product_id: str | None = None) -> list[Experiment]:
    rows: list[Experiment] = []
    for p in list_experiment_paths(repo_root):
        try:
            raw = loads_json(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                continue
            exp = experiment_from_dict(raw)
            if product_id is not None and exp.product_id != product_id:
                continue
            rows.append(exp)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda e: (e.created_at, e.id))
    return rows


def find_experiment_path(repo_root: Path, experiment_id: str) -> Path | None:
    p = experiment_path(repo_root, experiment_id)
    return p if p.is_file() else None
