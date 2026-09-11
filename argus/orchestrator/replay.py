"""
Read-only forensic replay of orchestration decision context from stored artifacts.

Does not call :func:`~argus.orchestrator.eligibility.evaluate_product_orchestration` or mutate ``runs/``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision.persistence import latest_product_path as decisions_latest_path
from argus.findings.persistence import latest_path as findings_latest_path
from argus.orchestrator.artifact_paths import (
    orchestration_advancement_path,
    orchestration_latest_path,
    orchestration_progression_latest_path,
)
from argus.orchestrator.operator_snapshot import (
    OPERATOR_SNAPSHOT_SCHEMA,
    operator_snapshot_json_path,
)
from argus.signals.persistence import latest_path as signals_latest_path

ORCHESTRATION_REPLAY_SCHEMA = "argus.orchestration_replay.v1"
ORCHESTRATION_STATE_SCHEMA = "argus.orchestration_state.v1"


def orchestration_replay_dir(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root).resolve() / "runs" / "orchestration" / "replay" / product_id


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _rel(root: Path, path: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def _artifact_entry(root: Path, path: Path, role: str, **extra: Any) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    row: dict[str, Any] = {"path": _rel(root, path), "role": role, "present": True}
    row.update(extra)
    return row


def replay_orchestration_product(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Reconstruct explainable context from existing files only (read-only).
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    generated_at = datetime.now(timezone.utc).isoformat()

    state_path = orchestration_latest_path(root, pid)
    snap_path = operator_snapshot_json_path(root, pid)
    task_path = root / "runs" / "orchestration" / "tasks" / "latest" / f"{pid}.json"
    adv_path = orchestration_advancement_path(root, pid)
    prog_path = orchestration_progression_latest_path(root, pid)
    assess_path = root / "runs" / "decision_assessment" / "latest" / f"{pid}.json"

    state = _load_json(state_path)
    snapshot = _load_json(snap_path)
    task = _load_json(task_path)
    advancement = _load_json(adv_path)
    progression_run = _load_json(prog_path)
    assessment = _load_json(assess_path)

    product_yaml = root / "products" / pid / "product.yaml"
    sig_path = signals_latest_path(root, pid)
    fin_path = findings_latest_path(root, pid)
    dec_path = decisions_latest_path(root, pid)

    input_rows: list[dict[str, Any]] = []
    for p, role in (
        (product_yaml, "product_manifest"),
        (sig_path, "signals_latest"),
        (fin_path, "findings_latest"),
        (dec_path, "decisions_latest"),
        (assess_path, "decision_assessment_latest"),
    ):
        e = _artifact_entry(root, p, role)
        if e:
            input_rows.append(e)

    output_rows: list[dict[str, Any]] = []
    for p, role in (
        (state_path, "orchestration_state_latest"),
        (snap_path, "operator_snapshot"),
        (task_path, "orchestration_task_latest"),
        (adv_path, "orchestration_advancement_latest"),
        (prog_path, "orchestration_progression_run_latest"),
    ):
        e = _artifact_entry(root, p, role)
        if e:
            output_rows.append(e)

    reason_codes: list[str] = []
    if state and state.get("schema") == ORCHESTRATION_STATE_SCHEMA:
        rc = state.get("orchestration_status_reason_codes")
        if isinstance(rc, list):
            reason_codes.extend(str(x) for x in rc if str(x).strip())
        wi = state.get("waiting_inputs") or []
        if isinstance(wi, list):
            for w in wi:
                if isinstance(w, dict):
                    for c in w.get("reason_codes") or []:
                        if str(c).strip():
                            reason_codes.append(str(c))
    if task:
        for c in task.get("reason_codes") or []:
            if str(c).strip():
                reason_codes.append(str(c))
    reason_codes = sorted(set(reason_codes))

    orch_status = str(state.get("orchestration_status") or "") if state else ""
    next_action = str(state.get("next_action") or "") if state else ""
    readiness = state.get("readiness") if isinstance(state, dict) else None

    readiness_tier = None
    understanding_debt = None
    if isinstance(readiness, dict):
        readiness_tier = readiness.get("readiness_tier")
        understanding_debt = readiness.get("understanding_debt")

    snap_tier = None
    snap_next = None
    if snapshot and snapshot.get("schema") == OPERATOR_SNAPSHOT_SCHEMA:
        rd = snapshot.get("readiness") if isinstance(snapshot.get("readiness"), dict) else {}
        snap_tier = rd.get("readiness_tier")
        snap_next = snapshot.get("next_action")

    key_decisions: list[dict[str, Any]] = []
    if state:
        key_decisions.append(
            {
                "field": "orchestration_status",
                "value": orch_status,
                "source_artifact": _rel(root, state_path),
            }
        )
        key_decisions.append(
            {
                "field": "next_action",
                "value": next_action,
                "source_artifact": _rel(root, state_path),
            }
        )
        key_decisions.append(
            {
                "field": "readiness_tier",
                "value": readiness_tier,
                "source_artifact": _rel(root, state_path),
            }
        )
    elif snapshot:
        key_decisions.append(
            {
                "field": "next_action (snapshot only)",
                "value": snap_next,
                "source_artifact": _rel(root, snap_path),
            }
        )
        key_decisions.append(
            {
                "field": "readiness_tier (snapshot only)",
                "value": snap_tier,
                "source_artifact": _rel(root, snap_path),
            }
        )

    if not state and not snapshot:
        completeness = "none"
    elif state and snapshot:
        completeness = "full"
    else:
        completeness = "partial"

    why_parts: list[str] = []
    if not state and not snapshot:
        why_parts.append("No orchestration state or operator snapshot on disk for this product.")
    else:
        if orch_status:
            why_parts.append(f"Orchestration headline status is {orch_status!r} (from persisted state JSON).")
        if next_action and next_action.lower() != "none":
            why_parts.append(f"Next action is {next_action!r} — task/advancement artifacts may reflect queued work.")
        ih = state.get("import_health") if isinstance(state, dict) and isinstance(state.get("import_health"), dict) else {}
        if ih:
            fp = str(ih.get("first_pass_status") or "")
            gt = str(ih.get("gating_tier") or "")
            if fp or gt:
                why_parts.append(f"Import health (first_pass={fp!r}, gating={gt!r}) influenced eligibility.")
        blk = state.get("blockers") if isinstance(state, dict) else None
        if isinstance(blk, list) and blk:
            why_parts.append(f"{len(blk)} blocker(s) recorded — likely constrains next_action.")
        if reason_codes:
            why_parts.append(f"Reason codes recorded: {', '.join(reason_codes[:12])}{'…' if len(reason_codes) > 12 else ''}.")
    why = " ".join(why_parts) if why_parts else "Insufficient artifacts to infer posture."

    return {
        "schema": ORCHESTRATION_REPLAY_SCHEMA,
        "run_id": run_id,
        "generated_at_utc": generated_at,
        "product_id": pid,
        "read_only": True,
        "completeness": completeness,
        "inputs": {"artifacts": [x for x in input_rows if x]},
        "outputs": {"artifacts": [x for x in output_rows if x]},
        "summaries": {
            "orchestration_state": _excerpt_state(state),
            "operator_snapshot": _excerpt_snapshot(snapshot),
            "orchestration_task": _excerpt_small(task, ("schema", "task_type", "action", "reason_codes")),
            "advancement": _excerpt_small(advancement, ("schema", "action_status", "selected_action")),
            "progression_run": _excerpt_small(
                progression_run,
                ("schema", "run_id", "step_count", "steps_executed", "stopped_reason", "terminal_status"),
            ),
            "decision_assessment": _excerpt_small(
                assessment,
                ("schema", "product_id", "confidence_score", "confidence_bucket", "assessed_at_utc"),
            ),
        },
        "readiness_replay": {
            "from_state": {
                "readiness_tier": readiness_tier,
                "understanding_debt": understanding_debt,
            },
            "from_operator_snapshot": {
                "readiness_tier": snap_tier,
                "next_action": snap_next,
            },
        },
        "reason_codes_surfaced": reason_codes,
        "key_decision_points": key_decisions,
        "why_this_state_likely_occurred": why,
    }


def _excerpt_state(state: dict[str, Any] | None) -> dict[str, Any] | None:
    if not state:
        return None
    keys = (
        "schema",
        "evaluated_at_utc",
        "orchestration_status",
        "orchestration_status_reason",
        "orchestration_status_reason_codes",
        "next_action",
        "readiness_reason",
        "import_health",
    )
    return {k: state.get(k) for k in keys if k in state}


def _excerpt_snapshot(snap: dict[str, Any] | None) -> dict[str, Any] | None:
    if not snap:
        return None
    return {
        "schema": snap.get("schema"),
        "next_action": snap.get("next_action"),
        "readiness": snap.get("readiness"),
        "waiting_and_blocking": snap.get("waiting_and_blocking"),
        "import_health": snap.get("import_health"),
    }


def _excerpt_small(d: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any] | None:
    if not d:
        return None
    return {k: d.get(k) for k in keys if k in d}


def render_orchestration_replay_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Orchestration replay (read-only)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Product:** `{payload.get('product_id')}`",
        f"**Completeness:** `{payload.get('completeness')}`",
        f"**Generated (UTC):** {payload.get('generated_at_utc')}",
        "",
        "## Input artifacts",
        "",
    ]
    for a in payload.get("inputs", {}).get("artifacts") or []:
        lines.append(f"- `{a.get('path')}` — {a.get('role')}")
    if not (payload.get("inputs") or {}).get("artifacts"):
        lines.append("—")
    lines.extend(["", "## Output artifacts", ""])
    for a in payload.get("outputs", {}).get("artifacts") or []:
        lines.append(f"- `{a.get('path')}` — {a.get('role')}")
    if not (payload.get("outputs") or {}).get("artifacts"):
        lines.append("—")
    lines.extend(
        [
            "",
            "## Key decision points",
            "",
        ]
    )
    for k in payload.get("key_decision_points") or []:
        if isinstance(k, dict):
            lines.append(f"- **{k.get('field')}:** `{k.get('value')}` ← `{k.get('source_artifact')}`")
    if not payload.get("key_decision_points"):
        lines.append("—")
    rc = payload.get("reason_codes_surfaced") or []
    lines.extend(["", "## Reason codes surfaced", "", ", ".join(f"`{c}`" for c in rc) or "—", ""])
    lines.extend(
        [
            "## Why this state likely occurred",
            "",
            str(payload.get("why_this_state_likely_occurred") or "—"),
            "",
            "## Summaries (excerpts)",
            "",
            "```json",
            json.dumps(payload.get("summaries") or {}, indent=2, sort_keys=True)[:12000],
            "```",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def write_orchestration_replay_artifacts(
    repo_root: Path,
    product_id: str,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = orchestration_replay_dir(root, product_id)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_orchestration_replay_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_orchestration_replay(
    repo_root: Path,
    product_id: str,
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = replay_orchestration_product(repo_root, product_id)
    if write_artifacts:
        write_orchestration_replay_artifacts(repo_root, product_id, payload, run_id=payload.get("run_id"))
    return payload
