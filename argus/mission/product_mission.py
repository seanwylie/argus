"""
Structured product mission (``product.yaml``) — objective, drivers, guardrails, optional risk posture.

Legacy ``mission_id`` is normalized to ``objective`` only. Truth-producing layers do not import this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from argus.core.models.product_mission_spec import ProductMissionSpec

RISK_POSTURES = frozenset({"conservative", "moderate", "aggressive"})


def read_product_yaml(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    root = repo_root.resolve()
    pid = str(product_id).strip()
    if not pid:
        return None
    p = root / "products" / pid / "product.yaml"
    if not p.is_file():
        return None
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None
    return raw if isinstance(raw, dict) else None


def parse_product_mission_from_yaml(raw: dict[str, Any] | None) -> tuple[ProductMissionSpec | None, str | None]:
    """
    Parse ``mission`` / ``mission_id`` from a product.yaml mapping.

    Returns ``(spec, error)``. ``spec`` is None when the product declares no mission.
    **Error** when ``mission_id`` and ``mission`` are both present, or when shapes are invalid.
    """
    if not raw:
        return None, None
    mid_legacy = raw.get("mission_id")
    has_legacy = isinstance(mid_legacy, str) and mid_legacy.strip()
    block = raw.get("mission")
    has_block = isinstance(block, dict)

    if has_legacy and has_block:
        return None, "product.yaml: use either mission_id or mission, not both"

    if has_block:
        obj = block.get("objective")
        if not isinstance(obj, str) or not obj.strip():
            return None, "mission.objective must be a non-empty string when mission is set"
        objective = obj.strip()
        dr = block.get("drivers") or []
        gr = block.get("guardrails") or []
        if dr is not None and not isinstance(dr, list):
            return None, "mission.drivers must be a list when present"
        if gr is not None and not isinstance(gr, list):
            return None, "mission.guardrails must be a list when present"
        drivers: list[str] = []
        guardrails: list[str] = []
        for i, x in enumerate(dr):
            if not isinstance(x, str) or not x.strip():
                return None, f"mission.drivers[{i}] must be a non-empty string"
            drivers.append(x.strip())
        for i, x in enumerate(gr):
            if not isinstance(x, str) or not x.strip():
                return None, f"mission.guardrails[{i}] must be a non-empty string"
            guardrails.append(x.strip())
        rp_raw = block.get("risk_posture")
        rp: str | None = None
        if rp_raw is not None:
            if not isinstance(rp_raw, str) or not rp_raw.strip():
                return None, "mission.risk_posture must be a non-empty string when present"
            rp = rp_raw.strip().lower()
            if rp not in RISK_POSTURES:
                return None, f"mission.risk_posture must be one of {sorted(RISK_POSTURES)}, got {rp!r}"
        seen: set[str] = set()
        for pid in (objective, *drivers, *guardrails):
            if pid in seen:
                return None, f"duplicate mission profile id {pid!r} across objective/drivers/guardrails"
            seen.add(pid)
        return (
            ProductMissionSpec(
                objective=objective,
                drivers=tuple(drivers),
                guardrails=tuple(guardrails),
                risk_posture=rp,
            ),
            None,
        )

    if has_legacy:
        return ProductMissionSpec(objective=mid_legacy.strip(), drivers=(), guardrails=(), risk_posture=None), None

    return None, None


def validate_product_mission_registry(repo_root: Path, spec: ProductMissionSpec) -> str | None:
    """Return error string if any referenced profile id is unknown."""
    from argus.mission.mission import load_mission_by_id

    root = repo_root.resolve()
    for pid in (spec.objective, *spec.drivers, *spec.guardrails):
        try:
            load_mission_by_id(root, pid)
        except ValueError as e:
            return str(e)
    return None


def product_declares_mission(raw: dict[str, Any] | None) -> bool:
    if not raw or not isinstance(raw, dict):
        return False
    if isinstance(raw.get("mission_id"), str) and raw["mission_id"].strip():
        return True
    m = raw.get("mission")
    if isinstance(m, dict) and isinstance(m.get("objective"), str) and str(m["objective"]).strip():
        return True
    return False
