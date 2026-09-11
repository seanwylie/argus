"""
Bridge: durable execution artifacts under ``runs/execution/<product_id>/`` → :class:`SignalRecord`.

Real-world outcomes are labeled for the learning pipeline (``provenance`` / not synthetic).

**Payload conventions**

- ``outcome_direction`` — only ``success`` or ``failure`` (derived from the artifact).
- ``outcome_magnitude`` — deterministic: if ``duration_seconds`` / ``execution_duration`` is
  present and ``> 0``, that value (rounded); else ``1.0`` when ``success`` else ``0.0``.
  (Magnitude is informational for downstream rules; not a second success/failure signal.)
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import SeverityLevel, SignalType
from argus.core.models.signal import SignalRecord
from argus.signals.ids import new_signal_id

# Re-export for ``execution_apply`` and path joins.
EXECUTION_ROOT = Path("runs") / "execution"

PROVENANCE_EXECUTION_OUTCOME = "execution_outcome"
ADAPTER_SOURCE = "execution"

def execution_outcome_signal_identity(product_id: str, execution_record_relpath: str) -> str:
    """
    Stable id for one execution JSON artifact within a repo (dedupe + inspection).

    Form: ``execution_outcome:<product_id>:<relpath>`` with forward slashes.
    """
    rel = str(execution_record_relpath or "").strip().replace("\\", "/")
    return f"execution_outcome:{product_id}:{rel}"


def _is_execution_outcome_signal(r: SignalRecord) -> bool:
    if r.signal_type != SignalType.EXECUTION:
        return False
    p = r.payload if isinstance(r.payload, dict) else {}
    if p.get("provenance") != PROVENANCE_EXECUTION_OUTCOME:
        return False
    return bool(str(p.get("execution_record_relpath") or "").strip())


def dedupe_execution_outcome_signals(records: list[SignalRecord]) -> list[SignalRecord]:
    """
    Collapse duplicate execution-outcome rows (same artifact identity) deterministically.

    Overlap can occur when the classic execution adapter and the optional adapter-layer
    integration both emit from ``generate_signals_from_execution_outcomes``. Non–execution-outcome
    rows are unchanged.

    For each duplicate group, the kept row prefers the record **without** the ``adapter_layer``
    tag, then the lexicographically smallest :attr:`SignalRecord.id`. The kept payload may gain
    ``execution_outcome_dedupe_collapsed_count`` and ``execution_outcome_dedupe_superseded_ids``.
    """
    if not records:
        return []

    exec_indices: list[int] = []
    for i, r in enumerate(records):
        if _is_execution_outcome_signal(r):
            exec_indices.append(i)

    if len(exec_indices) <= 1:
        return list(records)

    groups: dict[str, list[int]] = {}
    for i in exec_indices:
        r = records[i]
        p = r.payload if isinstance(r.payload, dict) else {}
        pid = str(r.product_id)
        rel = str(p.get("execution_record_relpath") or "").strip()
        key = execution_outcome_signal_identity(pid, rel)
        groups.setdefault(key, []).append(i)

    to_remove: set[int] = set()
    replacements: dict[int, SignalRecord] = {}

    for _key, idxs in sorted(groups.items()):
        if len(idxs) <= 1:
            continue

        def sort_key(i: int) -> tuple[int, str]:
            r = records[i]
            layer = 1 if "adapter_layer" in (r.tags or []) else 0
            return (layer, r.id)

        idxs_sorted = sorted(idxs, key=sort_key)
        keep_i = idxs_sorted[0]
        drop_is = idxs_sorted[1:]
        to_remove.update(drop_is)

        kept = records[keep_i]
        p0 = dict(kept.payload) if isinstance(kept.payload, dict) else {}
        p0["execution_outcome_dedupe_collapsed_count"] = len(drop_is)
        p0["execution_outcome_dedupe_superseded_ids"] = sorted(
            str(records[i].id) for i in drop_is
        )
        replacements[keep_i] = replace(kept, payload=p0)

    out: list[SignalRecord] = []
    for i, r in enumerate(records):
        if i in to_remove:
            continue
        out.append(replacements.get(i, r))
    return out


def _parse_execution_artifact(path: Path, repo_root: Path, product_id: str) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    pid = raw.get("product_id")
    if pid is not None and str(pid) != product_id:
        return None

    err_raw = raw.get("error") if raw.get("error") is not None else raw.get("execution_error")
    if isinstance(err_raw, str) and err_raw.strip():
        error_s: str | None = err_raw.strip()
    else:
        error_s = None

    success = raw.get("success")
    if success is None:
        success = error_s is None and raw.get("execution_failure") is not True
    success = bool(success)

    dur = raw.get("duration_seconds")
    if dur is None:
        dur = raw.get("execution_duration")
    try:
        duration = float(dur) if dur is not None else 0.0
    except (TypeError, ValueError):
        duration = 0.0

    finished = raw.get("finished_at_utc") or raw.get("observed_at_utc")
    observed: datetime | None = None
    if isinstance(finished, str) and finished.strip():
        try:
            observed = datetime.fromisoformat(finished.replace("Z", "+00:00"))
        except ValueError:
            observed = None
    if observed is None:
        try:
            observed = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            observed = datetime.now(timezone.utc)

    rel = str(path.resolve().relative_to(repo_root.resolve())).replace("\\", "/")
    eid = raw.get("experiment_id")
    aid = raw.get("action_id")
    artifact_prov = raw.get("provenance")

    out: dict[str, Any] = {
        "execution_success": success,
        "execution_failure": not success,
        "execution_duration": round(duration, 6),
        "execution_error": error_s,
        "execution_record_relpath": rel,
        "run_id": raw.get("run_id"),
        "command": raw.get("command"),
        "schema": raw.get("schema", "argus.execution_record.v1"),
        "_observed_at": observed,
        # Learning-pipeline labeling (audit-friendly; not synthetic findings).
        "provenance": PROVENANCE_EXECUTION_OUTCOME,
        "synthetic": False,
        "outcome_direction": "success" if success else "failure",
        "outcome_magnitude": round(duration, 6) if duration > 0 else (1.0 if success else 0.0),
        "observed_at_utc": observed.isoformat(),
    }
    if eid is not None and str(eid).strip():
        out["experiment_id"] = str(eid).strip()
    if aid is not None and str(aid).strip():
        out["action_id"] = str(aid).strip()
    ed = raw.get("execution_detail")
    if isinstance(ed, dict):
        out["execution_detail"] = ed
    oas = raw.get("orchestration_action_status")
    if oas is not None and str(oas).strip():
        out["orchestration_action_status"] = str(oas).strip()
    if isinstance(artifact_prov, dict):
        out["artifact_provenance"] = artifact_prov
    return out


def generate_signals_from_execution_outcomes(repo_root: Path, product_id: str) -> list[SignalRecord]:
    """
    One :class:`~argus.core.models.signal.SignalRecord` per ``*.json`` under
    ``runs/execution/<product_id>/`` (deterministic sort by path).
    """
    repo = repo_root.resolve()
    base = repo / EXECUTION_ROOT / product_id
    if not base.is_dir():
        return []

    out: list[SignalRecord] = []
    for path in sorted(base.glob("*.json")):
        parsed = _parse_execution_artifact(path, repo, product_id)
        if parsed is None:
            continue
        observed = parsed.pop("_observed_at")
        assert isinstance(observed, datetime)
        sev = SeverityLevel.HIGH if parsed["execution_failure"] else SeverityLevel.INFO
        rel = str(parsed.get("execution_record_relpath") or "").strip()
        eid = execution_outcome_signal_identity(product_id, rel)
        out.append(
            SignalRecord(
                id=new_signal_id(),
                product_id=product_id,
                signal_type=SignalType.EXECUTION,
                source=ADAPTER_SOURCE,
                observed_at=observed,
                payload={
                    **parsed,
                    "execution_outcome_id": eid,
                    # Back-compat alias (same value as ``execution_outcome_id``).
                    "signal_id_hint": eid,
                },
                severity_hint=sev,
                confidence=0.95,
                tags=["execution", "feedback", "execution_outcome"],
            )
        )
    return out
