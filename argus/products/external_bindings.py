"""
Validated ``raw_extensions.external_bindings`` — optional external identity contract per product.

``product_id`` remains the canonical routing key; bindings verify that observable external
identifiers in collected signals are consistent with declared expectations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

EXTERNAL_BINDINGS_SCHEMA = "argus.external_bindings.v1"

_KNOWN_TOP_KEYS = frozenset({"schema", "domains", "repos", "analytics"})
_ANALYTICS_SUBKEYS = frozenset({"google_analytics", "google_search_console", "other"})


@dataclass(frozen=True)
class ExternalBindings:
    """Normalized, validated binding lists (empty tuple = no bindings for that slot)."""

    domains: tuple[str, ...]
    repos: tuple[str, ...]
    analytics_google_analytics: tuple[str, ...]
    analytics_google_search_console: tuple[str, ...]
    analytics_other: tuple[str, ...]


def _non_empty_str_list(name: str, raw: Any) -> tuple[str, ...] | None:
    """Return tuple of stripped non-empty strings, or None if key absent; errors if wrong type."""
    if raw is None:
        return tuple()
    if not isinstance(raw, list):
        return None  # signal error
    out: list[str] = []
    for i, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            return None
        out.append(item.strip())
    return tuple(out)


def validate_external_bindings_block(block: Any) -> tuple[list[str], list[str]]:
    """
    Validate ``raw_extensions.external_bindings``.

    Returns ``(errors, warnings)``. Errors block manifest load; warnings are non-fatal.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(block, dict):
        return (["raw_extensions.external_bindings must be a mapping when present"], warnings)

    sch = block.get("schema")
    if sch != EXTERNAL_BINDINGS_SCHEMA:
        errors.append(
            "raw_extensions.external_bindings.schema must be "
            f"{EXTERNAL_BINDINGS_SCHEMA!r}; got {sch!r}"
        )
        return (errors, warnings)

    for k in block.keys():
        if k not in _KNOWN_TOP_KEYS:
            warnings.append(
                f"raw_extensions.external_bindings: unknown key {k!r} ignored (forward compatibility)"
            )

    for key in ("domains", "repos"):
        raw = block.get(key)
        if raw is None:
            continue
        t = _non_empty_str_list(key, raw)
        if t is None:
            errors.append(f"raw_extensions.external_bindings.{key} must be a list of non-empty strings")

    analytics = block.get("analytics")
    if analytics is not None:
        if not isinstance(analytics, dict):
            errors.append("raw_extensions.external_bindings.analytics must be a mapping when present")
        else:
            for k in analytics.keys():
                if k not in _ANALYTICS_SUBKEYS:
                    warnings.append(
                        "raw_extensions.external_bindings.analytics: unknown key "
                        f"{k!r} ignored (forward compatibility)"
                    )
            for sub in _ANALYTICS_SUBKEYS:
                raw = analytics.get(sub)
                if raw is None:
                    continue
                t = _non_empty_str_list(f"analytics.{sub}", raw)
                if t is None:
                    errors.append(
                        "raw_extensions.external_bindings.analytics."
                        f"{sub} must be a list of non-empty strings"
                    )

    return (errors, warnings)


def parse_external_bindings(raw_extensions: Mapping[str, Any] | None) -> ExternalBindings | None:
    """
    Return :class:`ExternalBindings` if ``external_bindings`` is present and valid, else ``None``.

    Call only after :func:`validate_external_bindings_block` reports no errors (or use
    :func:`external_bindings_from_raw_extensions` which validates inline).
    """
    if not raw_extensions:
        return None
    eb = raw_extensions.get("external_bindings")
    if eb is None:
        return None
    if not isinstance(eb, dict):
        return None
    if eb.get("schema") != EXTERNAL_BINDINGS_SCHEMA:
        return None

    def _list(key: str) -> tuple[str, ...]:
        raw = eb.get(key)
        if raw is None:
            return tuple()
        t = _non_empty_str_list(key, raw)
        return t if t is not None else tuple()

    dom = _list("domains")
    repos = _list("repos")

    ga: tuple[str, ...] = tuple()
    gsc: tuple[str, ...] = tuple()
    other: tuple[str, ...] = tuple()
    an = eb.get("analytics")
    if isinstance(an, dict):
        for sub, target in (
            ("google_analytics", "ga"),
            ("google_search_console", "gsc"),
            ("other", "other"),
        ):
            raw = an.get(sub)
            if raw is None:
                continue
            t = _non_empty_str_list(sub, raw)
            if t is None:
                continue
            if sub == "google_analytics":
                ga = t
            elif sub == "google_search_console":
                gsc = t
            else:
                other = t

    if not any((dom, repos, ga, gsc, other)):
        return None

    return ExternalBindings(
        domains=dom,
        repos=repos,
        analytics_google_analytics=ga,
        analytics_google_search_console=gsc,
        analytics_other=other,
    )


def external_bindings_from_raw_extensions(raw_extensions: Mapping[str, Any] | None) -> ExternalBindings | None:
    """Validate ``external_bindings`` block (if present) and return parsed bindings or ``None``."""
    if not raw_extensions:
        return None
    eb = raw_extensions.get("external_bindings")
    if eb is None:
        return None
    errs, _warns = validate_external_bindings_block(eb)
    if errs:
        return None
    return parse_external_bindings(raw_extensions)
