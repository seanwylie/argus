"""
Append-only local action records for the intervention inbox (acknowledge, snooze, resolve, ignore, escalate).

Deterministic: replay sorted files under ``runs/portfolio/intervention_inbox/actions/`` to merge state.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.intervention import portfolio_intervention_dir

INTERVENTION_INBOX_ACTION_SCHEMA = "argus.intervention_inbox_action.v1"

ACTION_ACKNOWLEDGE = "acknowledge"
ACTION_SNOOZE = "snooze"
ACTION_RESOLVE = "resolve"
ACTION_IGNORE = "ignore"
ACTION_ESCALATE = "escalate"

ACK_STATE_OPEN = "open"
ACK_STATE_ACKNOWLEDGED = "acknowledged"
ACK_STATE_SNOOZED = "snoozed"
ACK_STATE_RESOLVED = "resolved"
ACK_STATE_IGNORED = "ignored"
ACK_STATE_ESCALATED = "escalated"

def intervention_inbox_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "intervention_inbox"


def intervention_inbox_actions_dir(repo_root: Path) -> Path:
    return intervention_inbox_dir(repo_root) / "actions"


def fingerprint_for_intervention_row(row: dict[str, Any]) -> str:
    """Stable fingerprint for reopen / suppression logic (category + severity + reason codes)."""
    pid = str(row.get("product_id") or "").strip()
    cat = str(row.get("intervention_category") or "").strip()
    sev = str(row.get("severity") or "").strip()
    codes = sorted(str(c) for c in (row.get("detection_reason_codes") or []) if c)
    return f"{pid}|{cat}|{sev}|{','.join(codes)}"


def item_id_for_intervention_row(row: dict[str, Any]) -> str:
    """Stable id from product, category, and detection codes."""
    pid = str(row.get("product_id") or "").strip()
    cat = str(row.get("intervention_category") or "").strip()
    codes = sorted(str(c) for c in (row.get("detection_reason_codes") or []) if c)
    key = f"{pid}\0{cat}\0{','.join(codes)}"
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    return f"inv-{pid}-{h}"


def _slug_item_id(item_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", item_id.strip())
    return s[:120] if len(s) > 120 else s


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime | None:
    try:
        raw = s.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def list_intervention_action_files(repo_root: Path) -> list[Path]:
    d = intervention_inbox_actions_dir(repo_root)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and p.suffix == ".json"]
    return sorted(files, key=lambda p: p.name)


def load_intervention_action_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def merge_intervention_actions(repo_root: Path) -> dict[str, dict[str, Any]]:
    """
    Replay all action files in sorted order; last write per item_id wins for ack fields.
    Returns mapping item_id -> merged state (ack_state, snooze_until_utc, resolution_fingerprint, ...).
    """
    out: dict[str, dict[str, Any]] = {}
    for p in list_intervention_action_files(repo_root):
        rec = load_intervention_action_file(p)
        if not rec or rec.get("schema") != INTERVENTION_INBOX_ACTION_SCHEMA:
            continue
        iid = str(rec.get("item_id") or "").strip()
        if not iid:
            continue
        verb = str(rec.get("verb") or "").strip()
        at = str(rec.get("at_utc") or "").strip()
        cur = out.get(iid) or {
            "item_id": iid,
            "ack_state": ACK_STATE_OPEN,
        }
        cur["last_action_verb"] = verb
        cur["last_action_at_utc"] = at
        if verb == ACTION_ACKNOWLEDGE:
            cur["ack_state"] = ACK_STATE_ACKNOWLEDGED
            cur.pop("snooze_until_utc", None)
        elif verb == ACTION_SNOOZE:
            cur["ack_state"] = ACK_STATE_SNOOZED
            su = rec.get("snooze_until_utc")
            if isinstance(su, str) and su.strip():
                cur["snooze_until_utc"] = su.strip()
        elif verb == ACTION_RESOLVE:
            cur["ack_state"] = ACK_STATE_RESOLVED
            fp = rec.get("resolution_fingerprint")
            if isinstance(fp, str) and fp.strip():
                cur["resolution_fingerprint"] = fp.strip()
            cur.pop("snooze_until_utc", None)
        elif verb == ACTION_IGNORE:
            cur["ack_state"] = ACK_STATE_IGNORED
            fp = rec.get("resolution_fingerprint")
            if isinstance(fp, str) and fp.strip():
                cur["resolution_fingerprint"] = fp.strip()
            cur.pop("snooze_until_utc", None)
        elif verb == ACTION_ESCALATE:
            cur["ack_state"] = ACK_STATE_ESCALATED
            cur.pop("snooze_until_utc", None)
        note = rec.get("note")
        if isinstance(note, str) and note.strip():
            cur["last_note"] = note.strip()
        out[iid] = cur
    return out


def append_intervention_action(
    repo_root: Path,
    *,
    item_id: str,
    verb: str,
    at_utc: datetime | None = None,
    note: str | None = None,
    snooze_until_utc: str | None = None,
    resolution_fingerprint: str | None = None,
) -> Path:
    """Write one JSON action file; returns path."""
    root = repo_root.resolve()
    d = intervention_inbox_actions_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    ts = (at_utc or _utc_now()).strftime("%Y%m%dT%H%M%SZ")
    slug = _slug_item_id(item_id)
    name = f"{ts}__{verb}__{slug}.json"
    path = d / name
    payload: dict[str, Any] = {
        "schema": INTERVENTION_INBOX_ACTION_SCHEMA,
        "item_id": item_id,
        "verb": verb,
        "at_utc": (at_utc or _utc_now()).isoformat().replace("+00:00", "Z"),
    }
    if note:
        payload["note"] = note
    if snooze_until_utc:
        payload["snooze_until_utc"] = snooze_until_utc
    if resolution_fingerprint:
        payload["resolution_fingerprint"] = resolution_fingerprint
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return path


def snooze_until_iso(*, days: float, now: datetime | None = None) -> str:
    base = now or _utc_now()
    until = base + timedelta(days=float(days))
    return until.isoformat().replace("+00:00", "Z")


def is_snooze_active(state: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(state.get("ack_state") or "") != ACK_STATE_SNOOZED:
        return False
    su = state.get("snooze_until_utc")
    if not isinstance(su, str) or not su.strip():
        return False
    dt = _parse_iso(su)
    if dt is None:
        return False
    n = now or _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return n < dt


def intervention_report_path(repo_root: Path) -> Path:
    return portfolio_intervention_dir(repo_root) / "latest.json"


def load_latest_intervention_report(repo_root: Path) -> dict[str, Any] | None:
    p = intervention_report_path(repo_root)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def find_intervention_row_for_item_id(repo_root: Path, item_id: str) -> dict[str, Any] | None:
    rep = load_latest_intervention_report(repo_root)
    if not rep:
        return None
    for row in rep.get("flagged_products") or []:
        if not isinstance(row, dict):
            continue
        if item_id_for_intervention_row(row) == item_id:
            return row
    return None


def record_intervention_acknowledge(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    return append_intervention_action(repo_root, item_id=item_id, verb=ACTION_ACKNOWLEDGE, note=note)


def record_intervention_resolve(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    row = find_intervention_row_for_item_id(repo_root, item_id)
    if row is None:
        raise ValueError(
            f"no intervention row for item_id {item_id!r} in latest portfolio intervention report "
            f"({intervention_report_path(repo_root)})"
        )
    fp = fingerprint_for_intervention_row(row)
    return append_intervention_action(
        repo_root,
        item_id=item_id,
        verb=ACTION_RESOLVE,
        note=note,
        resolution_fingerprint=fp,
    )


def record_intervention_ignore(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    row = find_intervention_row_for_item_id(repo_root, item_id)
    if row is None:
        raise ValueError(
            f"no intervention row for item_id {item_id!r} in latest portfolio intervention report "
            f"({intervention_report_path(repo_root)})"
        )
    fp = fingerprint_for_intervention_row(row)
    return append_intervention_action(
        repo_root,
        item_id=item_id,
        verb=ACTION_IGNORE,
        note=note,
        resolution_fingerprint=fp,
    )


def record_intervention_snooze(
    repo_root: Path,
    item_id: str,
    *,
    days: float,
    note: str | None = None,
) -> Path:
    until = snooze_until_iso(days=days)
    return append_intervention_action(
        repo_root,
        item_id=item_id,
        verb=ACTION_SNOOZE,
        note=note,
        snooze_until_utc=until,
    )


def record_intervention_escalate(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    return append_intervention_action(repo_root, item_id=item_id, verb=ACTION_ESCALATE, note=note)
