"""Durable Phase 1 permission decision records under ``runs/policy/``."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json

PHASE1_PERMISSION_DECISION_SCHEMA = "argus.project_permission_decision.v1"


def _safe_slug(s: str, *, max_len: int = 80) -> str:
    t = "".join(c if c.isalnum() or c in "-_" else "_" for c in s.strip())
    t = re.sub(r"_+", "_", t).strip("_")
    if not t:
        t = "action"
    return t[:max_len]


def phase1_decisions_dir(repo_root: Path, product_id: str) -> Path:
    root = repo_root.resolve()
    pid = str(product_id).strip()
    d = root / "runs" / "policy" / "phase1_decisions" / pid
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_phase1_permission_decision(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    product_id: str,
    action_slug: str,
) -> Path:
    """
    Persist one ``argus.project_permission_decision.v1`` JSON under
    ``runs/policy/phase1_decisions/<product_id>/``.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    body = dict(payload)
    body.setdefault("schema", PHASE1_PERMISSION_DECISION_SCHEMA)
    if body.get("schema") != PHASE1_PERMISSION_DECISION_SCHEMA:
        raise ValueError(f"payload.schema must be {PHASE1_PERMISSION_DECISION_SCHEMA!r}")

    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y%m%dT%H%M%SZ")
    fname = f"phase1_{ts}_{_safe_slug(action_slug)}.json"
    out_dir = phase1_decisions_dir(root, pid)
    path = out_dir / fname
    try:
        rel = str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        rel = str(path)
    body.setdefault("artifact_path_repo_relative", rel)
    path.write_text(dumps_json(body) + "\n", encoding="utf-8")
    return path


__all__ = [
    "PHASE1_PERMISSION_DECISION_SCHEMA",
    "phase1_decisions_dir",
    "write_phase1_permission_decision",
]
