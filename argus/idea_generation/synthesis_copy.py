"""
Deterministic synthesis description tightening: product context, inspectable basis, less filler.

No LLMs. Used only for :func:`~argus.idea_generation.synthesis.synthesize_ideas` output.
"""

from __future__ import annotations

from typing import Any

# Bodies already cite ids/paths explicitly; avoid duplicate “Basis” lines.
_SKIP_BASIS_APPEND = frozenset(
    {
        "finding_grounded",
        "finding_twist_local",
        "signal_record_monetization",
        "temporal_domain",
        "finding_twist",
    }
)


def _collect_basis_fragments(grounding: dict[str, Any]) -> list[str]:
    raw = grounding.get("grounding_sources")
    if not isinstance(raw, list):
        return []
    frags: list[str] = []
    for s in raw:
        if not isinstance(s, dict):
            continue
        t = str(s.get("type") or "")
        if t == "finding" and s.get("id"):
            kid = str(s.get("kind") or "")
            frags.append("finding `" + str(s["id"]) + "`" + (f" ({kid})" if kid else ""))
        elif t in ("artifact", "artifact_hint") and s.get("path"):
            frags.append(f"artifact `{s['path']}`")
        elif t == "repo_path" and s.get("path"):
            frags.append(f"repo `{s['path']}`")
        elif t == "doctrine" and s.get("path"):
            frags.append(f"doctrine `{s['path']}`")
        elif t == "signal_record" and s.get("id"):
            st = str(s.get("signal_type") or "")
            frags.append("signal `" + str(s["id"]) + "`" + (f" [{st}]" if st else ""))
        elif t == "signal_type_only" and s.get("value") is not None:
            frags.append(f"signal family `{s['value']}` (no row id on this idea)")
        elif t == "product" and s.get("product_id"):
            frags.append(f"product `{s['product_id']}`")
        elif t == "portfolio_pair":
            a, b = s.get("product_a"), s.get("product_b")
            if a and b:
                frags.append(f"portfolio pair `{a}` × `{b}`")
        elif t == "finding_kind" and s.get("value") is not None:
            frags.append(f"finding kind `{s['value']}`")
        elif t == "alien_product" and s.get("id"):
            frags.append(f"host product `{s['id']}`")
        elif t == "temporal_tags" and isinstance(s.get("tags"), list):
            tags = s["tags"][:8]
            frags.append("temporal hooks " + ", ".join(f"`{x}`" for x in tags))
        elif t == "combinatorial" and s.get("pattern"):
            frags.append(f"pattern `{s['pattern']}`")
    return sorted(set(frags))


def finalize_synthesis_description(
    description: str,
    grounding: dict[str, Any] | None,
    *,
    pattern: str,
    product_label: str | None,
    product_id: str | None,
    product_root_rel: str,
    single_product: bool,
) -> str:
    """Apply deterministic templates using grounding + product scope."""
    out = description
    if grounding is None:
        return out

    plab = (product_label or "").strip() or None
    proot = (product_root_rel or "").strip()
    pid = (product_id or "").strip() or None

    # Product-scoped weak / combinatorial rows: lead with product + repo, not abstract “pad”.
    if single_product and plab and proot:
        if pattern == "domain_fuse":
            out = out.replace(
                "Pure combinatorial pad (low grounding):",
                f"For {plab} (repo `{proot}`), combinatorial sketch:",
                1,
            )
        elif pattern == "channel_domain":
            out = out.replace(
                "Combinatorial hook (weak grounding):",
                f"For {plab} (repo `{proot}`), channel×domain hook:",
                1,
            )
    elif pattern == "channel_domain" and not single_product:
        out = out.replace(
            "Portfolio-wide brainstorm (no single-product artifact anchor).",
            "Portfolio-wide brainstorm — tie each bet to a concrete `runs/signals/latest/<product>.json` "
            "or `runs/findings/latest/<product>.json` slice before funding.",
            1,
        )

    # Lattice row: real product id instead of placeholder; name the product in the lead.
    if pattern == "signal_monetization" and pid:
        out = out.replace("runs/signals/latest/<product_id>.json", f"runs/signals/latest/{pid}.json")
        if plab and not out.startswith(f"For {plab}:"):
            out = out.replace(
                "Evidence: signal family name only",
                f"For {plab}: evidence is signal family name only",
                1,
            )

    # Telemetry hook: name the product when this run is single-product scoped.
    if pattern == "temporal_domain" and single_product and plab:
        pfx = f"For {plab}: "
        if not out.startswith(pfx):
            out = pfx + out

    # High-signal rows: lead with product name when scoped to one product.
    if pattern == "finding_grounded" and single_product and plab:
        pfx = f"For {plab}: "
        if not out.startswith(pfx):
            out = pfx + out
    if pattern == "signal_record_monetization" and single_product and plab:
        pfx = f"For {plab}: "
        if not out.startswith(pfx):
            out = pfx + out

    # Portfolio pairs: less generic closing; still deterministic.
    if pattern == "product_pair":
        out = out.replace(
            "Start with one shared audience hypothesis and one measurable event.",
            "Anchor in each product’s current signals/findings bundles, then pick one falsifiable metric.",
        )

    frags = _collect_basis_fragments(grounding)
    if (
        frags
        and pattern not in _SKIP_BASIS_APPEND
        and "Basis (inspectable):" not in out
    ):
        # Repo already spelled in-body for single-product tree lines.
        if pattern in ("channel_domain", "domain_fuse") and "Product tree:" in out:
            pass
        else:
            joined = "; ".join(frags)
            out = f"{out.rstrip()} Basis (inspectable): {joined}."

    return out
