"""Bridge orchestration ``escalation_triggers`` to :class:`TriggerMatch` (deterministic, no LLM)."""

from __future__ import annotations

import json
from typing import Any

from argus.escalation.rules import TriggerMatch


def orchestration_trigger_to_match(trigger: dict[str, Any]) -> TriggerMatch:
    """
    Stable ``rule_id``: ``<code>`` or ``<code>@<session_id>`` when ``session_id`` is present.

    ``detail`` is a sorted JSON object string for auditability.
    """
    if not isinstance(trigger, dict):
        return TriggerMatch(rule_id="orchestration_malformed_trigger", detail="not_a_mapping")
    code = str(trigger.get("code") or "unknown_code").strip() or "unknown_code"
    sid = trigger.get("session_id")
    if sid is not None and str(sid).strip():
        rule_id = f"{code}@{str(sid).strip()}"
    else:
        rule_id = code
    detail = json.dumps(trigger, sort_keys=True, separators=(",", ":"))
    return TriggerMatch(rule_id=rule_id, detail=detail)


def matches_from_orchestration_triggers(triggers: list[dict[str, Any]] | None) -> list[TriggerMatch]:
    if not triggers:
        return []
    out: list[TriggerMatch] = []
    for t in triggers:
        if isinstance(t, dict):
            out.append(orchestration_trigger_to_match(t))
    # Deterministic order: code then session
    return sorted(out, key=lambda m: m.rule_id)


def merge_trigger_matches(primary: list[TriggerMatch], secondary: list[TriggerMatch]) -> list[TriggerMatch]:
    """Union with first-wins dedupe on ``rule_id`` (primary first)."""
    seen: set[str] = set()
    out: list[TriggerMatch] = []
    for m in primary + secondary:
        if m.rule_id in seen:
            continue
        seen.add(m.rule_id)
        out.append(m)
    return out
