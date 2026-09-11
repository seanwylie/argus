"""
Minimal Builder → portfolio outcome bridge (Phase 3 entry, not full Memory).

``argus.builder_outcome.v1`` is derived only from existing on-disk Builder status/signals artifacts.
Phase 3B adds **artifact-clock timing** vs signals and a **short rolling outcome rollup** (``history_tail.json``),
still observational — not causality, strategy, or portfolio memory.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from argus.builder.history_rollup import builder_history_row_from_status
from argus.builder.status import compute_builder_status
from argus.core.serialize import dumps_json
from argus.signals.persistence import load_latest_bundle

BUILDER_OUTCOME_SCHEMA = "argus.builder_outcome.v1"

AttributionStatus = Literal[
    "not_enough_data",
    "no_material_change_detected",
    "possible_positive_signal_change",
    "possible_negative_signal_change",
]

# What “possible signal change” was compared against (artifact-grounded; not causal).
ComparisonWindowStatus = Literal[
    "unavailable",
    "latest_only",
    "continuity_based",
    "bounded_recent_window",
]

# Strength of comparison evidence for operator summaries (not proof of causality).
ComparisonEvidenceStrength = Literal["none", "weak", "moderate"]

OUTCOME_BRIDGE_DISCLAIMER_SHORT = (
    "Observational only — not causal; does not assert Builder caused signal changes."
)

# Phase 3B — temporal context vs signals (artifact clocks only; not causal latency).
ObservationTimingStatus = Literal[
    "unknown",
    "same_cycle_or_adjacent",
    "recent_followup",
    "delayed_or_uncertain",
]

# Short recent outcome history pattern (deterministic labels; not Builder scoring).
RecentObservationPattern = Literal[
    "insufficient_recent_history",
    "single_observation_only",
    "mixed_recent_observations",
    "repeated_negative_observations",
    "repeated_no_material_change",
]

# Rolling history file: up to 4 prior minimal outcomes (updated on write; see ``write_builder_outcome_artifact``).
_OUTCOME_HISTORY_TAIL = "history_tail.json"
_MAX_HISTORY_TAIL = 4
_MAX_RECENT_ROLLUP = 5

# Builder activity vs signals collection clock comparison (conservative).
_SAME_CYCLE_MAX = timedelta(minutes=30)
_RECENT_FOLLOWUP_MAX = timedelta(hours=48)
_ORDER_SKEW_GRACE = timedelta(minutes=2)


def _parse_iso_utc(value: object) -> datetime | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def _format_delta_summary(builder_dt: datetime, signal_dt: datetime) -> str:
    delta = signal_dt - builder_dt
    sec = int(delta.total_seconds())
    if sec == 0:
        return "Signal collection timestamp matches latest Builder artifact clock (same instant on disk)."
    sign = "after" if sec > 0 else "before"
    ad = abs(sec)
    if ad < 60:
        return f"Signals collection is ~{ad}s {sign} latest Builder invoke/reconcile clock (artifact timestamps only)."
    if ad < 3600:
        return f"Signals collection is ~{ad // 60}m {sign} latest Builder invoke/reconcile clock (artifact timestamps only)."
    if ad < 86400:
        return f"Signals collection is ~{ad // 3600}h {sign} latest Builder invoke/reconcile clock (artifact timestamps only)."
    return f"Signals collection is ~{ad // 86400}d {sign} latest Builder invoke/reconcile clock (artifact timestamps only)."


def derive_observation_timing_context(
    *,
    builder_last_artifact_at_utc: str | None,
    signal_latest_collected_at_utc: str | None,
) -> dict[str, Any]:
    """
    Compare **artifact timestamps** only: latest Builder invoke/reconcile max vs latest signals bundle.

    Does not measure real-world latency or prove the signal run followed the Builder run causally.
    """
    bdt = _parse_iso_utc(builder_last_artifact_at_utc)
    sdt = _parse_iso_utc(signal_latest_collected_at_utc)
    out: dict[str, Any] = {
        "observation_timing_status": "unknown",
        "builder_to_signal_timing_note": "",
        "builder_last_artifact_at_utc": str(builder_last_artifact_at_utc).strip()
        if builder_last_artifact_at_utc
        else None,
        "signal_latest_collected_at_utc": str(signal_latest_collected_at_utc).strip()
        if signal_latest_collected_at_utc
        else None,
        "timing_delta_summary": None,
    }
    if bdt is None or sdt is None:
        out["builder_to_signal_timing_note"] = (
            "Cannot relate Builder and signals clocks — missing or unparsable timestamp on one side."
        )
        out["observation_timing_status"] = "unknown"
        return out

    raw_delta = sdt - bdt
    if raw_delta < -_ORDER_SKEW_GRACE:
        out["observation_timing_status"] = "delayed_or_uncertain"
        out["builder_to_signal_timing_note"] = (
            "Latest signals bundle is **older** than the newest Builder invoke/reconcile timestamps on disk — "
            "signals may not reflect activity after the last Builder run (or clocks skewed)."
        )
        out["timing_delta_summary"] = _format_delta_summary(bdt, sdt)
        return out

    # Treat small negative/zero as adjacent (clock skew / same-second writes).
    delta = sdt - bdt
    if delta <= timedelta(0):
        delta = timedelta(0)

    out["timing_delta_summary"] = _format_delta_summary(bdt, sdt)

    if delta <= _SAME_CYCLE_MAX:
        out["observation_timing_status"] = "same_cycle_or_adjacent"
        out["builder_to_signal_timing_note"] = (
            "Signals collection timestamp is within ~30 minutes of the latest Builder invoke/reconcile clock — "
            "adjacent in artifact time (not proof of same session)."
        )
    elif delta <= _RECENT_FOLLOWUP_MAX:
        out["observation_timing_status"] = "recent_followup"
        out["builder_to_signal_timing_note"] = (
            "Signals collection is later than Builder artifacts but within ~48h on disk — loose follow-up window."
        )
    else:
        out["observation_timing_status"] = "delayed_or_uncertain"
        out["builder_to_signal_timing_note"] = (
            "Large gap between Builder artifact clock and signals collection — treat timing relationship as uncertain."
        )

    return out


def derive_recent_observation_pattern(
    recent_attribution_statuses: list[str],
) -> RecentObservationPattern:
    """
    Deterministic pattern label from the newest-first list of attribution strings (max 5 entries).

    Does not score Builder quality or predict future outcomes.
    """
    raw = [str(x).strip() for x in recent_attribution_statuses if str(x).strip()]
    if not raw:
        return "insufficient_recent_history"
    neg = "possible_negative_signal_change"
    flat = "no_material_change_detected"
    if len(raw) == 1:
        return "single_observation_only"
    neg_n = sum(1 for x in raw if x == neg)
    if neg_n >= 2:
        return "repeated_negative_observations"
    if len(raw) >= 2 and all(x == flat for x in raw):
        return "repeated_no_material_change"
    if len(set(raw)) > 1:
        return "mixed_recent_observations"
    if len(raw) >= 2:
        return "mixed_recent_observations"
    return "single_observation_only"


def _minimal_history_entry_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cp = payload.get("comparison_provenance")
    cpd = cp if isinstance(cp, dict) else {}
    cws = cpd.get("comparison_window_status")
    ev = comparison_evidence_strength(str(cws) if cws is not None else None)
    return {
        "generated_at_utc": payload.get("generated_at_utc"),
        "attribution_status": payload.get("attribution_status"),
        "comparison_evidence_strength": ev,
    }


def _read_outcome_history_tail(repo_root: Path, product_id: str) -> list[dict[str, Any]]:
    p = builder_outcome_dir(repo_root, product_id) / _OUTCOME_HISTORY_TAIL
    if not p.is_file():
        return []
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for item in raw[:_MAX_HISTORY_TAIL]:
        if isinstance(item, dict):
            out.append(item)
    return out


def _write_outcome_history_tail(repo_root: Path, product_id: str, current_payload: dict[str, Any]) -> None:
    """Persist up to 4 prior minimal outcomes for the next ``build_builder_outcome_payload`` rollup."""
    d = builder_outcome_dir(repo_root, product_id)
    d.mkdir(parents=True, exist_ok=True)
    minimal = _minimal_history_entry_from_payload(current_payload)
    prev = _read_outcome_history_tail(repo_root, product_id)
    # ``prev`` was from *before* this write; after write, store [current] + old prev[:3].
    new_tail = [minimal] + prev[: _MAX_HISTORY_TAIL - 1]
    (d / _OUTCOME_HISTORY_TAIL).write_text(dumps_json(new_tail) + "\n", encoding="utf-8")


def build_recent_observation_summary(
    *,
    current_generated_at_utc: str | None,
    current_attribution_status: str | None,
    current_evidence_strength: ComparisonEvidenceStrength,
    history_tail: list[dict[str, Any]],
) -> dict[str, Any]:
    """
    Roll up at most ``_MAX_RECENT_ROLLUP`` observations: **current** plus prior tail (newest first).

    ``history_tail`` entries are **previous** runs only (maintained on disk between writes).
    """
    cur = {
        "generated_at_utc": current_generated_at_utc,
        "attribution_status": current_attribution_status,
        "comparison_evidence_strength": current_evidence_strength,
    }
    merged: list[dict[str, Any]] = [cur]
    for h in history_tail:
        if isinstance(h, dict):
            merged.append(h)
        if len(merged) >= _MAX_RECENT_ROLLUP:
            break

    atts = [str(x.get("attribution_status") or "").strip() for x in merged if isinstance(x, dict)]
    evs = [str(x.get("comparison_evidence_strength") or "").strip() for x in merged if isinstance(x, dict)]
    pattern = derive_recent_observation_pattern(atts)
    note = ""
    if pattern == "insufficient_recent_history":
        note = "No usable attribution history for rollup."
    elif pattern == "single_observation_only":
        note = "Only one recent outcome snapshot in the short window — no repeat pattern."
    return {
        "recent_observation_pattern": pattern,
        "recent_observation_count": len(merged),
        "recent_attribution_statuses": atts,
        "recent_evidence_strengths": evs,
        "recent_observation_note": note or None,
    }


def format_outcome_operator_one_liner(
    *,
    outcome_compact_line: str,
    observation_timing_status: str | None,
    recent_observation_pattern: str | None,
    recent_observation_count: int | None,
) -> str:
    """Single enriched line for portfolio/operator surfaces (still non-causal)."""
    parts = [outcome_compact_line.strip()]
    ts = str(observation_timing_status or "").strip()
    if ts:
        parts.append(f"timing: {ts}")
    rp = str(recent_observation_pattern or "").strip()
    if rp:
        n = recent_observation_count
        if n is not None and n > 0:
            parts.append(f"recent: {rp} (n={n})")
        else:
            parts.append(f"recent: {rp}")
    return " · ".join(p for p in parts if p)


def comparison_evidence_strength(comparison_window_status: str | None) -> ComparisonEvidenceStrength:
    """
    ``none`` / ``weak`` / ``moderate`` reflects how much structured before/after signal data exists.

    Does **not** mean statistical confidence or causal attribution.
    """
    cw = str(comparison_window_status or "").strip()
    if cw in ("unavailable", ""):
        return "none"
    if cw == "latest_only":
        return "weak"
    if cw == "continuity_based":
        return "moderate"
    if cw == "bounded_recent_window":
        # Pairwise timestamps exist but continuity schema label was unexpected — stay conservative.
        return "weak"
    return "weak"


def format_builder_outcome_compact_line(
    *,
    attribution_status: str | None,
    comparison_window_status: str | None,
    comparison_basis: str | None = None,
) -> str:
    """
    Single operator-readable line for portfolio/operator surfaces (non-causal phrasing).

    Kept stable for grep and dashboards; machine detail stays in JSON fields.
    """
    att = str(attribution_status or "unknown").strip() or "unknown"
    cw = str(comparison_window_status or "").strip()
    cb = str(comparison_basis or "").strip()

    if cw == "unavailable":
        compare = "unavailable — no signals snapshot for comparison"
    elif cw == "latest_only":
        compare = "latest_only / current snapshot only"
    elif cw == "continuity_based":
        compare = "continuity_based / adjacent continuity window"
    elif cw == "bounded_recent_window":
        compare = "bounded_recent_window / pairwise prior vs current"
    elif cw:
        compare = f"{cw}" + (f" ({cb})" if cb else "")
    else:
        compare = cb or "unknown"

    return f"Outcome: {att} (compare: {compare})"


def builder_outcome_dir(repo_root: Path, product_id: str) -> Path:
    return Path(repo_root).resolve() / "runs" / "builder" / "outcome" / str(product_id).strip()


def _rel(repo_root: Path, p: Path) -> str:
    root = repo_root.resolve()
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p)


def _builder_blocks_positive_attribution(row: dict[str, Any], osum: dict[str, Any]) -> bool:
    """True when optimistic signal wording must be suppressed (display-only rule)."""
    if row.get("scope_breach") is True:
        return True
    if row.get("path_scope_breach") is True or row.get("semantic_scope_breach") is True:
        return True
    rs = str(row.get("review_status") or "").strip().lower()
    if rs in ("unsafe", "blocked"):
        return True
    eo = str(row.get("execution_outcome") or "").strip().lower()
    if eo in ("breached", "blocked"):
        return True
    tp = str(osum.get("trust_posture") or "").strip()
    if tp in ("unsafe_scope_breach", "blocked"):
        return True
    return False


def _build_comparison_provenance(
    repo_root: Path,
    product_id: str,
    bundle: Any,
    continuity: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Explicit basis for signal-line attribution (what was compared, and what was not).

    When ``continuity_based`` / ``bounded_recent_window`` applies, both refer to the same embedded
    ``argus.signal_continuity.v1`` block (pairwise: immediate prior collection vs current).
    """
    pid = str(product_id).strip()
    signals_path = repo_root / "runs" / "signals" / "latest" / f"{pid}.json"
    sig_rel = _rel(repo_root, signals_path) if signals_path.is_file() else None

    refs: dict[str, Any] = {
        "signals_latest": sig_rel,
        "prior_collected_at_utc": (continuity or {}).get("prior_collected_at_utc"),
        "current_collected_at_utc": (continuity or {}).get("current_collected_at_utc"),
    }

    if bundle is None:
        return {
            "comparison_window_status": "unavailable",
            "comparison_basis": "none",
            "comparison_window_note": (
                "No `runs/signals/latest/<product>.json` — no signals snapshot to compare against."
            ),
            "compared_artifact_refs": refs,
        }

    if not continuity or not continuity.get("compared"):
        return {
            "comparison_window_status": "latest_only",
            "comparison_basis": "signals_latest_snapshot_only",
            "comparison_window_note": (
                "Only the current signals bundle is on disk, or continuity has no prior collection — "
                "no before/after pair for structural signal diff."
            ),
            "compared_artifact_refs": {
                **refs,
                "signals_collected_at_utc": bundle.collected_at_utc,
            },
        }

    # Pairwise diff embedded in latest bundle (consecutive collections only).
    sch = str((continuity or {}).get("schema") or "")
    if sch == "argus.signal_continuity.v1":
        status: ComparisonWindowStatus = "continuity_based"
        basis = "argus.signal_continuity.v1_pairwise"
        note = (
            "Structural diff from embedded `signal_continuity` (appeared/disappeared/regressions/window checks)."
        )
    else:
        status = "bounded_recent_window"
        basis = "signal_continuity_embedded_unlabeled_schema"
        note = (
            "Continuity compared=true but schema string unexpected — treat as bounded pairwise "
            "prior vs current collection timestamps only."
        )

    return {
        "comparison_window_status": status,
        "comparison_basis": basis,
        "comparison_window_note": (
            f"{note} Bounded recent window only: immediate prior `runs/signals` collection vs "
            "this one — not a long baseline or curated “before Builder” snapshot."
        ),
        "compared_artifact_refs": {
            **refs,
            "continuity_schema": (continuity or {}).get("schema"),
            "signals_collected_at_utc": bundle.collected_at_utc,
        },
    }


def _derive_attribution_and_delta(
    *,
    continuity: dict[str, Any] | None,
    blocks_positive: bool,
) -> tuple[AttributionStatus, str]:
    if continuity is None:
        return "not_enough_data", (
            "No `runs/signals/latest/<product>.json` bundle — cannot compare signal snapshots "
            "before/after this Builder window."
        )
    if not continuity.get("compared"):
        return "not_enough_data", (
            "Signals bundle exists but continuity has no prior collection — not enough to compare."
        )

    appeared = len(continuity.get("appeared") or [])
    disappeared = len(continuity.get("disappeared") or [])
    regressed = len(continuity.get("freshness_regressed") or [])
    broken = len(continuity.get("window_continuity_broken") or [])
    neg_hint = regressed > 0 or broken > 0 or disappeared > appeared
    pos_hint = appeared > disappeared and appeared > 0 and not neg_hint

    if blocks_positive:
        if neg_hint:
            return "possible_negative_signal_change", (
                f"Signal continuity shows structural stress ({regressed} freshness regressions, "
                f"{broken} window issues; {appeared} new keys, {disappeared} removed). "
                "Builder posture was also risky or blocked — do not treat as success evidence."
            )
        return "no_material_change_detected", (
            "Builder posture was risky, blocked, or scope-unsafe — positive signal attribution "
            "is not assessed even if signals moved."
        )

    if neg_hint:
        return "possible_negative_signal_change", (
            f"Signal continuity: {regressed} freshness regressions, {broken} window issues; "
            f"{appeared} appeared, {disappeared} removed — possible negative movement (not causal)."
        )
    if pos_hint:
        return "possible_positive_signal_change", (
            f"Signal continuity: {appeared} new keys vs {disappeared} removed — possible positive "
            "movement (not causal; verify in signals JSON)."
        )
    return "no_material_change_detected", (
        f"Signal continuity: {appeared} appeared, {disappeared} removed; no strong directional hint."
    )


def build_builder_outcome_payload(
    repo_root: Path,
    product_id: str,
    *,
    products_dir: Path | None = None,
    status_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Build ``argus.builder_outcome.v1`` from :func:`compute_builder_status` and optional signals bundle.

    When ``status_payload`` is provided, skips a second status compute.
    """
    root = repo_root.resolve()
    pid = str(product_id).strip()
    if not pid:
        raise ValueError("product_id required")

    st = status_payload if status_payload is not None else compute_builder_status(
        root, pid, products_dir=products_dir
    )
    row = builder_history_row_from_status(st)
    osum = st.get("operator_summary") if isinstance(st.get("operator_summary"), dict) else {}

    inv_p = row.get("latest_invoke_path")
    rec_p = row.get("latest_reconcile_path")
    ne_p = row.get("next_expansion_path")
    pt_p = row.get("prepared_task_path")

    bundle = load_latest_bundle(root, pid)
    continuity = bundle.signal_continuity if bundle else None
    signals_path = root / "runs" / "signals" / "latest" / f"{pid}.json"

    blocks = _builder_blocks_positive_attribution(row, osum)
    attribution, delta_summary = _derive_attribution_and_delta(continuity=continuity, blocks_positive=blocks)
    comparison_provenance = _build_comparison_provenance(root, pid, bundle, continuity)

    esc = {
        "operator_visible_escalation": row.get("operator_visible_escalation"),
        "escalation_packet_path_repo": row.get("escalation_packet_path_repo"),
        "reconcile_escalation_emit": row.get("reconcile_escalation_emit"),
    }

    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    lr = st.get("latest_reconcile") if isinstance(st.get("latest_reconcile"), dict) else {}
    target_summary = {
        "target_id": lr.get("current_target_id"),
        "target_type": lr.get("current_target_type") or row.get("target_type_invoke"),
        "group_id": lr.get("group_id"),
    }

    cws = comparison_provenance.get("comparison_window_status")
    cev = comparison_evidence_strength(str(cws) if cws is not None else None)
    history_tail = _read_outcome_history_tail(root, pid)
    observation_timing = derive_observation_timing_context(
        builder_last_artifact_at_utc=row.get("updated_at_utc"),
        signal_latest_collected_at_utc=bundle.collected_at_utc if bundle else None,
    )
    recent_observation_summary = build_recent_observation_summary(
        current_generated_at_utc=generated,
        current_attribution_status=attribution,
        current_evidence_strength=cev,
        history_tail=history_tail,
    )

    return {
        "schema": BUILDER_OUTCOME_SCHEMA,
        "product_id": pid,
        "generated_at_utc": generated,
        "source_artifacts": {
            "invoke_latest": inv_p,
            "reconcile_latest": rec_p,
            "next_expansion": ne_p,
            "prepared_task": pt_p,
            "signals_latest": _rel(root, signals_path) if signals_path.is_file() else None,
        },
        "target_summary": target_summary,
        "execution_contract_kind": row.get("execution_contract_kind"),
        "declared_target_type": row.get("declared_target_type"),
        "invocation_status": row.get("invocation_status"),
        "execution_outcome": row.get("execution_outcome"),
        "merge_readiness": row.get("merge_readiness"),
        "review_status": row.get("review_status"),
        "trust_posture": row.get("trust_posture"),
        "scope": {
            "scope_breach": row.get("scope_breach"),
            "path_scope_breach": row.get("path_scope_breach"),
            "semantic_scope_breach": row.get("semantic_scope_breach"),
        },
        "escalation": esc,
        "signal_comparison": {
            "signals_latest_collected_at_utc": bundle.collected_at_utc if bundle else None,
            "continuity_compared": bool(continuity.get("compared")) if continuity else False,
            "continuity_prior_collected_at_utc": (continuity or {}).get("prior_collected_at_utc"),
            "continuity_schema": (continuity or {}).get("schema"),
        },
        "delta_summary": delta_summary,
        "attribution_status": attribution,
        "comparison_provenance": comparison_provenance,
        "observation_timing": observation_timing,
        "recent_observation_summary": recent_observation_summary,
        "disclaimer": (
            "Observational bridge only: does not claim Builder caused signal changes; "
            "does not update strategy or portfolio memory. Compare signals and reconcile JSON directly."
        ),
    }


def write_builder_outcome_artifact(repo_root: Path, payload: dict[str, Any]) -> Path:
    """Write ``runs/builder/outcome/<product_id>/latest.json``."""
    if str(payload.get("schema") or "") != BUILDER_OUTCOME_SCHEMA:
        raise ValueError("payload must be argus.builder_outcome.v1")
    pid = str(payload.get("product_id") or "").strip()
    if not pid:
        raise ValueError("payload missing product_id")
    d = builder_outcome_dir(repo_root, pid)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    _write_outcome_history_tail(repo_root, pid, payload)
    return p


def read_builder_outcome_latest(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = builder_outcome_dir(repo_root, product_id) / "latest.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != BUILDER_OUTCOME_SCHEMA:
        return None
    return raw


def outcome_summary_for_portfolio(payload: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Light row for embedding in portfolio builder_activity JSON."""
    pid = str(payload.get("product_id") or "").strip()
    cp = payload.get("comparison_provenance")
    cpd = cp if isinstance(cp, dict) else {}
    note = str(cpd.get("comparison_window_note") or "")
    if len(note) > 220:
        note = note[:217] + "…"
    cws = cpd.get("comparison_window_status")
    cbb = cpd.get("comparison_basis")
    att = payload.get("attribution_status")
    ot = payload.get("observation_timing") if isinstance(payload.get("observation_timing"), dict) else {}
    ros = (
        payload.get("recent_observation_summary")
        if isinstance(payload.get("recent_observation_summary"), dict)
        else {}
    )
    base_compact = format_builder_outcome_compact_line(
        attribution_status=str(att) if att is not None else None,
        comparison_window_status=str(cws) if cws is not None else None,
        comparison_basis=str(cbb) if cbb is not None else None,
    )
    rn = ros.get("recent_observation_count")
    rn_i: int | None
    if isinstance(rn, int):
        rn_i = rn
    elif isinstance(rn, float) and rn == int(rn):
        rn_i = int(rn)
    else:
        rn_i = None
    one = format_outcome_operator_one_liner(
        outcome_compact_line=base_compact,
        observation_timing_status=str(ot.get("observation_timing_status") or "") or None,
        recent_observation_pattern=str(ros.get("recent_observation_pattern") or "") or None,
        recent_observation_count=rn_i,
    )
    if len(one) > 320:
        one = one[:317] + "…"
    return {
        "product_id": pid,
        "attribution_status": att,
        "delta_summary": payload.get("delta_summary"),
        "comparison_window_status": cws,
        "comparison_basis": cbb,
        "comparison_window_note": note or None,
        "comparison_evidence_strength": comparison_evidence_strength(
            str(cws) if cws is not None else None
        ),
        "outcome_compact_line": base_compact,
        "outcome_operator_one_liner": one,
        "observation_timing_status": ot.get("observation_timing_status"),
        "builder_to_signal_timing_note": str(ot.get("builder_to_signal_timing_note") or "")[:220]
        if ot.get("builder_to_signal_timing_note")
        else None,
        "recent_observation_pattern": ros.get("recent_observation_pattern"),
        "recent_observation_count": ros.get("recent_observation_count"),
        "recent_attribution_statuses": ros.get("recent_attribution_statuses"),
        "recent_evidence_strengths": ros.get("recent_evidence_strengths"),
        "outcome_disclaimer_short": OUTCOME_BRIDGE_DISCLAIMER_SHORT,
        "artifact_path_repo": _rel(repo_root, builder_outcome_dir(repo_root, pid) / "latest.json"),
    }


__all__ = [
    "BUILDER_OUTCOME_SCHEMA",
    "AttributionStatus",
    "ComparisonWindowStatus",
    "ComparisonEvidenceStrength",
    "ObservationTimingStatus",
    "RecentObservationPattern",
    "OUTCOME_BRIDGE_DISCLAIMER_SHORT",
    "build_recent_observation_summary",
    "comparison_evidence_strength",
    "derive_observation_timing_context",
    "derive_recent_observation_pattern",
    "format_builder_outcome_compact_line",
    "format_outcome_operator_one_liner",
    "build_builder_outcome_payload",
    "builder_outcome_dir",
    "outcome_summary_for_portfolio",
    "read_builder_outcome_latest",
    "write_builder_outcome_artifact",
]
