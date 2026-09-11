"""Filesystem storage for :class:`HumanInput` records."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, loads_json, to_jsonable
from argus.input.models import HumanInput


def _utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def new_input_id() -> str:
    return f"hin_{_utc_compact()}_{secrets.token_hex(4)}"


def global_input_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "input"


def product_input_dir(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "products" / product_id / "input"


def _path_for(repo_root: Path, inp: HumanInput) -> Path:
    if inp.scope == "global":
        return global_input_dir(repo_root) / f"{inp.id}.json"
    assert inp.product_id is not None
    return product_input_dir(repo_root, inp.product_id) / f"{inp.id}.json"


def save_input(repo_root: Path, inp: HumanInput) -> Path:
    """Write JSON; parent directories created as needed."""
    path = _path_for(repo_root, inp)
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = to_jsonable(inp)
    path.write_text(dumps_json(blob), encoding="utf-8")
    return path


def load_input(path: Path) -> HumanInput:
    raw = loads_json(path.read_text(encoding="utf-8"))
    return human_input_from_dict(raw)


def human_input_from_dict(d: dict[str, Any]) -> HumanInput:
    return HumanInput(
        id=str(d["id"]),
        scope=str(d["scope"]),  # type: ignore[arg-type]
        product_id=(None if d.get("product_id") in (None, "") else str(d["product_id"])),
        type=str(d["type"]),  # type: ignore[arg-type]
        content=str(d.get("content", "")),
        structured_fields=dict(d.get("structured_fields") or {}),
        created_at=str(d.get("created_at", "")),
        expires_at=(None if d.get("expires_at") in (None, "") else str(d["expires_at"])),
        priority_weight=float(d.get("priority_weight", 1.0)),
    )


def _iter_input_paths(repo_root: Path) -> list[Path]:
    root = repo_root.resolve()
    out: list[Path] = []
    g = global_input_dir(root)
    if g.is_dir():
        out.extend(sorted(g.glob("*.json")))
    products = root / "products"
    if products.is_dir():
        for child in sorted(products.iterdir()):
            if not child.is_dir():
                continue
            pdir = child / "input"
            if pdir.is_dir():
                out.extend(sorted(pdir.glob("*.json")))
    return out


def list_inputs(repo_root: Path) -> list[HumanInput]:
    """All stored inputs (may include expired)."""
    rows: list[HumanInput] = []
    for p in _iter_input_paths(repo_root):
        try:
            rows.append(load_input(p))
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            continue
    rows.sort(key=lambda x: (x.created_at, x.id))
    return rows


def find_input_path(repo_root: Path, input_id: str) -> Path | None:
    for p in _iter_input_paths(repo_root):
        if p.stem == input_id or p.name == f"{input_id}.json":
            return p
    return None


def remove_input(repo_root: Path, input_id: str) -> bool:
    p = find_input_path(repo_root, input_id)
    if p is None or not p.is_file():
        return False
    p.unlink()
    return True
