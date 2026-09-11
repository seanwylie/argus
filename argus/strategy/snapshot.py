"""Write ``runs/strategy/latest/<product_id>.json`` from decisions + evolution + surfaced evidence."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision.persistence import load_latest_product_decisions
from argus.findings.experiment_surfaced import (
    EXPERIMENT_SURFACED_SCHEMA,
    experiment_surfaced_latest_path,
)
from argus.findings.persistence import load_latest_findings

STRATEGY_SNAPSHOT_SCHEMA = "argus.strategy_snapshot.v1"

# How many prior generation files contribute raw posture history (oldest first, max).
STRATEGY_DAMPEN_HISTORY_FILES = 3
# Require current raw posture to match at least this many slots in the dampening window.
STRATEGY_DAMPEN_MIN_MATCHES = 2


def strategy_latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "strategy" / "latest" / f"{product_id}.json"


def strategy_generations_dir(repo_root: Path) -> Path:
    return repo_root.resolve() / "runs" / "strategy" / "generations"


def _recommended_mode_for_posture(posture: str) -> str:
    """Match :func:`_derive_posture` second return value for each posture."""
    return {
        "pivot": "revisit_assumptions_and_shift_focus",
        "double_down": "reinforce_current_bets",
        "explore": "run_more_targeted_experiments",
        "stabilize": "hold_and_integrate_recent_learnings",
    }.get(posture, "hold_and_integrate_recent_learnings")


def _read_posture_raw_from_snapshot_blob(raw: dict[str, Any]) -> str:
    if str(raw.get("posture_raw") or "").strip():
        return str(raw["posture_raw"]).strip()
    return str(raw.get("posture") or "").strip()


def list_strategy_generation_files(repo_root: Path, product_id: str) -> list[Path]:
    """Sorted generation paths for ``product_id`` (filename order = time order)."""
    gdir = strategy_generations_dir(repo_root)
    if not gdir.is_dir():
        return []
    suffix = f"_{product_id}.json"
    return sorted(p for p in gdir.iterdir() if p.is_file() and p.name.endswith(suffix))


def load_last_posture_raws_from_generations(
    repo_root: Path,
    product_id: str,
    *,
    limit: int = STRATEGY_DAMPEN_HISTORY_FILES,
) -> list[str]:
    """
    Load ``posture_raw`` (else ``posture``) from the last ``limit`` generation files, oldest first.
    """
    files = list_strategy_generation_files(repo_root, product_id)
    take = files[-limit:] if len(files) > limit else files
    out: list[str] = []
    for p in take:
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("product_id") or "") != product_id:
            continue
        pr = _read_posture_raw_from_snapshot_blob(raw)
        if pr:
            out.append(pr)
    return out


def apply_posture_dampening(
    posture_raw: str,
    *,
    previous_dampened_posture: str | None,
    history_raws: list[str],
) -> tuple[str, bool]:
    """
    Reduce single-step posture flips.

    Rules (deterministic; first pass):
    - No prior dampened snapshot → accept ``posture_raw``.
    - ``posture_raw`` equals prior dampened → accept (no change).
    - Else build a window of up to three values ending with ``posture_raw``:
      ``[r_{n-2}, r_{n-1}, posture_raw]`` when two prior generation raws exist;
      one prior raw → ``[r_{n-1}, posture_raw]``; none → ``[posture_raw]``.
    - Accept ``posture_raw`` only if it appears at least :data:`STRATEGY_DAMPEN_MIN_MATCHES`
      times in that window (so a one-off flip with no supporting prior raw is rejected).

    Returns ``(final_posture, unused_bool)`` — second value reserved; callers use
    ``posture_raw != final_posture`` for ``posture_changed``.
    """
    if not previous_dampened_posture:
        return posture_raw, False
    if posture_raw == previous_dampened_posture:
        return posture_raw, False

    h = list(history_raws)
    if len(h) >= 2:
        window = h[-2:] + [posture_raw]
    elif len(h) == 1:
        window = h + [posture_raw]
    else:
        window = [posture_raw]

    matches = sum(1 for x in window if x == posture_raw)
    if matches >= STRATEGY_DAMPEN_MIN_MATCHES:
        return posture_raw, False

    return previous_dampened_posture, True


def _norm_words(s: str) -> str:
    return " ".join(str(s).lower().split())


def _theme_label(action_type: str, summary: str) -> str:
    """Coarse theme: action verb + first few words of summary (no NLP)."""
    w = _norm_words(summary).split()
    head = " ".join(w[:4]) if w else "general"
    return f"{action_type}: {head}"


def _extract_theme_signals(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group by coarse theme; classify strengthening / stable / weakening from change_type."""
    groups: dict[str, dict[str, int]] = {}
    for row in candidates:
        if not isinstance(row, dict):
            continue
        at = str(row.get("action_type") or "")
        sm = str(row.get("summary") or "")
        key = _theme_label(at, sm)
        ct = str(row.get("change_type") or "").lower()
        if key not in groups:
            groups[key] = {"up": 0, "flat": 0, "down": 0}
        if ct in ("new", "modified", "modified_content"):
            groups[key]["up"] += 1
        elif ct in ("unchanged", "modified_ranking"):
            groups[key]["flat"] += 1
        else:
            groups[key]["flat"] += 1

    out: list[dict[str, Any]] = []
    for theme, counts in sorted(groups.items()):
        up, flat = counts["up"], counts["flat"]
        if up >= 2 or (up >= 1 and flat >= 1):
            sig = "strengthening"
            basis = "multiple new or modified decisions in this theme"
        elif up == 0 and flat >= 1:
            sig = "stable"
            basis = "unchanged decisions dominate this theme"
        else:
            sig = "emerging"
            basis = "new or isolated decision activity"
        out.append({"theme": theme, "signal": sig, "basis": basis})
    return out[:12]


def _count_non_synthetic_canonical_findings(repo_root: Path, product_id: str) -> int:
    """
    Count findings in the latest canonical bundle that are not experiment-surfaced
    synthetic rows (used for strategy skepticism corroboration).
    """
    fb = load_latest_findings(repo_root, product_id)
    if not fb:
        return 0
    n = 0
    for f in fb.findings:
        ev = f.evidence or {}
        if ev.get("synthetic") is True:
            continue
        if str(ev.get("provenance") or "") == "experiment_surfaced":
            continue
        n += 1
    return n


def _annotate_theme_evidence_basis(
    theme_signals: list[dict[str, Any]],
    *,
    surfaced_n: int,
    canonical_ns: int,
) -> list[dict[str, Any]]:
    """Tag strengthening rows with coarse evidence_basis (deterministic, global counts)."""
    out: list[dict[str, Any]] = []
    for row in theme_signals:
        if not isinstance(row, dict):
            continue
        r = dict(row)
        if str(r.get("signal") or "") != "strengthening":
            out.append(r)
            continue
        if canonical_ns >= 1 and surfaced_n >= 1:
            r["evidence_basis"] = "mixed"
        elif canonical_ns >= 1:
            r["evidence_basis"] = "corroborated"
        else:
            r["evidence_basis"] = "experiment_driven"
        out.append(r)
    return out


def _apply_double_down_skepticism(
    posture_raw: str,
    *,
    surfaced_n: int,
    canonical_ns: int,
    new_c: int,
    modified_c: int,
    removed_c: int,
) -> tuple[str, bool, str]:
    """
    If raw posture is double_down but only experiment-surfaced pressure is visible
    (no non-synthetic canonical findings), downgrade before dampening.
    """
    if posture_raw != "double_down":
        return posture_raw, False, ""
    if canonical_ns >= 1:
        return posture_raw, False, ""
    if surfaced_n < 1:
        return posture_raw, False, ""
    low_pressure = surfaced_n <= 1
    no_churn = new_c == 0 and modified_c == 0 and removed_c == 0
    if low_pressure and no_churn:
        return (
            "stabilize",
            True,
            "double_down_suppressed_without_canonical_corroboration_stable",
        )
    return (
        "explore",
        True,
        "double_down_suppressed_without_canonical_corroboration",
    )


def _derive_posture(
    *,
    new_c: int,
    unchanged_c: int,
    modified_c: int,
    removed_c: int,
    surfaced_n: int,
    theme_signals: list[dict[str, Any]],
) -> tuple[str, str]:
    """
    Deterministic posture from evolution counts + surfaced findings.

    Order of evaluation (first match wins):
    1. **pivot** — structural churn: any removals, or heavy modification churn.
    2. **explore** — multiple evolving decisions while experiment evidence exists.
    3. **double_down** — low churn, stable core, and experiment signal present with strengthening theme.
    4. **stabilize** — mostly unchanged, little net change, minimal new experiment evidence pressure.
    5. **explore** — default when ambiguous (bias toward learning).
    """
    churn = modified_c + new_c
    if removed_c >= 1 or modified_c >= 3:
        return (
            "pivot",
            "revisit_assumptions_and_shift_focus",
        )
    strengthening = any(
        str(t.get("signal") or "") == "strengthening" for t in theme_signals
    )
    # Concentrated momentum: experiment evidence + strengthening theme, low churn, no removals.
    if (
        removed_c == 0
        and churn <= 2
        and surfaced_n >= 1
        and strengthening
        and unchanged_c >= 1
        and modified_c <= 2
    ):
        return (
            "double_down",
            "reinforce_current_bets",
        )
    if churn >= 2 and surfaced_n >= 1:
        return (
            "explore",
            "run_more_targeted_experiments",
        )
    if unchanged_c >= new_c + modified_c and modified_c <= 1 and removed_c == 0 and churn <= 1 and surfaced_n <= 1:
        return (
            "stabilize",
            "hold_and_integrate_recent_learnings",
        )
    if surfaced_n >= 1:
        return (
            "explore",
            "run_more_targeted_experiments",
        )
    return (
        "stabilize",
        "hold_and_integrate_recent_learnings",
    )


def _summary_line(posture: str, ev: dict[str, Any]) -> str:
    return (
        f"Posture={posture}; "
        f"decision churn new={ev['new_decisions']} unchanged={ev['unchanged_decisions']} "
        f"modified={ev['modified_decisions']} removed={ev['removed_decisions']}; "
        f"surfaced_findings={ev['surfaced_findings_count']}."
    )


def build_strategy_snapshot(repo_root: Path, product_id: str) -> dict[str, Any]:
    """Build JSON-serializable strategy snapshot (no writes)."""
    root = repo_root.resolve()
    raw = load_latest_product_decisions(root, product_id)
    if not isinstance(raw, dict):
        raise ValueError("missing_or_invalid_decisions_latest")

    src_dec = str(raw.get("generated_at_utc") or "").strip()
    cands = [c for c in (raw.get("candidates") or []) if isinstance(c, dict)]
    devo = raw.get("decision_evolution") if isinstance(raw.get("decision_evolution"), dict) else {}
    new_c = int(devo.get("new_count") or 0)
    unchanged_c = int(devo.get("unchanged_count") or 0)
    modified_c = int(devo.get("modified_count") or 0)
    removed_c = int(devo.get("removed_count") or 0)
    if not devo:
        new_c = len(cands)
        unchanged_c = modified_c = removed_c = 0

    sp = experiment_surfaced_latest_path(root, product_id)
    src_surf: str | None = None
    surfaced_n = 0
    if sp.is_file():
        try:
            raw_s = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_s = None
        if isinstance(raw_s, dict) and str(raw_s.get("schema") or "") == EXPERIMENT_SURFACED_SCHEMA:
            src_surf = str(raw_s.get("generated_at_utc") or "").strip() or None
            findings = raw_s.get("findings") or []
            if isinstance(findings, list):
                surfaced_n = len(findings)

    canonical_ns = _count_non_synthetic_canonical_findings(root, product_id)
    theme_signals = _extract_theme_signals(cands)
    theme_signals = _annotate_theme_evidence_basis(
        theme_signals,
        surfaced_n=surfaced_n,
        canonical_ns=canonical_ns,
    )
    posture_raw, _recommended_mode_raw = _derive_posture(
        new_c=new_c,
        unchanged_c=unchanged_c,
        modified_c=modified_c,
        removed_c=removed_c,
        surfaced_n=surfaced_n,
        theme_signals=theme_signals,
    )
    posture_raw, skepticism_applied, skepticism_reason = _apply_double_down_skepticism(
        posture_raw,
        surfaced_n=surfaced_n,
        canonical_ns=canonical_ns,
        new_c=new_c,
        modified_c=modified_c,
        removed_c=removed_c,
    )

    previous_dampened: str | None = None
    lp = strategy_latest_path(root, product_id)
    if lp.is_file():
        try:
            prev_blob = json.loads(lp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            prev_blob = None
        if isinstance(prev_blob, dict) and str(prev_blob.get("product_id") or "") == product_id:
            previous_dampened = str(prev_blob.get("posture") or "").strip() or None

    history_raws = load_last_posture_raws_from_generations(
        root, product_id, limit=STRATEGY_DAMPEN_HISTORY_FILES
    )
    posture, _ = apply_posture_dampening(
        posture_raw,
        previous_dampened_posture=previous_dampened,
        history_raws=history_raws,
    )
    recommended_mode = _recommended_mode_for_posture(posture)
    posture_changed = posture_raw != posture

    evidence = {
        "new_decisions": new_c,
        "unchanged_decisions": unchanged_c,
        "modified_decisions": modified_c,
        "removed_decisions": removed_c,
        "surfaced_findings_count": surfaced_n,
    }
    summary = _summary_line(posture, evidence)

    ts = datetime.now(timezone.utc).isoformat()
    snap: dict[str, Any] = {
        "schema": STRATEGY_SNAPSHOT_SCHEMA,
        "schema_version": "1",
        "product_id": product_id,
        "generated_at_utc": ts,
        "source_decisions_generated_at_utc": src_dec,
        "summary": summary,
        "posture_raw": posture_raw,
        "posture": posture,
        "posture_changed": posture_changed,
        "posture_history": history_raws,
        "theme_signals": theme_signals,
        "recommended_mode": recommended_mode,
        "evidence": evidence,
        "skepticism_applied": skepticism_applied,
        "skepticism_reason": skepticism_reason if skepticism_applied else None,
    }
    if src_surf:
        snap["source_experiment_surfaced_generated_at_utc"] = src_surf
    return snap


def save_strategy_snapshot(repo_root: Path, product_id: str, snapshot: dict[str, Any]) -> Path:
    """Persist timestamped generation + latest symlink-style latest file."""
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    gen = strategy_generations_dir(root)
    gen.mkdir(parents=True, exist_ok=True)
    out = gen / f"{ts}_{product_id}.json"
    out.write_text(dumps_json(snapshot) + "\n", encoding="utf-8")
    lp = strategy_latest_path(root, product_id)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(dumps_json(snapshot) + "\n", encoding="utf-8")
    return out
