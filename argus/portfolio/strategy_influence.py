"""
Soft, bounded influence from portfolio strategic posture on creation and operator queue.

Nudges are weights and recommendations only — they do not replace safety or eligibility logic
elsewhere. Missing or stale strategy artifacts yield neutral influence (backward compatible).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

# Local path/schema constants only — avoid importing ``strategy`` (would circular-import via operator_queue).
_PORTFOLIO_STRATEGY_ARTIFACT_SCHEMA = "argus.portfolio_strategy.v1"


def _portfolio_strategy_latest_path(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "strategy" / "latest.json"


STRATEGY_INFLUENCE_SCHEMA = "argus.portfolio_strategy_influence.v1"

# Bounded additive queue nudges (same units as priority_score components, typically single digits).
_MAX_QUEUE_NUDGE = 1.5


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def load_latest_strategic_posture(repo_root: Path) -> tuple[str | None, dict[str, Any] | None]:
    """
    Return ``(posture, strategy_payload_or_none)`` from ``runs/portfolio/strategy/latest.json``.
    """
    root = repo_root.resolve()
    raw = _load_json(_portfolio_strategy_latest_path(root))
    if raw is None or str(raw.get("schema") or "") != _PORTFOLIO_STRATEGY_ARTIFACT_SCHEMA:
        return None, None
    posture = str(raw.get("strategic_posture") or "").strip().lower()
    if not posture:
        return None, raw
    return posture, raw


def describe_soft_influence(posture: str | None) -> dict[str, Any]:
    """Human-readable downstream hints for strategy artifacts (inspectable, non-executable)."""
    p = posture or "unknown"
    hints: dict[str, str] = {
        "creation": "neutral — no posture-specific creation bias.",
        "operator_queue": "neutral — base queue scoring only.",
        "lifecycle_cleanup": "neutral",
    }
    if posture == "create":
        hints["creation"] = "elevated — prefer highlighting creation proposals and mission gaps."
        hints["operator_queue"] = "slight bias toward early-stage products when ranking."
    elif posture == "repair":
        hints["creation"] = "suppressed appetite — keep proposals visible; de-emphasize net-new expansion."
        hints["operator_queue"] = "elevate blocked/intervention-heavy products in ranking (bounded nudge)."
    elif posture == "expand":
        hints["creation"] = "moderate expansion bias — align new work with positive trajectory."
        hints["operator_queue"] = "slight bias toward advance-ready products."
    elif posture in ("consolidate", "harvest"):
        hints["creation"] = "restrained — reduce expansion bias; prefer fewer parallel bets."
        hints["operator_queue"] = "slight de-emphasis of pure advance-ready rows vs stabilization."
    elif posture == "retire":
        hints["creation"] = "restrained — favor explicit exits and cleanup over new surface area."
        hints["lifecycle_cleanup"] = "elevate late-stage / negative-trajectory attention in queue nudges."
    return {
        "schema": STRATEGY_INFLUENCE_SCHEMA,
        "strategic_posture": p,
        "downstream_hints": hints,
        "bounded": True,
        "max_queue_priority_nudge": _MAX_QUEUE_NUDGE,
    }


def creation_influence_for_posture(
    posture: str | None,
    *,
    proposal_count: int,
) -> dict[str, Any]:
    """
    Fields merged into creation proposal payloads (top-level and per-proposal).

    Does not remove proposals or change gap detection — surfacing and appetite labels only.
    """
    p = posture
    appetite = "neutral"
    surfacing_default = "normal"
    emphasis = 0.5
    notes: list[str] = []

    if p == "create":
        appetite = "elevated"
        surfacing_default = "highlight"
        emphasis = 0.85
        notes.append(
            "Portfolio posture `create`: creation proposals receive stronger recommendation emphasis."
        )
        if proposal_count >= 1:
            notes.append("Treat proposals as an active portfolio program — rank and staff explicitly.")
    elif p == "repair":
        appetite = "suppressed"
        surfacing_default = "muted"
        emphasis = 0.25
        notes.append(
            "Portfolio posture `repair`: net-new creation appetite is softly suppressed; "
            "proposals remain for visibility — stabilize existing work first."
        )
    elif p in ("consolidate", "retire", "harvest"):
        appetite = "restrained"
        surfacing_default = "subdued"
        emphasis = 0.35
        notes.append(
            f"Portfolio posture `{p}`: reduce expansion bias; prefer consolidation, harvest, or exit discipline."
        )
    elif p == "expand":
        appetite = "expansion_friendly"
        surfacing_default = "normal"
        emphasis = 0.65
        notes.append("Portfolio posture `expand`: favor growth-aligned creation when gaps justify it.")
    else:
        notes.append("No strategic posture loaded — creation influence is neutral (backward compatible).")

    return {
        "schema": STRATEGY_INFLUENCE_SCHEMA,
        "strategic_posture": p,
        "creation_appetite": appetite,
        "creation_emphasis_score": emphasis,
        "default_proposal_surfacing": surfacing_default,
        "notes": notes,
        "bounded": True,
    }


def apply_proposal_strategy_influence(
    proposals: list[dict[str, Any]],
    top_level: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return new list with per-proposal ``strategy_influence`` attached."""
    default_surf = str(top_level.get("default_proposal_surfacing") or "normal")
    appetite = str(top_level.get("creation_appetite") or "neutral")
    out: list[dict[str, Any]] = []
    for pr in proposals:
        row = dict(pr)
        sev = str((row.get("evidence_summary") or {}).get("gap_severity") or "").lower()
        surf = default_surf
        if default_surf == "subdued":
            surf = "muted"
        if default_surf == "highlight" and sev == "low":
            surf = "normal"
        note = f"Surfacing `{surf}` under portfolio creation appetite `{appetite}`."
        row["strategy_influence"] = {
            "surfacing": surf,
            "note": note,
        }
        out.append(row)
    return out


def queue_priority_nudge(
    posture: str | None,
    *,
    lifecycle_stage: str | None,
    orchestration_status: str | None,
    readiness_tier: str | None,
    recommendation: str | None,
) -> tuple[float, dict[str, Any]]:
    """
    Bounded additive nudge to operator queue priority_score (inspectable breakdown).

    Does not remove or block products — only re-orders emphasis within existing scoring.
    """
    ls = (lifecycle_stage or "").strip().lower()
    orch = (orchestration_status or "").strip().lower()
    tier = (readiness_tier or "").strip().lower()
    rec = (recommendation or "").strip().lower()

    parts: dict[str, float] = {}
    nudge = 0.0

    if posture == "repair":
        if "blocked" in orch or "blocked_waiting" in orch:
            parts["repair_blocked_or_waiting"] = 0.85
        if "import" in rec and ("fail" in rec or "repair" in rec or "refresh" in rec):
            parts["repair_import_stress"] = 0.45
        if "intervention" in rec or "stabil" in rec:
            parts["repair_intervention_language"] = 0.35
    elif posture == "create":
        if ls in ("idea", "build", "validate"):
            parts["create_early_lifecycle"] = 0.4
    elif posture == "expand":
        if "advance" in tier:
            parts["expand_advance_ready"] = 0.35
    elif posture in ("consolidate", "harvest"):
        if "advance" in tier:
            parts["consolidate_deemphasize_advance_only"] = -0.3
        if ls in ("maintain", "decline"):
            parts["consolidate_stabilization"] = 0.25
    elif posture == "retire":
        if ls in ("decline", "kill", "maintain"):
            parts["retire_late_lifecycle_attention"] = 0.5
        if "advance" in tier and ls not in ("decline", "kill"):
            parts["retire_deemphasize_pure_advance"] = -0.25

    nudge = sum(parts.values())
    cap = float(_MAX_QUEUE_NUDGE)
    if nudge > cap:
        scale = cap / nudge if nudge else 1.0
        parts = {k: round(v * scale, 4) for k, v in parts.items()}
        nudge = round(sum(parts.values()), 4)
    elif nudge < -cap:
        scale = -cap / nudge if nudge else 1.0
        parts = {k: round(v * scale, 4) for k, v in parts.items()}
        nudge = round(sum(parts.values()), 4)

    meta = {
        "strategic_posture": posture,
        "components": parts,
        "total_nudge": round(nudge, 4),
        "bounded": True,
        "cap": cap,
    }
    return round(nudge, 4), meta


def cycle_strategy_context(posture: str | None) -> dict[str, Any]:
    """Attach to portfolio cycle summary — does not change safety or overall recommendation enums."""
    if not posture:
        return {
            "schema": STRATEGY_INFLUENCE_SCHEMA,
            "loaded": False,
            "operator_cycle_note": None,
        }
    return {
        "schema": STRATEGY_INFLUENCE_SCHEMA,
        "loaded": True,
        "strategic_posture": posture,
        "operator_cycle_note": (
            f"Soft strategy context: `{posture}` nudges creation emphasis and operator queue priority "
            f"(bounded; inspect `portfolio_strategy` and `operator_queue` JSON)."
        ),
    }


__all__ = [
    "STRATEGY_INFLUENCE_SCHEMA",
    "apply_proposal_strategy_influence",
    "creation_influence_for_posture",
    "cycle_strategy_context",
    "describe_soft_influence",
    "load_latest_strategic_posture",
    "queue_priority_nudge",
]
