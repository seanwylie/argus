"""Load and validate ``signals.yaml`` or inline ``signal_manifest`` in ``product.yaml``."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from argus.core.models.enums import SignalType
from argus.core.models.signal_manifest import (
    PRODUCT_SIGNAL_MANIFEST_SCHEMA,
    ManifestRequiredFor,
    ManifestTrustLevel,
    ManifestValueType,
    ProductSignalManifest,
    ProductSignalManifestEntry,
    SignalManifestCategory,
    SourceWindowKind,
)
from argus.products.loader import load_yaml_file

_SIGNALS_FILENAME = "signals.yaml"
_MAX_SLA_LEN = 64
_SLA_PATTERN = re.compile(
    r"^(?:best_effort|unknown|none|\d+(?:\.\d+)?(?:ms|s|m|h|d|w)|[a-z][a-z0-9_\-]{0,47})$",
    re.I,
)


def _strip_opt(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _parse_bool(v: object, default: bool = True) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def validate_signal_manifest_dict(raw: dict[str, Any]) -> tuple[list[str], list[str], ProductSignalManifest | None]:
    """
    Validate a manifest mapping (from YAML).

    Returns ``(errors, warnings, manifest_or_none)``. On any error, manifest is None.
    """
    errors: list[str] = []
    warnings: list[str] = []

    sch = _strip_opt(raw.get("schema"))
    if sch is not None and sch != PRODUCT_SIGNAL_MANIFEST_SCHEMA:
        errors.append(
            f"signal_manifest.schema: expected {PRODUCT_SIGNAL_MANIFEST_SCHEMA!r}, got {sch!r}",
        )

    sigs_raw = raw.get("signals")
    if sigs_raw is None:
        errors.append("signal_manifest.signals is required (use an empty list if none)")
        return errors, warnings, None
    if not isinstance(sigs_raw, list):
        errors.append("signal_manifest.signals must be a list")
        return errors, warnings, None

    entries: list[ProductSignalManifestEntry] = []
    seen_ids: set[str] = set()

    for i, item in enumerate(sigs_raw):
        prefix = f"signal_manifest.signals[{i}]"
        if not isinstance(item, dict):
            errors.append(f"{prefix} must be a mapping")
            continue

        ie: list[str] = []

        sid = _strip_opt(item.get("id"))
        if not sid:
            ie.append(f"{prefix}.id is required and must be non-empty")
        elif sid in seen_ids:
            ie.append(f"{prefix}.id: duplicate signal id {sid!r}")
        else:
            seen_ids.add(sid)

        cat_raw = _strip_opt(item.get("category"))
        category: SignalManifestCategory | None = None
        if not cat_raw:
            ie.append(f"{prefix}.category is required")
        else:
            try:
                category = SignalManifestCategory(cat_raw)
            except ValueError:
                ie.append(
                    f"{prefix}.category: invalid {cat_raw!r} "
                    f"(expected one of: {', '.join(sorted(SignalManifestCategory))})",
                )

        st_raw = _strip_opt(item.get("source_type"))
        source_type: SignalType | None = None
        if not st_raw:
            ie.append(f"{prefix}.source_type is required")
        else:
            try:
                source_type = SignalType(st_raw)
            except ValueError:
                ie.append(
                    f"{prefix}.source_type: unknown {st_raw!r} "
                    f"(expected one of: {', '.join(sorted(SignalType))})",
                )

        source_ref = _strip_opt(item.get("source_ref"))
        path = _strip_opt(item.get("path"))
        if not source_ref and not path:
            ie.append(f"{prefix}: at least one of source_ref or path must be set")

        sla = _strip_opt(item.get("freshness_sla"))
        if not sla:
            ie.append(f"{prefix}.freshness_sla is required")
        elif len(sla) > _MAX_SLA_LEN:
            ie.append(f"{prefix}.freshness_sla: exceeds {_MAX_SLA_LEN} characters")
        elif not _SLA_PATTERN.match(sla):
            warnings.append(
                f"{prefix}.freshness_sla: unconventional format {sla!r} "
                f"(expected e.g. 15m, 1h, 24h, best_effort)",
            )

        vt_raw = _strip_opt(item.get("value_type"))
        value_type: ManifestValueType | None = None
        if not vt_raw:
            ie.append(f"{prefix}.value_type is required")
        else:
            try:
                value_type = ManifestValueType(vt_raw)
            except ValueError:
                ie.append(
                    f"{prefix}.value_type: invalid {vt_raw!r} "
                    f"(expected one of: {', '.join(sorted(ManifestValueType))})",
                )

        rf_raw = _strip_opt(item.get("required_for"))
        required_for: ManifestRequiredFor | None = None
        if not rf_raw:
            ie.append(f"{prefix}.required_for is required")
        else:
            try:
                required_for = ManifestRequiredFor(rf_raw)
            except ValueError:
                ie.append(
                    f"{prefix}.required_for: invalid {rf_raw!r} "
                    f"(expected one of: {', '.join(sorted(ManifestRequiredFor))})",
                )

        tl_raw = _strip_opt(item.get("trust_level"))
        trust_level: ManifestTrustLevel | None = None
        if not tl_raw:
            ie.append(f"{prefix}.trust_level is required")
        else:
            try:
                trust_level = ManifestTrustLevel(tl_raw)
            except ValueError:
                ie.append(
                    f"{prefix}.trust_level: invalid {tl_raw!r} "
                    f"(expected one of: {', '.join(sorted(ManifestTrustLevel))})",
                )

        swk: SourceWindowKind | None = None
        swk_raw = _strip_opt(item.get("source_window_kind"))
        if swk_raw:
            try:
                swk = SourceWindowKind(swk_raw)
            except ValueError:
                ie.append(
                    f"{prefix}.source_window_kind: invalid {swk_raw!r} "
                    f"(expected one of: {', '.join(sorted(SourceWindowKind))})",
                )

        if ie:
            errors.extend(ie)
            continue

        assert sid is not None and category is not None and source_type is not None
        assert sla is not None and value_type is not None
        assert required_for is not None and trust_level is not None

        entries.append(
            ProductSignalManifestEntry(
                id=sid,
                category=category,
                source_type=source_type,
                source_ref=source_ref,
                path=path,
                freshness_sla=sla,
                value_type=value_type,
                required_for=required_for,
                trust_level=trust_level,
                description=_strip_opt(item.get("description")),
                unit=_strip_opt(item.get("unit")),
                source_window_kind=swk,
                owner=_strip_opt(item.get("owner")),
                enabled=_parse_bool(item.get("enabled"), default=True),
            )
        )

    if errors:
        return errors, warnings, None

    manifest = ProductSignalManifest(schema=PRODUCT_SIGNAL_MANIFEST_SCHEMA, signals=entries)

    return [], warnings, manifest


def resolve_signal_manifest_payload(
    *,
    product_root: Path,
    product_yaml: dict[str, object],
    product_id: str,
) -> tuple[dict[str, Any] | None, list[str], list[str]]:
    """
    Choose manifest source: sibling ``signals.yaml`` overrides inline ``signal_manifest``.

    Returns ``(payload_dict_or_none, errors, warnings)``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    sig_path = product_root / _SIGNALS_FILENAME
    inline = product_yaml.get("signal_manifest")

    if sig_path.is_file():
        raw, err = load_yaml_file(sig_path)
        if err:
            errors.append(f"{_SIGNALS_FILENAME}: {err}")
            return None, errors, warnings
        if not isinstance(raw, dict):
            errors.append(f"{_SIGNALS_FILENAME}: top level must be a mapping")
            return None, errors, warnings
        if isinstance(inline, dict) and inline:
            warnings.append(
                "product.yaml signal_manifest is ignored because signals.yaml is present",
            )
        pid_file = _strip_opt(raw.get("product_id"))
        if pid_file is not None and pid_file != product_id:
            errors.append(
                f"{_SIGNALS_FILENAME} product_id {pid_file!r} does not match product.yaml id {product_id!r}",
            )
            return None, errors, warnings
        return raw, [], warnings

    if isinstance(inline, dict):
        return dict(inline), [], warnings

    if inline is not None:
        errors.append("product.yaml signal_manifest must be a mapping when set")
        return None, errors, warnings

    return None, [], warnings


def enabled_manifest_entries(node: object) -> list[ProductSignalManifestEntry]:
    """Return enabled entries from a :class:`~argus.core.models.product.ProductNode` manifest."""
    sm = getattr(node, "signal_manifest", None)
    if sm is None:
        return []
    return [e for e in sm.signals if e.enabled]


def load_product_signal_manifest(
    *,
    product_root: Path,
    product_yaml: dict[str, object],
    product_id: str,
) -> tuple[ProductSignalManifest | None, list[str], list[str]]:
    """
    Resolve and validate the signal manifest for a product.

    Returns ``(manifest_or_none, errors, warnings)``. When there is no manifest file
    or inline block, returns ``(None, [], [])``.
    """
    payload, errs, warns = resolve_signal_manifest_payload(
        product_root=product_root,
        product_yaml=product_yaml,
        product_id=product_id,
    )
    if errs:
        return None, errs, warns
    if payload is None:
        return None, [], warns

    e2, w2, manifest = validate_signal_manifest_dict(payload)
    return manifest, errs + e2, warns + w2
