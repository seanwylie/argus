"""Manifest validation (YAML shape, enums, paths) before and after :class:`ProductNode` construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from argus.core.models.enums import LifecycleStage, SignalType
from argus.core.models.product import ProductNode
from argus.core.models.validation import ArgusValidationError, validate_product_node
from argus.core.serialize import product_node_from_dict, to_jsonable
from argus.mission.product_mission import (
    parse_product_mission_from_yaml,
    validate_product_mission_registry,
)
from argus.products.external_bindings import validate_external_bindings_block
from argus.products.paths import (
    ensure_posix_relative,
    join_under_product,
    looks_like_filesystem_command,
)
from argus.products.signal_manifest import load_product_signal_manifest

_LIFECYCLE_VALUES = ", ".join(s.value for s in LifecycleStage)
_SIGNAL_VALUES = ", ".join(s.value for s in SignalType)

# --- Canonical schema (see docs/product-model.md) ---
# Required top-level keys for a loadable product: id, owner (with team), lifecycle (with stage).
# Optional: name, type, status, state, metrics, cost, signals, actions, constraints, tags,
# raw_extensions (importer blocks: import_state; human: argus_onboarding, etc.), inline signal_manifest.

KNOWN_OPTIONAL_ROOT_KEYS = frozenset(
    {
        "name",
        "type",
        "status",
        "state",
        "metrics",
        "cost",
        "signals",
        "actions",
        "constraints",
        "lifecycle",
        "owner",
        "tags",
        "raw_extensions",
        "signal_manifest",
        "id",
        "mission_id",
        "mission",
    }
)


def _errors_import_state_block(ist: Any) -> list[str]:
    """Validate ``raw_extensions.import_state`` (importer-owned; schema ``argus.import_state.v1``)."""
    from argus.importer.import_state import IMPORT_STATE_SCHEMA

    if not isinstance(ist, dict):
        return ["raw_extensions.import_state must be a mapping when present"]
    sch = ist.get("schema")
    if sch != IMPORT_STATE_SCHEMA:
        return [
            "raw_extensions.import_state.schema must be "
            f"{IMPORT_STATE_SCHEMA!r} (managed by `argus importer`); got {sch!r}"
        ]
    return []


def _errors_top_level_shapes(data: dict[str, object], warnings: list[str]) -> list[str]:
    """Structural checks beyond :class:`ProductNode` construction."""
    errs: list[str] = []

    for key in ("cost", "metrics", "constraints"):
        val = data.get(key)
        if val is not None and not isinstance(val, dict):
            errs.append(f"{key} must be a mapping when present, got {type(val).__name__}")

    metrics = data.get("metrics")
    if isinstance(metrics, dict):
        for mk in ("local_paths", "primary"):
            mv = metrics.get(mk)
            if mv is None:
                continue
            if not isinstance(mv, list):
                errs.append(f"metrics.{mk} must be a list when present, got {type(mv).__name__}")
                continue
            for j, item in enumerate(mv):
                if not isinstance(item, str):
                    errs.append(f"metrics.{mk}[{j}] must be a string")

    tags = data.get("tags")
    if tags is not None:
        if not isinstance(tags, list):
            errs.append(f"tags must be a list when present, got {type(tags).__name__}")
        else:
            for i, t in enumerate(tags):
                if not isinstance(t, str):
                    errs.append(f"tags[{i}] must be a string")

    raw = data.get("raw_extensions")
    if raw is not None:
        if not isinstance(raw, dict):
            errs.append(f"raw_extensions must be a mapping when present, got {type(raw).__name__}")
        else:
            ist = raw.get("import_state")
            if ist is not None:
                errs.extend(_errors_import_state_block(ist))
            eb = raw.get("external_bindings")
            if eb is not None:
                eb_errs, eb_warns = validate_external_bindings_block(eb)
                errs.extend(eb_errs)
                warnings.extend(eb_warns)

    m = data.get("mission_id")
    if m is not None and not isinstance(m, str):
        errs.append("mission_id must be a string when present")
    miss = data.get("mission")
    if miss is not None and not isinstance(miss, dict):
        errs.append("mission must be a mapping when present")

    life = data.get("lifecycle")
    if isinstance(life, dict) and "next_gate" in life:
        ng = life.get("next_gate")
        if ng is not None and not str(ng).strip():
            errs.append("lifecycle.next_gate must be omitted or a non-empty string (got empty)")

    return errs


def _unknown_root_keys_warnings(data: dict[str, object]) -> list[str]:
    """Warn on keys that are likely typos (not errors — forward compatibility)."""
    extra = set(data.keys()) - KNOWN_OPTIONAL_ROOT_KEYS
    if not extra:
        return []
    return [
        "unknown top-level key(s) in product.yaml (ignored by loader; possible typo): "
        + ", ".join(sorted(extra))
    ]


@dataclass
class ManifestValidationResult:
    """Errors block normalization; warnings are non-fatal."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    node: ProductNode | None = None


def _lifecycle_stage_from_raw(raw: object) -> LifecycleStage | None:
    if raw is None:
        return None
    try:
        return LifecycleStage(str(raw))
    except ValueError:
        return None


def validate_manifest(
    data: dict[str, object],
    *,
    repo_root: Path,
    product_root: Path,
    config_path: Path,
) -> ManifestValidationResult:
    """
    Validate ``product.yaml`` content and build a :class:`ProductNode` when possible.

    Checks required keys, lifecycle/signal consistency, referenced script paths, and
    delegates structural checks to :func:`validate_product_node`.
    """
    out = ManifestValidationResult()

    if not data.get("id"):
        out.errors.append("missing required field: id")
    elif not str(data["id"]).strip():
        out.errors.append("id must be non-empty")

    life = data.get("lifecycle")
    ls_life: LifecycleStage | None = None
    if life is None:
        out.errors.append("missing required field: lifecycle")
    elif not isinstance(life, dict):
        out.errors.append("lifecycle must be a mapping")
    else:
        stage_raw = life.get("stage")
        if stage_raw is None:
            out.errors.append("missing required field: lifecycle.stage")
        else:
            ls = _lifecycle_stage_from_raw(stage_raw)
            if ls is None:
                out.errors.append(
                    f"invalid lifecycle.stage: {stage_raw!r} (expected one of: {_LIFECYCLE_VALUES})"
                )
            else:
                ls_life = ls

    owner = data.get("owner")
    if owner is None:
        out.errors.append("missing required field: owner")
    elif not isinstance(owner, dict):
        out.errors.append("owner must be a mapping")
    elif not str(owner.get("team", "")).strip():
        out.errors.append("missing required field: owner.team")

    spec, m_err = parse_product_mission_from_yaml(dict(data))
    if m_err:
        out.errors.append(m_err)
    elif spec is not None:
        reg_err = validate_product_mission_registry(repo_root, spec)
        if reg_err:
            out.errors.append(reg_err)

    out.errors.extend(_errors_top_level_shapes(data, out.warnings))
    out.warnings.extend(_unknown_root_keys_warnings(data))

    state_raw = data.get("state")
    if state_raw is not None:
        ls_state = _lifecycle_stage_from_raw(state_raw)
        if ls_state is None:
            out.errors.append(
                f"invalid state: {state_raw!r} (expected one of: {_LIFECYCLE_VALUES})"
            )
        elif ls_life is not None and ls_state != ls_life:
            out.errors.append(
                f"state '{ls_state.value}' conflicts with lifecycle.stage "
                f"'{ls_life.value}' (invalid transition / inconsistent definition)"
            )

    signals = data.get("signals")
    if isinstance(signals, list):
        for i, item in enumerate(signals):
            if not isinstance(item, dict):
                out.errors.append(f"signals[{i}] must be a mapping")
                continue
            t = item.get("type")
            if t is None:
                out.errors.append(f"signals[{i}].type is required")
                continue
            try:
                SignalType(str(t))
            except ValueError:
                out.errors.append(
                    f"unknown signal type: {t!r} (expected one of: {_SIGNAL_VALUES})"
                )
    elif signals is not None:
        out.errors.append("signals must be a list when present")

    actions = data.get("actions")
    if isinstance(actions, dict):
        for key, val in actions.items():
            if val is None:
                continue
            cmd = str(val).strip()
            if not cmd:
                out.warnings.append(f"actions.{key} is empty")
                continue
            if looks_like_filesystem_command(cmd):
                rel = cmd[2:] if cmd.startswith("./") else cmd
                rel = rel.lstrip("/")
                try:
                    target = join_under_product(product_root, rel)
                except ValueError as e:
                    out.errors.append(f"action {key}: {e}")
                    continue
                if not target.is_file():
                    out.errors.append(
                        f"action {key}: script or file not found at {target} "
                        f"(declared as {cmd!r})"
                    )
    elif actions is not None:
        out.errors.append("actions must be a mapping when present")

    metrics = data.get("metrics")
    if isinstance(metrics, dict):
        lp_raw = metrics.get("local_paths")
        lp_list = lp_raw if isinstance(lp_raw, list) else []
        for p in lp_list:
            if not isinstance(p, str):
                out.warnings.append(
                    f"metrics.local_paths entry must be string, got {type(p).__name__}"
                )
                continue
            rel = p.strip().lstrip("/")
            try:
                dirpath = join_under_product(product_root, rel)
            except ValueError as e:
                out.errors.append(f"metrics.local_paths: {e}")
                continue
            if not dirpath.is_dir():
                out.warnings.append(
                    f"metrics local_path {p!r}: directory not found at {dirpath}"
                )

    if not data.get("name"):
        out.warnings.append("optional field missing: name (defaults to id)")

    pid = str(data.get("id", "")).strip()
    if pid and product_root.name != pid:
        out.warnings.append(
            f"convention: product directory name {product_root.name!r} differs from id {pid!r} "
            "(expected `products/<id>/product.yaml` with matching <id>)"
        )

    manifest_obj = None
    if pid:
        manifest_obj, m_errs, m_warns = load_product_signal_manifest(
            product_root=product_root,
            product_yaml=data,
            product_id=pid,
        )
        out.errors.extend(m_errs)
        out.warnings.extend(m_warns)
        if manifest_obj:
            for e in manifest_obj.signals:
                if e.path:
                    rel = e.path.strip().lstrip("/")
                    try:
                        join_under_product(product_root, rel)
                    except ValueError as ex:
                        out.errors.append(f"signal_manifest signal {e.id!r} path: {ex}")

    if out.errors:
        return out

    merged = dict(data)
    merged["product_root"] = ensure_posix_relative(repo_root, product_root)
    merged["config_path"] = ensure_posix_relative(repo_root, config_path)
    if manifest_obj is not None:
        merged["signal_manifest"] = to_jsonable(manifest_obj)
    else:
        merged.pop("signal_manifest", None)

    try:
        node = product_node_from_dict(merged)
    except (KeyError, TypeError, ValueError) as e:
        out.errors.append(f"failed to build ProductNode: {e}")
        return out

    try:
        validate_product_node(node)
    except ArgusValidationError as e:
        out.errors.append(str(e))
        return out

    out.node = node
    return out
