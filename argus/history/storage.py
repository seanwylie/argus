"""Filesystem layout and I/O for portfolio snapshots under ``runs/history/``."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, to_jsonable
from argus.history.models import PortfolioSnapshot, portfolio_snapshot_from_dict


def history_root(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "history"


def snapshots_base(repo_root: Path) -> Path:
    return history_root(repo_root) / "snapshots"


def latest_manifest_path(repo_root: Path) -> Path:
    return history_root(repo_root) / "latest.json"


def sanitize_label(label: str) -> str:
    s = label.strip()
    if not s:
        return ""
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s).strip("_") or "label"


def snapshot_dir(repo_root: Path, snapshot_id: str) -> Path:
    return snapshots_base(repo_root) / snapshot_id


def snapshot_file_path(repo_root: Path, snapshot_id: str) -> Path:
    return snapshot_dir(repo_root, snapshot_id) / "snapshot.json"


def write_snapshot_json(
    repo_root: Path,
    snapshot: PortfolioSnapshot,
    *,
    extra_copy_path: Path | None = None,
) -> Path:
    """
    Persist ``snapshot`` to ``runs/history/snapshots/<id>/snapshot.json``,
    update ``runs/history/latest.json``, and optionally duplicate JSON to ``extra_copy_path``.
    """
    root = repo_root.resolve()
    sid = snapshot.snapshot_id
    d = snapshot_dir(root, sid)
    d.mkdir(parents=True, exist_ok=True)
    out = d / "snapshot.json"
    payload = to_jsonable(snapshot)
    out.write_text(dumps_json(payload), encoding="utf-8")

    rel = out.relative_to(root).as_posix()
    manifest = {
        "schema": "argus.history.latest_manifest.v1",
        "snapshot_id": sid,
        "relative_path": rel,
        "observed_at_utc": snapshot.observed_at_utc,
        "label": snapshot.label,
    }
    lp = latest_manifest_path(root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(dumps_json(manifest), encoding="utf-8")

    if extra_copy_path is not None:
        ep = extra_copy_path.expanduser().resolve()
        if ep.is_dir():
            ep = ep / f"{sid}.json"
        ep.parent.mkdir(parents=True, exist_ok=True)
        ep.write_text(dumps_json(payload), encoding="utf-8")

    return out


def load_snapshot_file(path: Path) -> PortfolioSnapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("snapshot file must be a JSON object")
    return portfolio_snapshot_from_dict(data)


def load_latest_manifest(repo_root: Path) -> dict[str, Any] | None:
    p = latest_manifest_path(repo_root.resolve())
    if not p.is_file():
        return None
    data = json.loads(p.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


def load_snapshot_by_id(repo_root: Path, snapshot_id: str) -> PortfolioSnapshot:
    p = snapshot_file_path(repo_root, snapshot_id)
    if not p.is_file():
        raise FileNotFoundError(f"snapshot not found: {p}")
    return load_snapshot_file(p)


def iter_snapshot_dirs(repo_root: Path) -> list[Path]:
    """Newest-first by directory name (timestamp prefix sorts lexicographically)."""
    base = snapshots_base(repo_root.resolve())
    if not base.is_dir():
        return []
    dirs = [p for p in base.iterdir() if p.is_dir() and (p / "snapshot.json").is_file()]
    return sorted(dirs, key=lambda p: p.name, reverse=True)


def resolve_snapshot_ref(repo_root: Path, ref: str) -> Path:
    """
    Resolve CLI ref to a ``snapshot.json`` path.

    - Absolute or relative path ending in ``.json`` → use as file path
    - Otherwise treat as ``snapshot_id`` under ``runs/history/snapshots/<id>/snapshot.json``
    """
    root = repo_root.resolve()
    r = ref.strip()
    if not r:
        raise ValueError("empty snapshot reference")
    p = Path(r).expanduser()
    if p.suffix.lower() == ".json" or "/" in r or r.startswith("."):
        cand = p if p.is_absolute() else (root / p).resolve()
        if not cand.is_file():
            raise FileNotFoundError(f"snapshot file not found: {cand}")
        return cand
    sp = snapshot_file_path(root, r)
    if not sp.is_file():
        raise FileNotFoundError(f"snapshot id not found: {r!r} ({sp})")
    return sp
