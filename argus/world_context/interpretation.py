"""
Deterministic interpretation of ``argus.world_context.v1`` signals (advisory only).

No LLM, no web calls — rules over stored rows only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.world_context.persist import (
    WORLD_CONTEXT_INTERPRETATION_SCHEMA,
    WORLD_CONTEXT_SCHEMA,
)

# Must match ``argus.world_context.service.ADVISORY_DISCLAIMER`` (duplicated to avoid import cycles).
_ADVISORY_DISCLAIMER = (
    "Advisory only — external signals do not authorize product creation or portfolio decisions."
)

# Heuristic thresholds (documented in limitations; not scores).
VERY_LOW_TRAFFIC_USERS = 30
LOW_TRAFFIC_USERS = 80
CANADA_CONCENTRATED_SHARE = 0.4


def _fval(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _group_by_entity(signals: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for s in signals:
        if not isinstance(s, dict):
            continue
        e = str(s.get("entity") or "").strip()
        if not e:
            continue
        out.setdefault(e, []).append(s)
    return out


def _entity_profile(entity: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract comparable facts from normalized signals for one entity."""
    traffic_users: float | None = None
    trend_unit: str | None = None
    trend_conf: str | None = None
    canada_share: float | None = None
    conversion_zero: bool | None = None
    revenue_zero: bool | None = None
    engagement_max_min: float | None = None
    engagement_canada_min: float | None = None

    for r in rows:
        st = str(r.get("signal_type") or "")
        unit = str(r.get("unit") or "").lower()
        val = _fval(r.get("value"))
        if st == "traffic" and unit == "users_last_28d" and val is not None:
            traffic_users = val
        elif st == "trend":
            trend_unit = str(r.get("unit") or "")
            trend_conf = str(r.get("confidence") or "")
        elif st == "audience" and "canada_user_share" in unit and val is not None:
            canada_share = val
        elif st == "conversion" and val is not None:
            conversion_zero = val == 0.0
        elif st == "revenue" and val is not None:
            revenue_zero = val == 0.0
        elif st == "engagement" and val is not None:
            if "canada" in unit and "minute" in unit:
                engagement_canada_min = max(engagement_canada_min or 0.0, val)
            elif "minute" in unit:
                engagement_max_min = max(engagement_max_min or 0.0, val)

    return {
        "entity": entity,
        "traffic_users_28d": traffic_users,
        "trend_unit": trend_unit,
        "trend_confidence": trend_conf,
        "canada_share": canada_share,
        "conversion_zero": conversion_zero,
        "revenue_zero": revenue_zero,
        "engagement_max_minutes": engagement_max_min,
        "engagement_canada_minutes": engagement_canada_min,
    }


def _patterns_for_profile(p: dict[str, Any]) -> list[str]:
    ps: list[str] = []
    tu = str(p.get("trend_unit") or "").lower()
    users = p.get("traffic_users_28d")
    if isinstance(users, (int, float)) and users < VERY_LOW_TRAFFIC_USERS:
        ps.append("very_low_traffic")
    elif isinstance(users, (int, float)) and users < LOW_TRAFFIC_USERS:
        ps.append("low_traffic")
    if "spike" in tu or "not_sustained" in tu:
        ps.append("spike_not_sustained")
    if "indeterminate" in tu or "growth_indeterminate" in tu:
        ps.append("trend_indeterminate")
    cs = p.get("canada_share")
    if isinstance(cs, (int, float)) and cs >= CANADA_CONCENTRATED_SHARE:
        ps.append("canada_concentrated_audience")
    if p.get("conversion_zero") is True:
        ps.append("no_conversion_signal")
    if p.get("revenue_zero") is True:
        ps.append("no_revenue_signal")
    ec = p.get("engagement_canada_minutes")
    em = p.get("engagement_max_minutes")
    has_eng = (isinstance(ec, (int, float)) and ec > 0) or (isinstance(em, (int, float)) and em > 0)
    if has_eng and p.get("conversion_zero") is True:
        ps.append("engagement_without_conversion")
    return sorted(set(ps))


def _lines_for_entity(p: dict[str, Any]) -> tuple[list[str], list[str]]:
    """Returns (summary_lines, signal_types_used)."""
    e = str(p["entity"])
    lines: list[str] = []
    types_used: list[str] = []

    users = p.get("traffic_users_28d")
    if isinstance(users, (int, float)):
        types_used.append("traffic")
        if users < VERY_LOW_TRAFFIC_USERS:
            lines.append(f"{e}: very low traffic ({int(users)} users in last 28d).")
        elif users < LOW_TRAFFIC_USERS:
            lines.append(f"{e}: low traffic ({int(users)} users in last 28d).")
        else:
            lines.append(f"{e}: higher traffic ({int(users)} users in last 28d).")

    tu = str(p.get("trend_unit") or "").lower()
    if tu:
        types_used.append("trend")
        if "spike" in tu or "not_sustained" in tu:
            lines.append(f"{e}: traffic pattern described as spike-like and not sustained (trend signal).")
        elif "indeterminate" in tu:
            lines.append(f"{e}: trend indeterminate from available volume (trend signal).")

    cs = p.get("canada_share")
    if isinstance(cs, (int, float)) and cs >= CANADA_CONCENTRATED_SHARE:
        types_used.append("audience")
        lines.append(f"{e}: audience concentrated in Canada (~{cs:.0%} share of users in signals).")

    if p.get("conversion_zero") is True and p.get("revenue_zero") is True:
        types_used.extend(["conversion", "revenue"])
        lines.append(f"{e}: no conversion and no revenue in recorded signals.")
    elif p.get("conversion_zero") is True:
        types_used.append("conversion")
        lines.append(f"{e}: no conversion in recorded signals.")
    elif p.get("revenue_zero") is True:
        types_used.append("revenue")
        lines.append(f"{e}: no revenue in recorded signals.")

    ec = p.get("engagement_canada_minutes")
    em = p.get("engagement_max_minutes")
    if isinstance(ec, (int, float)) and ec > 0:
        types_used.append("engagement")
        lines.append(f"{e}: Canada segment engagement ~{ec:.1f} minutes (engagement signal).")
    elif isinstance(em, (int, float)) and em > 0:
        types_used.append("engagement")
        lines.append(f"{e}: engagement up to ~{em:.1f} minutes (engagement signal).")

    if not lines:
        lines.append(f"{e}: insufficient structured signals for a detailed summary.")
    return lines, sorted(set(types_used))


def _comparison_lines(
    profiles: list[dict[str, Any]],
) -> tuple[list[str], list[str]]:
    """Returns (comparison_lines, pattern ids)."""
    if len(profiles) < 2:
        return [], []
    lines: list[str] = []
    pats: list[str] = []
    # Sort by traffic descending when known
    keyed = [(p, p.get("traffic_users_28d")) for p in profiles]
    keyed.sort(key=lambda x: (x[1] is None, -(x[1] or 0.0)))

    hi, lo = keyed[0][0], keyed[-1][0]
    hi_u = hi.get("traffic_users_28d")
    lo_u = lo.get("traffic_users_28d")
    if isinstance(hi_u, (int, float)) and isinstance(lo_u, (int, float)) and hi_u > lo_u * 1.5:
        lines.append(
            f"Traffic contrast: `{hi['entity']}` has materially higher recorded traffic than `{lo['entity']}` "
            f"({int(hi_u)} vs {int(lo_u)} users / 28d in signals)."
        )
        pats.append("traffic_contrast")

    # Spike only on higher traffic entity
    if "spike_not_sustained" in _patterns_for_profile(hi) and "spike_not_sustained" not in _patterns_for_profile(lo):
        lines.append(
            f"Spike-like pattern appears on `{hi['entity']}` signals, not on `{lo['entity']}` (trend units)."
        )
        pats.append("spike_contrast")

    # Canada concentration
    hc = hi.get("canada_share")
    lc = lo.get("canada_share")
    if (
        isinstance(hc, (int, float))
        and hc >= CANADA_CONCENTRATED_SHARE
        and (not isinstance(lc, (int, float)) or lc < CANADA_CONCENTRATED_SHARE * 0.5)
    ):
        lines.append(
            f"Geography: `{hi['entity']}` shows stronger Canadian audience concentration in signals than `{lo['entity']}`."
        )
        pats.append("geography_contrast")

    # Both no conversion / no revenue
    all_nc = all(p.get("conversion_zero") is True for p in profiles)
    all_nr = all(p.get("revenue_zero") is True for p in profiles)
    if all_nc and all_nr:
        lines.append("Neither entity shows conversion or revenue in these signals.")
        pats.append("neither_monetized")

    return lines, sorted(set(pats))


def _global_patterns(per_patterns: dict[str, list[str]], comparison_pats: list[str]) -> list[str]:
    flat = set()
    for ps in per_patterns.values():
        flat.update(ps)
    flat.update(comparison_pats)
    return sorted(flat)


def _operator_narrative(
    per_lines: dict[str, list[str]],
    comparison_lines: list[str],
    entities: list[str],
) -> str:
    parts: list[str] = []
    if comparison_lines:
        parts.append(" ".join(comparison_lines[:3]))
    # One line per entity if not redundant
    for e in entities:
        ls = per_lines.get(e) or []
        if not ls:
            continue
        # Skip entity block if fully covered by comparison for neither_monetized only
        parts.append(" ".join(ls[:4]))
    text = " ".join(x for x in parts if x).strip()
    if len(text) > 900:
        text = text[:897] + "…"
    return text


def build_interpretation_payload(world_context: dict[str, Any] | None) -> dict[str, Any] | None:
    """
    Build ``argus.world_context.interpretation.v1`` payload from a loaded world-context artifact.

    Returns ``None`` if world context is missing, wrong schema, or has no signals.
    """
    if not world_context or str(world_context.get("schema") or "") != WORLD_CONTEXT_SCHEMA:
        return None
    signals = world_context.get("signals")
    if not isinstance(signals, list) or not signals:
        return None

    grouped = _group_by_entity(signals)
    entities = sorted(grouped.keys())
    profiles = [_entity_profile(e, grouped[e]) for e in entities]
    per_entity: dict[str, Any] = {}
    per_lines: dict[str, list[str]] = {}
    per_patterns: dict[str, list[str]] = {}

    for p in profiles:
        e = str(p["entity"])
        plines, stypes = _lines_for_entity(p)
        pats = _patterns_for_profile(p)
        per_lines[e] = plines
        per_patterns[e] = pats
        per_entity[e] = {
            "summary_lines": plines,
            "patterns": pats,
            "signal_types_informed": stypes,
        }

    comp_lines, comp_pats = _comparison_lines(profiles) if len(entities) >= 2 else ([], [])
    notable = _global_patterns(per_patterns, comp_pats)

    narrative = _operator_narrative(per_lines, comp_lines, entities)
    if not narrative:
        narrative = (
            f"Recorded signals for: {', '.join(f'`{e}`' for e in entities)}. "
            + _ADVISORY_DISCLAIMER
        )
    else:
        narrative = narrative + " " + _ADVISORY_DISCLAIMER

    limitations = [
        "Interpretation uses deterministic thresholds (traffic bands, Canada share) — not forecasts.",
        "Statements follow stored units and values; missing signal types are not inferred.",
    ]

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": str(WORLD_CONTEXT_INTERPRETATION_SCHEMA),
        "generated_at_utc": now,
        "advisory_only": True,
        "disclaimer": _ADVISORY_DISCLAIMER,
        "source_artifact_rel": "runs/world_context/latest.json",
        "source_schema": WORLD_CONTEXT_SCHEMA,
        "source_world_context_generated_at_utc": world_context.get("generated_at_utc"),
        "entities_ordered": entities,
        "per_entity": per_entity,
        "comparison": {"summary_lines": comp_lines, "patterns": comp_pats} if comp_lines else None,
        "notable_patterns": notable,
        "limitations": limitations,
        "operator_narrative": narrative.strip(),
    }


def operator_advisory_from_world_context(world_context: dict[str, Any] | None) -> str | None:
    """
    Primary advisory paragraph for operator summary / zero-state: interpretation if possible,
    else legacy aggregate headline from world-context summary.
    """
    if not world_context:
        return None
    interp = build_interpretation_payload(world_context)
    if interp and str(interp.get("operator_narrative") or "").strip():
        return str(interp["operator_narrative"]).strip()
    from argus.world_context.service import summarize_for_operator

    return summarize_for_operator(world_context)
