"""
Builder: deterministic ``next_expansion.json`` from a product's ``content_catalog.json``.

Heuristic only — not a planner. Works for any product that declares a content catalog:
the catalog file and its schema are the opt-in, so no product identity is hardcoded.

A catalog is a list of **groups** (each with an ordinal) holding ordered **slots** (each with
an ordinal and a status). The heuristic is pure ordinal/status arithmetic and carries no
domain vocabulary of its own.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.next_expansion_prepare import next_expansion_path
from argus.core.serialize import dumps_json

CONTENT_CATALOG_SCHEMA = "argus.content_catalog.v1"
NEXT_EXPANSION_SCHEMA = "argus.next_expansion.v1"
GENERATOR_META_ID = "argus.content_catalog.next_expansion_heuristic.v1"

CONTENT_CATALOG_SCHEMAS_SUPPORTED = frozenset({CONTENT_CATALOG_SCHEMA})

CONTENT_CATALOG_RELPATH = "content/content_catalog.json"

#: Slot statuses treated as already built ("embodied") when ranking groups.
EMBODIED_STATUSES = ("seeded", "outlined")

#: Per-group landing page, relative to the product root. ``{ordinal}`` is zero-padded to 2.
GROUP_HUB_PATH_TEMPLATE = "app/site/group/group_{ordinal:02d}.html"


class NextExpansionGenerateError(ValueError):
    """Cannot derive a next expansion from current product state."""


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


def content_catalog_path(
    repo_root: Path, product_id: str, *, products_dir: Path | None = None
) -> Path:
    base = _products_base(repo_root, products_dir) / product_id
    return (base / CONTENT_CATALOG_RELPATH).resolve()


def _group_hub_path(
    repo_root: Path, product_id: str, group_ordinal: int, *, products_dir: Path | None
) -> Path:
    return (
        _products_base(repo_root, products_dir)
        / product_id
        / GROUP_HUB_PATH_TEMPLATE.format(ordinal=group_ordinal)
    )


def _load_content_catalog(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise NextExpansionGenerateError(f"Missing content catalog: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NextExpansionGenerateError(f"Invalid JSON in {path}: {e}") from e
    if not isinstance(raw, dict):
        raise NextExpansionGenerateError("content_catalog root must be an object")
    sch = str(raw.get("schema") or "")
    if sch not in CONTENT_CATALOG_SCHEMAS_SUPPORTED:
        raise NextExpansionGenerateError(
            f"Unsupported catalog schema {sch!r} (supported: {sorted(CONTENT_CATALOG_SCHEMAS_SUPPORTED)})"
        )
    groups = raw.get("groups")
    if not isinstance(groups, list) or not groups:
        raise NextExpansionGenerateError("content_catalog.groups must be a non-empty list")
    return raw


def _embodied_count(group: dict[str, Any]) -> int:
    slots = group.get("slots")
    if not isinstance(slots, list):
        return 0
    return sum(
        1
        for s in slots
        if isinstance(s, dict) and s.get("status") in EMBODIED_STATUSES
    )


def _group_sort_key(
    repo_root: Path,
    product_id: str,
    group: dict[str, Any],
    *,
    products_dir: Path | None,
) -> tuple[int, int, int]:
    """
    Higher is better for first two tuple elements when using reverse sort.

    Prefer: most embodied slots, then hub page present, then lower group ordinal (tie-break).
    """
    emb = _embodied_count(group)
    go = int(group.get("ordinal") or 999)
    hub = 1 if _group_hub_path(repo_root, product_id, go, products_dir=products_dir).is_file() else 0
    return (emb, hub, -go)


def _contiguous_chain_len_from_slot_one(slots: list[dict[str, Any]]) -> int:
    """How many slots starting at ordinal 1 are embodied (contiguous from the front)."""
    by_ord = sorted(
        [s for s in slots if isinstance(s, dict)],
        key=lambda s: int(s.get("ordinal") or 0),
    )
    if not by_ord:
        return 0
    # Re-index to 1..n ordinals present
    n = 0
    for s in by_ord:
        o = int(s.get("ordinal") or 0)
        if o != n + 1:
            break
        if s.get("status") not in EMBODIED_STATUSES:
            break
        n += 1
    return n


def _pick_next_planned_slot(group: dict[str, Any]) -> dict[str, Any] | None:
    slots = group.get("slots")
    if not isinstance(slots, list):
        return None
    dicts = [s for s in slots if isinstance(s, dict)]
    dicts.sort(key=lambda s: int(s.get("ordinal") or 0))
    i = 0
    while i < len(dicts) and dicts[i].get("status") in EMBODIED_STATUSES:
        i += 1
    if i < len(dicts) and dicts[i].get("status") == "planned":
        return dicts[i]
    return None


def _non_targets_for_group(
    chosen: dict[str, Any], all_groups: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Explicit non-targets, so the choice is legible rather than implicit."""
    out: list[dict[str, str]] = []
    chosen_ord = int(chosen.get("ordinal") or 1)
    others = [
        g
        for g in all_groups
        if isinstance(g, dict) and int(g.get("ordinal") or 0) != chosen_ord
    ]
    if not others:
        return out
    if chosen_ord == 1:
        nxt = min((int(g.get("ordinal") or 0) for g in others if int(g.get("ordinal") or 0) > 1), default=None)
        if nxt is not None:
            out.append(
                {
                    "id": f"group_{nxt:02d}_hub",
                    "reason": (
                        f"Group {nxt} is not yet as embodied as group 1 for this heuristic; "
                        "breadth (a new group hub) is secondary to finishing the strongest in-order spine."
                    ),
                }
            )
    else:
        out.append(
            {
                "id": "group_01_spine_depth",
                "reason": (
                    f"When expanding group {chosen_ord}, earlier groups may still have planned slots; "
                    "this generator picked the currently richest group — revisit the catalog if you "
                    "want strict lowest-group-first ordering."
                ),
            }
        )
    return out


def generate_next_expansion_payload(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Build an ``argus.next_expansion.v1`` payload from the product's content catalog.

    Any product that ships a catalog with a supported schema is eligible; the catalog is the
    opt-in, so no product identity is consulted.

    Heuristic:
    1. Choose the group with the highest (embodied slot count, hub file exists, lower ordinal wins ties).
    2. Within that group, walk slots in order from ordinal 1 while status is embodied.
    3. Target the first **planned** slot after that prefix (next sequential gap).
    """
    cat_path = content_catalog_path(repo_root, product_id, products_dir=products_dir)
    catalog = _load_content_catalog(cat_path)
    groups_raw = catalog.get("groups")
    assert isinstance(groups_raw, list)
    groups = [g for g in groups_raw if isinstance(g, dict)]

    ranked = sorted(
        groups,
        key=lambda g: _group_sort_key(
            repo_root, product_id, g, products_dir=products_dir
        ),
        reverse=True,
    )
    chosen = ranked[0]
    gid = str(chosen.get("id") or "")
    gtitle = str(chosen.get("title") or gid)
    go = int(chosen.get("ordinal") or 1)
    emb = _embodied_count(chosen)
    slots = chosen.get("slots")
    if not isinstance(slots, list):
        raise NextExpansionGenerateError("chosen group has no slots list")

    chain = _contiguous_chain_len_from_slot_one([s for s in slots if isinstance(s, dict)])
    nxt = _pick_next_planned_slot(chosen)
    if nxt is None:
        raise NextExpansionGenerateError(
            "No planned slot found after the contiguous embodied prefix "
            f"(group {gid!r} may be fully built in the catalog)."
        )

    sid = str(nxt.get("id") or "")
    so = int(nxt.get("ordinal") or 0)
    st = str(nxt.get("status") or "planned")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    confidence = "high" if emb >= 2 else "medium"

    hub_present = _group_hub_path(
        repo_root, product_id, go, products_dir=products_dir
    ).is_file()
    rationale = (
        f"Heuristic: group {go} ({gtitle}) currently has the strongest embodied spine in the catalog "
        f"({emb} embodied slot(s); hub page {'present' if hub_present else 'absent'}). "
        f"The contiguous chain from slot 1 has {chain} embodied slot(s); "
        f"the next in-order planned slot is {sid}."
    )

    basis = [
        f"Source: {cat_path.name} (schema {str(catalog.get('schema') or '')})",
        f"Chosen group: {gid} (ordinal {go})",
        f"Embodied slots in chosen group: {emb}",
        f"Contiguous embodied prefix from slot 1: {chain} slot(s)",
        f"Next planned slot: {sid} (ordinal {so})",
        f"Generator: {GENERATOR_META_ID}",
    ]

    payload: dict[str, Any] = {
        "schema": NEXT_EXPANSION_SCHEMA,
        "disclaimer": (
            "Heuristic priority from the content catalog + simple group/spine rules; "
            "not a commitment, backlog, or demand signal."
        ),
        "as_of_utc": now,
        "generator": {
            "id": GENERATOR_META_ID,
            "group_selection": "max_embodied_then_hub_then_lower_ordinal",
            "slot_selection": "first_planned_after_contiguous_embodied_prefix_from_slot_1",
        },
        "primary_target": {
            "target_type": "content_slot",
            "id": sid,
            "group_id": gid,
            "group_ordinal": go,
            "ordinal_in_group": so,
            "status_in_catalog": st,
            "rationale": rationale,
            "confidence": confidence,
            "basis": basis,
        },
        "explicit_non_targets": _non_targets_for_group(chosen, groups),
    }

    return payload


def write_next_expansion(
    repo_root: Path,
    product_id: str,
    payload: dict[str, Any],
    *,
    products_dir: Path | None = None,
) -> Path:
    out = next_expansion_path(repo_root, product_id, products_dir=products_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(dumps_json(payload), encoding="utf-8")
    return out


def generate_and_write_next_expansion(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
) -> tuple[dict[str, Any], Path]:
    payload = generate_next_expansion_payload(
        repo_root, product_id, products_dir=products_dir
    )
    path = write_next_expansion(repo_root, product_id, payload, products_dir=products_dir)
    return payload, path
