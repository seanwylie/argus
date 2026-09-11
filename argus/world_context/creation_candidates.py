"""
Advisory creation directions derived deterministically from world context + interpretation.

Hypothesis-level only — no ranking, no autonomous creation, no LLM.

``primary_candidate_id`` is a single “most supported by current signals” pointer, not a scoreboard.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from argus.world_context.persist import (
    WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
    WORLD_CONTEXT_INTERPRETATION_SCHEMA,
    WORLD_CONTEXT_SCHEMA,
)

_ADVISORY_LABEL = (
    "Advisory creation directions — hypothesis-level suggestions from external signals only; "
    "not product decisions and not executed automatically."
)


def _pretty_entity(eid: str) -> str:
    s = str(eid).strip()
    if not s:
        return s
    return s[:1].upper() + s[1:] if len(s) > 1 else s.upper()


def _sig_evidence_rows(
    signals: list[dict[str, Any]],
    *,
    entities: set[str] | None = None,
    signal_types: set[str] | None = None,
    max_rows: int = 8,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for s in signals:
        if not isinstance(s, dict):
            continue
        e = str(s.get("entity") or "")
        st = str(s.get("signal_type") or "")
        if entities is not None and e not in entities:
            continue
        if signal_types is not None and st not in signal_types:
            continue
        out.append(
            {
                "entity": e,
                "signal_type": st,
                "unit": str(s.get("unit") or ""),
                "value": s.get("value"),
                "confidence": str(s.get("confidence") or ""),
            }
        )
        if len(out) >= max_rows:
            break
    return out


def _traffic_by_entity(signals: list[dict[str, Any]]) -> dict[str, float]:
    m: dict[str, float] = {}
    for s in signals:
        if not isinstance(s, dict):
            continue
        if str(s.get("signal_type")) != "traffic":
            continue
        if str(s.get("unit") or "") != "users_last_28d":
            continue
        v = s.get("value")
        if isinstance(v, (int, float)):
            e = str(s.get("entity") or "")
            if e:
                m[e] = float(v)
    return m


def _highest_traffic_entity(traffic: dict[str, float]) -> str | None:
    if not traffic:
        return None
    return max(traffic.items(), key=lambda x: x[1])[0]


def _lowest_traffic_entity(traffic: dict[str, float]) -> str | None:
    if not traffic:
        return None
    return min(traffic.items(), key=lambda x: x[1])[0]


def _strength(pattern_hits: int, *, kind: str) -> str:
    if kind == "restrain":
        return "medium" if pattern_hits >= 2 else "low"
    if pattern_hits >= 3:
        return "medium"
    return "low"


def _build_situation_summary(
    *,
    hi_entity: str | None,
    lo_entity: str | None,
    entities_ordered: list[str],
) -> str:
    """One to two sentences for operator fold; deterministic."""
    parts: list[str] = []
    if hi_entity and lo_entity and hi_entity != lo_entity:
        ph = _pretty_entity(hi_entity)
        pl = _pretty_entity(lo_entity)
        parts.append(
            f"{ph} shows stronger traffic than {pl}; spike and geography signals attach to {ph} where the data supports it."
        )
        parts.append(
            f"{pl} reads early-stage and low-volume — treat large bets cautiously until the signal strengthens."
        )
    elif entities_ordered:
        parts.append(
            f"External signals cover {', '.join(f'`{e}`' for e in entities_ordered)}; compare entities before committing scope."
        )
    text = " ".join(parts).strip()
    if len(text) > 700:
        text = text[:697] + "…"
    return text


def _pick_primary_candidate_id(candidates: list[dict[str, Any]]) -> str | None:
    """Single pointer: strongest action-like signal bundle first; else restraint; else first."""
    if not candidates:
        return None
    ids = [str(c.get("candidate_id") or "") for c in candidates if c.get("candidate_id")]
    if "conversion_wrapper_around_spike" in ids:
        return "conversion_wrapper_around_spike"
    for c in candidates:
        cid = str(c.get("candidate_id") or "")
        if c.get("candidate_kind") == "action" and cid.startswith("canada_focus"):
            return cid
    for c in candidates:
        cid = str(c.get("candidate_id") or "")
        if c.get("candidate_kind") == "action" and cid.startswith("engagement_funnel"):
            return cid
    for c in candidates:
        if c.get("candidate_kind") == "restrain":
            return str(c.get("candidate_id") or "") or None
    return ids[0]


def _finalize_candidate(c: dict[str, Any]) -> dict[str, Any]:
    c.pop("_priority", None)
    return c


def build_creation_candidates_payload(
    world_context: dict[str, Any] | None,
    interpretation: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Build ``argus.world_context.creation_candidates.v1`` from world context + interpretation.

    Returns None if inputs are missing or schemas mismatch (caller may rebuild interpretation first).
    """
    if not world_context or str(world_context.get("schema") or "") != WORLD_CONTEXT_SCHEMA:
        return None
    if not interpretation or str(interpretation.get("schema") or "") != WORLD_CONTEXT_INTERPRETATION_SCHEMA:
        return None

    signals = world_context.get("signals")
    if not isinstance(signals, list) or not signals:
        return None

    traffic = _traffic_by_entity(signals)
    hi_entity = _highest_traffic_entity(traffic)
    lo_entity = _lowest_traffic_entity(traffic)
    entities_ordered = list(interpretation.get("entities_ordered") or [])
    per_ent = interpretation.get("per_entity") if isinstance(interpretation.get("per_entity"), dict) else {}
    notable = set(interpretation.get("notable_patterns") or [])
    comp = interpretation.get("comparison") if isinstance(interpretation.get("comparison"), dict) else {}
    comp_pats = set(comp.get("patterns") or [])

    pool: list[tuple[int, dict[str, Any]]] = []
    seen_ids: set[str] = set()

    def push(priority: int, cand: dict[str, Any]) -> None:
        cid = str(cand.get("candidate_id") or "")
        if not cid or cid in seen_ids:
            return
        seen_ids.add(cid)
        cand["_priority"] = priority
        pool.append((priority, cand))

    # 1) Spike + traffic contrast + no monetization → capture spike
    if (
        "spike_contrast" in comp_pats
        and "traffic_contrast" in comp_pats
        and "neither_monetized" in comp_pats
        and hi_entity
    ):
        pe = _pretty_entity(hi_entity)
        ev = _sig_evidence_rows(
            signals,
            entities={hi_entity},
            signal_types={"traffic", "trend", "conversion"},
        )
        pu = sorted(
            {"traffic_contrast", "spike_contrast", "neither_monetized", "spike_not_sustained"} & (notable | comp_pats)
        )
        push(
            100,
            {
                "candidate_id": "conversion_wrapper_around_spike",
                "candidate_kind": "action",
                "title": f"Capture {pe}'s traffic spike before it fades",
                "rationale": f"{pe} combines higher volume with a spike-like (not sustained) trend and no conversion yet — "
                "testing capture now is more time-sensitive than expanding scope blindly.",
                "entities": [hi_entity],
                "interpretation_patterns": pu,
                "evidence_summary": f"Higher traffic on `{hi_entity}`, spike trend unit, zero conversion in signals.",
                "strength": _strength(len(pu), kind="action"),
                "next_steps": [
                    f"Ship one minimal landing or product shell for `{hi_entity}` with a single named conversion event.",
                    "Track that event for one spike cycle before adding surfaces.",
                ],
                "signal_evidence": ev,
            },
        )

    # 2) Restraint: low-signal + indeterminate trend + no monetization (typically low-traffic entity)
    for eid in entities_ordered:
        block = per_ent.get(eid) if isinstance(per_ent.get(eid), dict) else {}
        pats = set(block.get("patterns") or [])
        justified = (
            "very_low_traffic" in pats
            and "trend_indeterminate" in pats
            and ("no_conversion_signal" in pats or "no_revenue_signal" in pats)
        )
        # Prefer the lowest-traffic entity when contrast exists; else any entity that matches.
        if justified and (lo_entity is None or eid == lo_entity or len(entities_ordered) == 1):
            pe = _pretty_entity(eid)
            ev = _sig_evidence_rows(signals, entities={eid}, signal_types={"traffic", "trend", "conversion", "revenue"})
            puse = sorted(pats & {"very_low_traffic", "trend_indeterminate", "no_conversion_signal", "no_revenue_signal"})
            push(
                82,
                {
                    "candidate_id": f"observe_restrain_{eid}",
                    "candidate_kind": "restrain",
                    "title": f"Hold major build on {pe} — the signal is still thin",
                    "rationale": f"{pe} has very low traffic and an indeterminate trend with no conversion/revenue in the feed — "
                    "large product bets are poorly supported; prefer observation and light instrumentation.",
                    "entities": [eid],
                    "interpretation_patterns": puse,
                    "evidence_summary": f"`{eid}` matches very_low_traffic + trend_indeterminate + no monetization signals.",
                    "strength": _strength(len(puse), kind="restrain"),
                    "next_steps": [
                        f"Keep `{eid}` to measurement-only: weekly traffic check + one funnel hypothesis doc, no multi-surface build.",
                    ],
                    "signal_evidence": ev,
                },
            )
            break

    # 3) Canada concentration
    for eid in entities_ordered:
        block = per_ent.get(eid) if isinstance(per_ent.get(eid), dict) else {}
        pats = set(block.get("patterns") or [])
        if "canada_concentrated_audience" in pats:
            pe = _pretty_entity(eid)
            ev = _sig_evidence_rows(signals, entities={eid}, signal_types={"audience", "engagement"})
            push(
                68,
                {
                    "candidate_id": f"canada_focus_{eid}",
                    "candidate_kind": "action",
                    "title": f"Shape {pe} around Canadian traffic",
                    "rationale": f"Canadian share and engagement are elevated for {pe} — a Canada-first story matches how attention arrives.",
                    "entities": [eid],
                    "interpretation_patterns": ["canada_concentrated_audience"],
                    "evidence_summary": f"`{eid}` shows concentrated Canadian audience in signals.",
                    "strength": _strength(1, kind="action"),
                    "next_steps": [
                        f"Write a one-page mission for `{eid}` that names Canada; then `argus products create` with that scope.",
                    ],
                    "signal_evidence": ev,
                },
            )
            break

    # 4) Engagement without conversion
    for eid in entities_ordered:
        block = per_ent.get(eid) if isinstance(per_ent.get(eid), dict) else {}
        pats = set(block.get("patterns") or [])
        if "engagement_without_conversion" in pats:
            pe = _pretty_entity(eid)
            ev = _sig_evidence_rows(signals, entities={eid}, signal_types={"engagement", "conversion"})
            pu = ["engagement_without_conversion", "no_conversion_signal"]
            push(
                62,
                {
                    "candidate_id": f"engagement_funnel_{eid}",
                    "candidate_kind": "action",
                    "title": f"Turn {pe}'s engagement into one measurable conversion",
                    "rationale": f"Time-on-site exists for {pe} but conversion is zero — pick one conversion action and instrument it.",
                    "entities": [eid],
                    "interpretation_patterns": pu,
                    "evidence_summary": f"`{eid}` has engagement rows with zero conversion.",
                    "strength": _strength(len(pu), kind="action"),
                    "next_steps": [
                        f"Choose one primary action for `{eid}` (signup, purchase, waitlist) and wire a single event before building breadth.",
                    ],
                    "signal_evidence": ev,
                },
            )
            break

    if not pool:
        return None

    pool.sort(key=lambda x: -x[0])
    max_n = 4
    trimmed = [dict(_finalize_candidate(dict(x[1]))) for x in pool[:max_n]]

    primary_id = _pick_primary_candidate_id(trimmed)
    # Move primary to front
    if primary_id:
        ordered: list[dict[str, Any]] = []
        rest: list[dict[str, Any]] = []
        for c in trimmed:
            if str(c.get("candidate_id")) == primary_id:
                ordered.append(c)
            else:
                rest.append(c)
        trimmed = ordered + rest if ordered else trimmed

    situation_summary = _build_situation_summary(
        hi_entity=hi_entity,
        lo_entity=lo_entity,
        entities_ordered=entities_ordered,
    )

    limitations = [
        _ADVISORY_LABEL,
        "primary_candidate_id points to the single direction best supported by current patterns — not a ranked portfolio.",
        "Candidates are capped at four; stronger patterns can displace weaker ones.",
        "Strength is coarse, not a business score.",
    ]

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return {
        "schema": WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA,
        "generated_at_utc": now,
        "advisory_only": True,
        "disclaimer": _ADVISORY_LABEL,
        "source_artifact_rel": "runs/world_context/latest.json",
        "interpretation_artifact_rel": "runs/world_context/interpretation/latest.json",
        "source_world_context_generated_at_utc": world_context.get("generated_at_utc"),
        "interpretation_generated_at_utc": interpretation.get("generated_at_utc"),
        "situation_summary": situation_summary,
        "primary_candidate_id": primary_id,
        "candidates": trimmed,
        "limitations": limitations,
    }


def summarize_candidates_for_operator(payload: dict[str, Any] | None) -> str | None:
    """Compact line for start-here: primary first, then others."""
    if not payload or str(payload.get("schema") or "") != WORLD_CONTEXT_CREATION_CANDIDATES_SCHEMA:
        return None
    cands = payload.get("candidates")
    if not isinstance(cands, list) or not cands:
        return None
    primary = str(payload.get("primary_candidate_id") or "").strip()
    parts_pri: list[str] = []
    parts_rest: list[str] = []
    for c in cands[:4]:
        if not isinstance(c, dict):
            continue
        t = str(c.get("title") or "").strip()
        cid = str(c.get("candidate_id") or "")
        if not t:
            continue
        if primary and cid == primary:
            parts_pri.append(t)
        else:
            parts_rest.append(t)
    bits: list[str] = []
    if parts_pri:
        bits.append("Primary direction (advisory): " + parts_pri[0])
    if parts_rest:
        bits.append("Also consider: " + " · ".join(parts_rest[:3]))
    if not bits:
        return None
    return " · ".join(bits) + " — full detail in operator summary."
