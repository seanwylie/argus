"""Filesystem persistence under ``runs/refinement/<session_id>/``."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json

_SESSION_ID_RE = re.compile(r"^ref_[0-9TZ_]+_[a-f0-9]{8}$")


def refinement_root(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "refinement"


def session_dir(repo_root: Path, session_id: str) -> Path:
    return refinement_root(repo_root) / session_id


def index_path(repo_root: Path) -> Path:
    return refinement_root(repo_root) / "index.json"


def ensure_session_layout(repo_root: Path, session_id: str) -> Path:
    d = session_dir(repo_root, session_id)
    for sub in ("drafts", "reviews", "reviews_in", "synthesis", "convergence", "outcomes"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")


def read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def update_index(repo_root: Path, entry: dict[str, Any]) -> None:
    p = index_path(repo_root)
    cur: dict[str, Any] = {"schema": "argus.refinement_index.v1", "sessions": []}
    if p.is_file():
        prev = read_json(p)
        if isinstance(prev, dict) and isinstance(prev.get("sessions"), list):
            cur["sessions"] = list(prev["sessions"])
    sessions: list[dict[str, Any]] = cur["sessions"]
    sid = str(entry.get("session_id", ""))
    replaced = False
    for i, row in enumerate(sessions):
        if isinstance(row, dict) and row.get("session_id") == sid:
            sessions[i] = {**row, **entry}
            replaced = True
            break
    if not replaced:
        sessions.append(entry)
    write_json(p, cur)


def list_session_entries(repo_root: Path) -> list[dict[str, Any]]:
    p = index_path(repo_root)
    if not p.is_file():
        return []
    raw = read_json(p)
    if not raw or not isinstance(raw.get("sessions"), list):
        return []
    return [x for x in raw["sessions"] if isinstance(x, dict)]


def valid_session_id(session_id: str) -> bool:
    return bool(_SESSION_ID_RE.match(session_id.strip()))


def outcome_path(repo_root: Path, session_id: str) -> Path:
    return session_dir(repo_root, session_id) / "outcomes" / "latest.json"
