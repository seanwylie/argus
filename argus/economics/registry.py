"""Load and merge resource registry + cost ingest files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.core.serialize import loads_json
from argus.economics.resource_models import ResourceEntry

CONFIG_REGISTRY = Path("config") / "economics" / "resources.json"
RUNS_REGISTRY = Path("runs") / "economics" / "resource_registry.json"
COST_INGEST = Path("runs") / "economics" / "cost_ingest.json"


def _num(x: Any) -> float:
    if x is None:
        return 0.0
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _entry_from_dict(d: dict[str, Any], *, source: str) -> ResourceEntry | None:
    rid = d.get("id")
    if not isinstance(rid, str) or not rid.strip():
        return None
    kind = d.get("kind") or d.get("type") or "generic"
    cost = _num(d.get("monthly_cost_usd") if d.get("monthly_cost_usd") is not None else d.get("cost_usd"))
    pid = d.get("product_id")
    if pid is not None and str(pid).strip() == "":
        pid = None
    if pid is not None:
        pid = str(pid).strip()
    labels = d.get("labels") if isinstance(d.get("labels"), dict) else {}
    if pid is None and isinstance(labels, dict):
        lp = labels.get("product_id") or labels.get("product")
        if lp is not None and str(lp).strip():
            pid = str(lp).strip()
    name = str(d.get("name") or d.get("display_name") or "")
    tags: list[str] = []
    raw_tags = d.get("tags")
    if isinstance(raw_tags, list):
        tags = [str(t) for t in raw_tags if str(t).strip()]
    return ResourceEntry(
        id=rid.strip(),
        kind=str(kind),
        monthly_cost_usd=cost,
        product_id=pid,
        name=name,
        tags=tags,
        source=source,
        metadata={k: v for k, v in d.items() if k not in ("id", "kind", "type", "monthly_cost_usd", "cost_usd", "product_id", "name", "display_name", "tags", "labels")},
    )


def _load_resource_list(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = loads_json(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return []
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    if isinstance(raw, dict):
        items = raw.get("resources")
        if isinstance(items, list):
            return [x for x in items if isinstance(x, dict)]
    return []


def load_merged_resources(repo_root: Path) -> dict[str, ResourceEntry]:
    """
    Merge resources from (in order; later overrides by id):
    ``config/economics/resources.json``, ``runs/economics/resource_registry.json``,
    ``runs/economics/cost_ingest.json``.
    """
    repo_root = repo_root.resolve()
    merged: dict[str, ResourceEntry] = {}

    def ingest(path: Path, source: str) -> None:
        for d in _load_resource_list(path):
            e = _entry_from_dict(d, source=source)
            if e is None:
                continue
            merged[e.id] = e

    ingest(repo_root / CONFIG_REGISTRY, "config")
    ingest(repo_root / RUNS_REGISTRY, "registry")
    ingest(repo_root / COST_INGEST, "ingest")
    return merged
