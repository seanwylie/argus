"""Load adapter instances from optional repo configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from argus.adapters.base import Adapter
from argus.adapters.registry import registered_adapters

CONFIG_REL = Path("config") / "adapters.json"

DEFAULT_ENABLED_IDS: tuple[str, ...] = ("execution", "filesystem", "metrics")


def default_config() -> dict[str, Any]:
    return {
        "schema": "argus.adapters_config.v1",
        "enabled_ids": list(DEFAULT_ENABLED_IDS),
        "signals_collect": {"merge_adapter_layer": False},
    }


def load_adapter_config(repo_root: Path) -> dict[str, Any]:
    """Read ``config/adapters.json`` or return defaults."""
    p = repo_root.resolve() / CONFIG_REL
    if not p.is_file():
        return default_config()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_config()
    if not isinstance(raw, dict):
        return default_config()
    base = default_config()
    if isinstance(raw.get("enabled_ids"), list):
        base["enabled_ids"] = [str(x).strip() for x in raw["enabled_ids"] if str(x).strip()]
    sc = raw.get("signals_collect")
    if isinstance(sc, dict) and isinstance(sc.get("merge_adapter_layer"), bool):
        base["signals_collect"] = {"merge_adapter_layer": sc["merge_adapter_layer"]}
    return base


def instantiate_adapters(
    *,
    enabled_ids: list[str] | None = None,
) -> list[Adapter]:
    """
    Build adapter instances for the given ids (or all registered if None).

    Unknown ids are skipped deterministically.
    """
    reg = registered_adapters()
    ids = enabled_ids if enabled_ids is not None else sorted(reg.keys())
    out: list[Adapter] = []
    for aid in sorted(ids):
        row = reg.get(aid)
        if row is None:
            continue
        _, cls = row
        out.append(cls())
    return out


def load_adapters_for_repo(repo_root: Path) -> tuple[list[Adapter], dict[str, Any]]:
    """Config + instantiated adapters for ``enabled_ids``."""
    cfg = load_adapter_config(repo_root)
    ids = cfg.get("enabled_ids") or list(DEFAULT_ENABLED_IDS)
    if not isinstance(ids, list):
        ids = list(DEFAULT_ENABLED_IDS)
    adapters = instantiate_adapters(enabled_ids=[str(x) for x in ids])
    return adapters, cfg
