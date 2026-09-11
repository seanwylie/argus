"""
Collect-time verification: compare extracted external identifiers in signal rows to
``raw_extensions.external_bindings`` (when declared).

Narrow extraction only — no fuzzy matching, no product-id inference.
"""

from __future__ import annotations

from typing import Any, Callable
from urllib.parse import urlparse

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.signal import SignalRecord
from argus.products.external_bindings import ExternalBindings

EXTERNAL_IDENTITY_VERIFICATION_SCHEMA = "argus.signal_collection_external_identity.v1"

# Optional payload keys adapters or snapshots may set (documented in product-model.md).
_PAYLOAD_DOMAIN_KEYS = (
    "site_hostname",
    "domain",
    "hostname",
    "primary_hostname",
)
_PAYLOAD_REPO_KEYS = ("repo_url", "source_repo_url", "repository_url")
_PAYLOAD_GA_KEYS = ("ga_property_id", "property_id", "google_analytics_property_id")
_PAYLOAD_GSC_KEYS = ("gsc_property_id", "search_console_property_id")


class SignalIdentityVerificationError(ValueError):
    """Raised when declared bindings contradict extracted external identifiers."""


def _norm_domain(s: str) -> str:
    return s.strip().lower().rstrip(".")


def _norm_repo_url(s: str) -> str:
    t = s.strip()
    if not t:
        return ""
    p = urlparse(t if "://" in t else f"https://{t}")
    host = (p.netloc or p.path.split("/")[0]).lower().rstrip(".")
    path = (p.path or "").rstrip("/")
    if host and path:
        return f"{host}{path}"
    return host or t.lower().rstrip("/")


def _norm_id(s: str) -> str:
    return s.strip()


def _extract_from_snippet(obj: Any, depth: int = 0) -> dict[str, list[str]]:
    """Recursively pull known keys from nested dicts (limited depth)."""
    out: dict[str, list[str]] = {"domains": [], "repos": [], "ga": [], "gsc": [], "other": []}
    if depth > 3 or not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        ks = str(k).lower()
        if isinstance(v, str) and v.strip():
            if ks in ("site_hostname", "domain", "hostname", "primary_hostname"):
                out["domains"].append(_norm_domain(v))
            elif ks in ("repo_url", "source_repo_url", "repository_url", "html_url"):
                out["repos"].append(_norm_repo_url(v))
            elif ks in ("ga_property_id", "property_id", "google_analytics_property_id"):
                out["ga"].append(_norm_id(v))
            elif ks in ("gsc_property_id", "search_console_property_id"):
                out["gsc"].append(_norm_id(v))
        elif isinstance(v, dict):
            sub = _extract_from_snippet(v, depth + 1)
            for kk in out:
                out[kk].extend(sub.get(kk, []))
    return out


def extract_external_identities_from_record(record: SignalRecord) -> dict[str, list[str]]:
    """
    Extract comparable external identity strings from one record (post-canonical).

    Returns dict with keys: ``domains``, ``repos``, ``google_analytics``,
    ``google_search_console``, ``other_analytics`` — each a list of normalized strings.
    """
    domains: list[str] = []
    repos: list[str] = []
    ga: list[str] = []
    gsc: list[str] = []
    other: list[str] = []

    p = record.payload if isinstance(record.payload, dict) else {}
    for k in _PAYLOAD_DOMAIN_KEYS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            domains.append(_norm_domain(v))
    for k in _PAYLOAD_REPO_KEYS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            repos.append(_norm_repo_url(v))
    for k in _PAYLOAD_GA_KEYS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            ga.append(_norm_id(v))
    for k in _PAYLOAD_GSC_KEYS:
        v = p.get(k)
        if isinstance(v, str) and v.strip():
            gsc.append(_norm_id(v))
    v_other = p.get("analytics_property_id")
    if isinstance(v_other, str) and v_other.strip():
        other.append(_norm_id(v_other))

    snip = p.get("snippet")
    if isinstance(snip, dict):
        sub = _extract_from_snippet(snip)
        domains.extend(sub["domains"])
        repos.extend(sub["repos"])
        ga.extend(sub["ga"])
        gsc.extend(sub["gsc"])
        other.extend(sub["other"])

    c = record.canonical
    if isinstance(c, CanonicalSignal):
        ref = str(c.source_ref or "").strip()
        if ref.lower().startswith(("http://", "https://")):
            purl = urlparse(ref)
            host = (purl.netloc or "").lower().rstrip(".")
            if host:
                domains.append(_norm_domain(host))
            path = (purl.path or "").strip("/")
            if host or path:
                repos.append(_norm_repo_url(ref))

    # de-dupe preserving order
    def _dedupe(xs: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for x in xs:
            if x and x not in seen:
                seen.add(x)
                out.append(x)
        return out

    return {
        "domains": _dedupe(domains),
        "repos": _dedupe(repos),
        "google_analytics": _dedupe(ga),
        "google_search_console": _dedupe(gsc),
        "other_analytics": _dedupe(other),
    }


def merge_extracted_identities(records: list[SignalRecord]) -> dict[str, list[str]]:
    """Union extraction across records (deduped)."""
    acc: dict[str, list[str]] = {
        "domains": [],
        "repos": [],
        "google_analytics": [],
        "google_search_console": [],
        "other_analytics": [],
    }
    for r in records:
        part = extract_external_identities_from_record(r)
        for k in acc:
            acc[k].extend(part.get(k, []))
    seen: dict[str, set[str]] = {k: set() for k in acc}
    out: dict[str, list[str]] = {k: [] for k in acc}
    for k, xs in acc.items():
        for x in xs:
            if x not in seen[k]:
                seen[k].add(x)
                out[k].append(x)
    return out


def _check_category(
    *,
    label: str,
    declared: tuple[str, ...],
    extracted: list[str],
    normalize: Callable[[str], str],
) -> dict[str, Any]:
    allowed = {normalize(x) for x in declared}
    mismatch: list[str] = []

    if not declared:
        return {"name": label, "status": "skipped", "declared": [], "extracted": extracted}

    if not extracted:
        return {
            "name": label,
            "status": "not_verifiable",
            "declared": list(declared),
            "extracted": [],
            "note": "no comparable identity extracted from collected signals",
        }

    for ex in extracted:
        if normalize(ex) not in allowed:
            mismatch.append(ex)

    if mismatch:
        return {
            "name": label,
            "status": "failed",
            "declared": list(declared),
            "extracted": extracted,
            "mismatch": mismatch,
        }

    return {
        "name": label,
        "status": "verified",
        "declared": list(declared),
        "extracted": extracted,
    }


def verify_external_identities_for_collection(
    bindings: ExternalBindings | None,
    records: list[SignalRecord],
) -> tuple[dict[str, Any], list[str]]:
    """
    Verify extracted identities against ``bindings``.

    Returns ``(artifact_block, blocking_errors)``. If ``blocking_errors`` is non-empty,
    the collection must not be persisted.
    """
    merged = merge_extracted_identities(records)

    if bindings is None:
        return (
            {
                "schema": EXTERNAL_IDENTITY_VERIFICATION_SCHEMA,
                "overall_status": "skipped_no_bindings",
                "note": "no external_bindings declared for this product",
            },
            [],
        )

    categories: list[dict[str, Any]] = []
    blocking: list[str] = []

    checks = [
        ("domains", bindings.domains, merged["domains"], _norm_domain),
        ("repos", bindings.repos, merged["repos"], _norm_repo_url),
        (
            "analytics.google_analytics",
            bindings.analytics_google_analytics,
            merged["google_analytics"],
            _norm_id,
        ),
        (
            "analytics.google_search_console",
            bindings.analytics_google_search_console,
            merged["google_search_console"],
            _norm_id,
        ),
        ("analytics.other", bindings.analytics_other, merged["other_analytics"], _norm_id),
    ]

    any_declared = any(t[1] for t in checks)
    if not any_declared:
        return (
            {
                "schema": EXTERNAL_IDENTITY_VERIFICATION_SCHEMA,
                "overall_status": "skipped_no_bindings",
                "note": "external_bindings present but all lists empty",
            },
            [],
        )

    for label, declared, extracted, fn in checks:
        if not declared:
            continue
        cat = _check_category(label=label, declared=declared, extracted=extracted, normalize=fn)
        categories.append(cat)
        if cat.get("status") == "failed":
            mm = cat.get("mismatch") or []
            blocking.append(
                f"{label}: extracted identity not in declared bindings: {mm!r} "
                f"(declared allow-list: {list(declared)!r})"
            )

    # Overall status
    if blocking:
        overall = "failed"
    elif not categories:
        overall = "skipped_no_bindings"
    else:
        stats = [c.get("status") for c in categories]
        if all(s == "verified" for s in stats):
            overall = "verified"
        elif all(s in ("verified", "not_verifiable") for s in stats) and any(
            s == "not_verifiable" for s in stats
        ):
            overall = "not_verifiable"
        elif all(s == "not_verifiable" for s in stats):
            overall = "not_verifiable"
        else:
            overall = "verified"

    payload: dict[str, Any] = {
        "schema": EXTERNAL_IDENTITY_VERIFICATION_SCHEMA,
        "overall_status": overall,
        "categories": categories,
    }
    return payload, blocking
