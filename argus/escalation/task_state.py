"""Explicit escalation eligibility / task snapshot (durable; separate from packet body)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.escalation.rules import TriggerMatch

TASK_SCHEMA = "argus.escalation_task.v1"


def tasks_latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "escalations" / "tasks" / "latest" / f"{product_id}.json"


def write_escalation_task_state(
    repo_root: Path,
    product_id: str,
    orchestration_payload: dict[str, Any],
    merged_matches: list[TriggerMatch],
    *,
    packet_written: bool | None,
) -> Path:
    """
    Persist machine-readable escalation context: orchestration triggers + merged rule ids.

    Written on every ``escalation generate`` (even when no packet is saved).
    """
    root = repo_root.resolve()
    triggers = orchestration_payload.get("escalation_triggers")
    if not isinstance(triggers, list):
        triggers = []
    codes: set[str] = set()
    for t in triggers:
        if isinstance(t, dict) and t.get("code"):
            codes.add(str(t["code"]))
    for m in merged_matches:
        codes.add(m.rule_id.split("@", 1)[0])

    payload: dict[str, Any] = {
        "schema": TASK_SCHEMA,
        "product_id": product_id,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "escalation_eligible": bool(orchestration_payload.get("escalation_eligible")),
        "orchestration_status": orchestration_payload.get("orchestration_status"),
        "orchestration_status_reason": orchestration_payload.get("orchestration_status_reason"),
        "escalation_triggers": [x for x in triggers if isinstance(x, dict)],
        "reason_codes": sorted(codes),
        "merged_trigger_rule_ids": [m.rule_id for m in merged_matches],
        "packet_written": packet_written,
    }
    path = tasks_latest_path(root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return path
