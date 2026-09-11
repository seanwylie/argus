"""
Governance + governed execution helpers for the operator console (pure; no Streamlit).

Reads ``runs/policy/pending_approvals``, ``runs/policy/approval_grants``, and
``runs/execution/*/run.json`` — the same artifact families as CLI / core.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.project_permissions.approvals import load_approval_grants


def _repo(root: Path) -> Path:
    return root.resolve()


def scan_pending_approval_rows(repo_root: Path) -> list[dict[str, Any]]:
    """One row per pending approval JSON under ``runs/policy/pending_approvals/<product>/*.json``."""
    root = _repo(repo_root)
    pend = root / "runs" / "policy" / "pending_approvals"
    rows: list[dict[str, Any]] = []
    if not pend.is_dir():
        return rows
    for pd in sorted(pend.iterdir()):
        if not pd.is_dir():
            continue
        pid = pd.name
        for f in sorted(pd.glob("*.json")):
            try:
                raw = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(raw, dict):
                continue
            try:
                rel = str(f.relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = str(f)
            rows.append(
                {
                    "product_id": pid,
                    "rel_path": rel,
                    "file_name": f.name,
                    "phase1_policy_field": raw.get("phase1_policy_field"),
                    "orchestration_action_id": raw.get("orchestration_action_id"),
                    "execution_path": raw.get("execution_path"),
                    "reason_preview": str(raw.get("reason") or "")[:240],
                    "requested_at_utc": raw.get("requested_at_utc"),
                    "schema": raw.get("schema"),
                },
            )
    return rows


def count_active_grants(repo_root: Path, product_id: str) -> int:
    """Grants that are ``always``, or ``confirm_once`` without ``consumed_at_utc``."""
    data = load_approval_grants(repo_root, product_id)
    n = 0
    for g in data.get("grants") or []:
        if not isinstance(g, dict):
            continue
        k = g.get("kind")
        if k == "always":
            n += 1
        elif k == "confirm_once" and not g.get("consumed_at_utc"):
            n += 1
    return n


def classify_governed_run(raw: dict[str, Any]) -> str:
    """High-level outcome bucket for operator filtering."""
    st = str(raw.get("status") or "")
    ed = raw.get("execution_detail") if isinstance(raw.get("execution_detail"), dict) else {}
    pd = ed.get("phase1_permission_decision") if isinstance(ed.get("phase1_permission_decision"), dict) else {}
    agg = str(pd.get("aggregate_decision") or "")
    if st == "success":
        return "success"
    if st == "failed":
        return "failed_subprocess"
    if st == "blocked":
        if agg in (
            "refused",
            "blocked_pending_confirmation",
            "capability_mismatch",
            "error_invalid_policy",
            "error_missing_product",
        ):
            return agg
        if ed.get("execution_approval_blocked"):
            return "approval_blocked"
        if ed.get("autonomy_blocked"):
            return "autonomy_blocked"
        return "blocked_other"
    return st or "unknown"


def execution_run_table_row(repo_root: Path, run_id: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Flatten a ``run.json`` dict for tabular display."""
    ed = raw.get("execution_detail") if isinstance(raw.get("execution_detail"), dict) else {}
    pd = ed.get("phase1_permission_decision") if isinstance(ed.get("phase1_permission_decision"), dict) else {}
    return {
        "run_id": run_id,
        "product_id": raw.get("product_id"),
        "action_id": raw.get("action_id"),
        "status": raw.get("status"),
        "outcome": classify_governed_run(raw),
        "phase1_aggregate": pd.get("aggregate_decision"),
        "subprocess_launched": raw.get("subprocess_launched"),
        "exit_code": raw.get("exit_code"),
        "started_at": raw.get("started_at"),
        "finished_at": raw.get("finished_at"),
        "error_preview": str(raw.get("error_log") or "")[:120],
        "artifact": str((_repo(repo_root) / "runs" / "execution" / run_id / "run.json").as_posix()),
    }


def list_recent_governed_execution_runs(repo_root: Path, *, limit: int = 40) -> list[dict[str, Any]]:
    """Recent governed subprocess runs (newest ``run.json`` mtime first)."""
    root = _repo(repo_root)
    ex = root / "runs" / "execution"
    if not ex.is_dir():
        return []
    scored: list[tuple[float, str, dict[str, Any]]] = []
    for d in ex.iterdir():
        if not d.is_dir() or d.name.startswith("."):
            continue
        p = d / "run.json"
        if not p.is_file():
            continue
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(raw, dict):
            continue
        try:
            mt = p.stat().st_mtime
        except OSError:
            mt = 0.0
        scored.append((mt, d.name, raw))
    scored.sort(key=lambda x: x[0], reverse=True)
    out: list[dict[str, Any]] = []
    for _mt, rid, raw in scored[: max(1, limit)]:
        out.append(execution_run_table_row(repo_root, rid, raw))
    return out


def run_argus_cli(repo_root: Path, argv: list[str], *, timeout_s: float = 180.0) -> tuple[int, str, str]:
    """
    Thin wrapper: ``python -m argus <argv...>`` in ``repo_root`` (same as terminal operators use).

    Does not mutate governance; callers use this for refresh-style commands only.
    """
    import subprocess
    import sys

    cmd = [sys.executable, "-m", "argus", *argv]
    p = subprocess.run(
        cmd,
        cwd=str(_repo(repo_root)),
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    return p.returncode, p.stdout, p.stderr
