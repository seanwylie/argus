"""Register adapter classes by id and :class:`~argus.adapters.base.AdapterCategory`."""

from __future__ import annotations

from argus.adapters.base import Adapter, AdapterCategory

# adapter_id -> (category, class)
_ADAPTER_REGISTRY: dict[str, tuple[AdapterCategory, type[Adapter]]] = {}


def register_adapter_class(
    adapter_id: str,
    category: AdapterCategory,
    cls: type[Adapter],
) -> None:
    """Register a concrete adapter class (typically at import time)."""
    _ADAPTER_REGISTRY[adapter_id.strip()] = (category, cls)


def registered_adapters() -> dict[str, tuple[AdapterCategory, type[Adapter]]]:
    """Copy of id → (category, class)."""
    return dict(_ADAPTER_REGISTRY)


def adapter_ids_by_category(category: AdapterCategory) -> list[str]:
    return sorted(k for k, (c, _) in _ADAPTER_REGISTRY.items() if c == category)


def summary_table() -> dict[str, dict[str, str]]:
    """Metadata for CLI ``list``."""
    rows: dict[str, dict[str, str]] = {}
    for aid, (cat, cls) in sorted(_ADAPTER_REGISTRY.items()):
        _ = cls()  # ensure adapter is instantiable for metadata
        rows[aid] = {
            "adapter_id": aid,
            "category": cat.value,
            "class": f"{cls.__module__}.{cls.__name__}",
        }
    return rows


def _register_builtins() -> None:
    from argus.adapters.builtins import DEFAULT_BUILTIN_ADAPTERS

    for cls in DEFAULT_BUILTIN_ADAPTERS:
        tmp: Adapter = cls()
        register_adapter_class(tmp.adapter_id, tmp.category, cls)


_register_builtins()
