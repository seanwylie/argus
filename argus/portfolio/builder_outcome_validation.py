"""
Aggregate view of on-disk ``argus.builder_outcome.v1`` artifacts — calibration / shape only.

**Not** Builder scoring, portfolio strategy, or causal attribution. Derived only from
``runs/builder/outcome/<product_id>/latest.json`` when present.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.builder.outcome import comparison_evidence_strength, read_builder_outcome_latest
from argus.core.serialize import dumps_json

BUILDER_OUTCOME_VALIDATION_SCHEMA = "argus.builder_outcome_validation.v1"

_VALIDATION_DISCLAIMER = (
    "Validates the observational Phase 3 layer only — not Builder effectiveness, not causal claims, "
    "not strategy or portfolio memory."
)

_LIST_CAP = 24

_INTERP_DISCLAIMER = (
    "Deterministic summary from aggregate counts and lists only — not product quality, "
    "not Builder success, not causality or strategy."
)


def derive_validation_interpretation(report: dict[str, Any]) -> dict[str, Any]:
    """
    Conservative, deterministic lines derived **only** from validation aggregates.

    Safe to call on a partial report dict (e.g. loaded artifact missing new keys).
    """
    n = int(report.get("products_with_outcome_artifact") or 0)
    ev = report.get("counts_by_comparison_evidence_strength")
    ev = ev if isinstance(ev, dict) else {}
    tm = report.get("counts_by_observation_timing_status")
    tm = tm if isinstance(tm, dict) else {}
    att = report.get("counts_by_attribution_status")
    att = att if isinstance(att, dict) else {}
    recent = report.get("counts_by_recent_observation_pattern")
    recent = recent if isinstance(recent, dict) else {}

    weak_n = int(ev.get("none", 0) or 0) + int(ev.get("weak", 0) or 0)
    unk_t = int(tm.get("unknown", 0) or 0) + int(tm.get("delayed_or_uncertain", 0) or 0)
    same_adj = int(tm.get("same_cycle_or_adjacent", 0) or 0)

    rep_neg = report.get("products_repeated_negative_observations") or []
    rep_neg_n = len(rep_neg) if isinstance(rep_neg, list) else 0

    wmaj = report.get("products_weak_majority_recent_window") or []
    wmaj_n = len(wmaj) if isinstance(wmaj, list) else 0

    wcur = report.get("products_weak_current_evidence") or []
    wcur_n = len(wcur) if isinstance(wcur, list) else 0

    ned = int(att.get("not_enough_data", 0) or 0)
    single_obs = int(recent.get("single_observation_only", 0) or 0)
    mixed_pat = int(recent.get("mixed_recent_observations", 0) or 0)

    headline: str
    if n <= 0:
        headline = "No readable builder outcome artifacts — nothing to calibrate yet."
    elif rep_neg_n >= 1:
        headline = (
            "Repeated negative observation patterns are present for at least one product "
            "(see list below; observational only)."
        )
    elif n >= 1 and weak_n >= n:
        headline = "All recorded outcomes rely on weak or unavailable comparison evidence for signals."
    elif n >= 2 and weak_n * 2 >= n * 3:
        headline = "Most current Builder outcome evidence is weak or unavailable for signal comparison."
    elif n >= 2 and unk_t * 2 >= n:
        headline = "Timing versus signals is often uncertain — treat observational reads cautiously."
    elif mixed_pat >= 1:
        headline = "Recent observation shapes are mixed across products — check pattern counts below."
    else:
        headline = "Builder observational snapshots are present — use aggregates below to judge coverage shape."

    lines: list[str] = []
    if n == 1:
        lines.append("Only one outcome snapshot — repeat-pattern labels are inherently limited.")
    if n >= 2 and ned * 2 >= n * 3:
        lines.append(
            "Attribution is often `not_enough_data` — limited pairwise signal material in the snapshot set."
        )
    if n >= 2 and single_obs * 2 >= n * 3:
        lines.append(
            "Recent-pattern labels skew toward single-observation — few multi-run repeats in this window."
        )
    if wcur_n >= 2:
        lines.append(
            "Several products show none or weak comparison evidence on the current snapshot (see list below)."
        )
    elif wcur_n == 1 and n >= 2:
        lines.append("At least one product shows none or weak comparison evidence on the current snapshot.")
    if wmaj_n >= 1:
        lines.append(
            "Some products show a majority of weak evidence entries in the short recent window (list below)."
        )
    if n >= 2 and same_adj * 2 >= n and unk_t == 0:
        lines.append(
            "Timing versus Builder artifacts is often adjacent for several products — still artifact clocks only."
        )
    if not lines and n >= 1:
        lines.append("Observational coverage looks ordinary for this snapshot — still verify per-product JSON.")

    # attention level: plain words, not scoring
    if n <= 0:
        level = "none"
    elif rep_neg_n >= 1 or (n >= 2 and (weak_n * 2 >= n * 3 or unk_t * 2 >= n)) or wmaj_n >= 3:
        level = "attention_suggested"
    else:
        level = "ordinary"

    return {
        "validation_headline": headline,
        "validation_interpretation_lines": lines[:8],
        "validation_attention_level": level,
        "validation_interpretation_disclaimer": _INTERP_DISCLAIMER,
    }


def _outcome_root(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "builder" / "outcome"


def list_builder_outcome_product_ids(repo_root: Path) -> list[str]:
    """Product ids that have ``runs/builder/outcome/<id>/latest.json`` on disk."""
    base = _outcome_root(repo_root)
    if not base.is_dir():
        return []
    ids: list[str] = []
    for child in sorted(base.iterdir(), key=lambda p: p.name):
        if child.is_dir() and (child / "latest.json").is_file():
            ids.append(child.name)
    return ids


def _evidence_from_payload(payload: dict[str, Any]) -> str:
    cp = payload.get("comparison_provenance")
    cpd = cp if isinstance(cp, dict) else {}
    return comparison_evidence_strength(str(cpd.get("comparison_window_status") or None))


def _timing_status_from_payload(payload: dict[str, Any]) -> str:
    ot = payload.get("observation_timing")
    otd = ot if isinstance(ot, dict) else {}
    s = str(otd.get("observation_timing_status") or "").strip()
    return s if s else "unknown"


def _recent_pattern_from_payload(payload: dict[str, Any]) -> str:
    rs = payload.get("recent_observation_summary")
    rsd = rs if isinstance(rs, dict) else {}
    s = str(rsd.get("recent_observation_pattern") or "").strip()
    return s if s else "insufficient_recent_history"


def _weak_majority_recent(payload: dict[str, Any]) -> bool:
    rs = payload.get("recent_observation_summary")
    if not isinstance(rs, dict):
        return False
    evs = rs.get("recent_evidence_strengths")
    if not isinstance(evs, list) or len(evs) < 2:
        return False
    weakish = frozenset({"none", "weak"})
    vals = [str(x).strip().lower() for x in evs if str(x).strip()]
    if not vals:
        return False
    n_weak = sum(1 for x in vals if x in weakish)
    return (n_weak / len(vals)) >= 0.5


def build_builder_outcome_validation_report(repo_root: Path) -> dict[str, Any]:
    """
    Deterministic counts and short product lists from existing builder outcome JSON only.

    Products without a readable ``latest.json`` are omitted (not counted as failures here).
    """
    root = repo_root.resolve()
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    pids = list_builder_outcome_product_ids(root)

    cnt_att: Counter[str] = Counter()
    cnt_ev: Counter[str] = Counter()
    cnt_time: Counter[str] = Counter()
    cnt_recent: Counter[str] = Counter()

    repeated_neg: list[str] = []
    weak_current: list[str] = []
    weak_majority: list[str] = []

    loaded = 0
    for pid in pids:
        raw = read_builder_outcome_latest(root, pid)
        if not raw:
            continue
        loaded += 1
        att = str(raw.get("attribution_status") or "").strip() or "unknown"
        cnt_att[att] += 1

        ev = _evidence_from_payload(raw)
        cnt_ev[ev] += 1

        ts = _timing_status_from_payload(raw)
        cnt_time[ts] += 1

        rp = _recent_pattern_from_payload(raw)
        cnt_recent[rp] += 1

        if rp == "repeated_negative_observations":
            repeated_neg.append(pid)

        if ev in ("none", "weak"):
            weak_current.append(pid)

        if _weak_majority_recent(raw):
            weak_majority.append(pid)

    repeated_neg = sorted(set(repeated_neg))[:_LIST_CAP]
    weak_current = sorted(set(weak_current))[:_LIST_CAP]
    weak_majority = sorted(set(weak_majority))[:_LIST_CAP]

    base: dict[str, Any] = {
        "schema": BUILDER_OUTCOME_VALIDATION_SCHEMA,
        "generated_at_utc": generated,
        "source_glob": "runs/builder/outcome/*/latest.json",
        "disclaimer": _VALIDATION_DISCLAIMER,
        "products_with_outcome_artifact": loaded,
        "outcome_directories_seen": len(pids),
        "counts_by_attribution_status": dict(sorted(cnt_att.items())),
        "counts_by_comparison_evidence_strength": dict(sorted(cnt_ev.items())),
        "counts_by_observation_timing_status": dict(sorted(cnt_time.items())),
        "counts_by_recent_observation_pattern": dict(sorted(cnt_recent.items())),
        "products_repeated_negative_observations": repeated_neg,
        "products_weak_current_evidence": weak_current,
        "products_weak_majority_recent_window": weak_majority,
    }
    base.update(derive_validation_interpretation(base))
    return base


def builder_outcome_validation_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "builder_outcome_validation"


def write_builder_outcome_validation_artifact(
    repo_root: Path,
    payload: dict[str, Any] | None = None,
) -> Path:
    """Write ``runs/portfolio/builder_outcome_validation/latest.json``."""
    pl = payload if payload is not None else build_builder_outcome_validation_report(repo_root)
    if str(pl.get("schema") or "") != BUILDER_OUTCOME_VALIDATION_SCHEMA:
        raise ValueError("payload must be argus.builder_outcome_validation.v1")
    d = builder_outcome_validation_dir(repo_root)
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    return p


def load_builder_outcome_validation_latest(repo_root: Path) -> dict[str, Any] | None:
    p = builder_outcome_validation_dir(repo_root) / "latest.json"
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("schema") != BUILDER_OUTCOME_VALIDATION_SCHEMA:
        return None
    if not str(raw.get("validation_headline") or "").strip():
        raw = {**raw, **derive_validation_interpretation(raw)}
    return raw


__all__ = [
    "BUILDER_OUTCOME_VALIDATION_SCHEMA",
    "build_builder_outcome_validation_report",
    "builder_outcome_validation_dir",
    "derive_validation_interpretation",
    "list_builder_outcome_product_ids",
    "load_builder_outcome_validation_latest",
    "write_builder_outcome_validation_artifact",
]
