"""
Product deprecation proposals — deterministic candidates for retire, harvest, archive, or repair-first.

Proposal-only: does not change products, orchestration, or portfolio state. Uses the same artifact
sources as portfolio refresh (outcomes, strategy, intervention inbox, operator queue, patterns).
"""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.intervention_inbox import (
    INTERVENTION_INBOX_SCHEMA,
    build_intervention_inbox_payload,
)
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.patterns import PORTFOLIO_PATTERNS_SCHEMA
from argus.portfolio.strategy_influence import load_latest_strategic_posture
from argus.products.inventory import build_inventory

PRODUCT_DEPRECATION_PROPOSALS_SCHEMA = "argus.product_deprecation_proposals.v1"

DEPRECATION_POSTURES: tuple[str, ...] = ("retire", "harvest", "archive", "repair_instead")
# When two postures tie on score, prefer caution / remediation first.
_POSTURE_TIE_ORDER: tuple[str, ...] = ("repair_instead", "retire", "archive", "harvest")

_MIN_SCORE_TO_PROPOSE = 4.0


def deprecation_proposals_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "deprecation"


def _load_json(path: Path) -> dict[str, Any] | None:
    import json

    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _load_portfolio_outcomes(repo_root: Path) -> dict[str, Any] | None:
    raw = _load_json(repo_root / "runs" / "portfolio" / "outcomes" / "latest.json")
    if raw and str(raw.get("schema") or "") == PORTFOLIO_OUTCOMES_SCHEMA:
        return raw
    return None


def _load_portfolio_patterns(repo_root: Path) -> dict[str, Any] | None:
    raw = _load_json(repo_root / "runs" / "portfolio" / "patterns" / "latest.json")
    if raw and str(raw.get("schema") or "") == PORTFOLIO_PATTERNS_SCHEMA:
        return raw
    return None


def _load_operator_queue(repo_root: Path) -> dict[str, Any] | None:
    raw = _load_json(repo_root / "runs" / "portfolio" / "operator_queue" / "latest.json")
    if raw and str(raw.get("schema") or "") == OPERATOR_QUEUE_SCHEMA:
        return raw
    return None


def _proposal_id(product_id: str, posture: str, salt: str) -> str:
    h = hashlib.sha256(f"{product_id}|{posture}|{salt}".encode()).hexdigest()[:12]
    return f"deprecation_{h}"


def _pattern_hits_for_product(patterns: dict[str, Any] | None, product_id: str) -> list[dict[str, Any]]:
    if not patterns:
        return []
    out: list[dict[str, Any]] = []
    for p in patterns.get("detected_patterns") or []:
        if not isinstance(p, dict):
            continue
        aff = p.get("affected_products") or []
        if isinstance(aff, list) and product_id in [str(x) for x in aff]:
            out.append(
                {
                    "pattern_id": p.get("pattern_id"),
                    "severity": p.get("severity"),
                    "title": p.get("title"),
                }
            )
    return out


def _inbox_evidence_for_product(inbox: dict[str, Any] | None, product_id: str) -> dict[str, Any]:
    if not inbox or str(inbox.get("schema") or "") != INTERVENTION_INBOX_SCHEMA:
        return {"active_items": [], "high_severity_active": 0, "recurring_count": 0}
    active: list[dict[str, Any]] = []
    for it in inbox.get("open_items") or []:
        if not isinstance(it, dict):
            continue
        if str(it.get("product_id") or "") != product_id:
            continue
        if not it.get("in_active_queue"):
            continue
        active.append(
            {
                "item_id": it.get("item_id"),
                "severity": it.get("severity"),
                "intervention_category": it.get("intervention_category"),
                "recurring": it.get("recurring"),
            }
        )
    high = sum(1 for x in active if str(x.get("severity") or "") == "high")
    rec = sum(1 for x in active if x.get("recurring"))
    return {"active_items": active, "high_severity_active": high, "recurring_count": rec}


def _score_postures(
    *,
    lifecycle_stage: str,
    outcome: dict[str, Any] | None,
    queue_entry: dict[str, Any] | None,
    inbox_ev: dict[str, Any],
    pattern_hits: list[dict[str, Any]],
    strategic_posture: str | None,
) -> dict[str, float]:
    """Higher = stronger fit for that deprecation posture (deterministic)."""
    ls = lifecycle_stage.strip().lower()
    traj = str((outcome or {}).get("overall_trajectory") or "")
    mi = (outcome or {}).get("mission_interpretation") or {}
    if not isinstance(mi, dict):
        mi = {}
    malign = str(mi.get("mission_alignment") or "neutral")
    int_pat = str((outcome or {}).get("intervention_pattern") or "")
    blk_pat = str((outcome or {}).get("blocked_pattern") or "")

    tier = str((queue_entry or {}).get("readiness_tier") or "").lower()
    orch = str((queue_entry or {}).get("orchestration_status") or "").lower()

    scores = {p: 0.0 for p in DEPRECATION_POSTURES}

    # --- repair_instead: structural/intervention burden before exit ---
    if int_pat in ("repeated", "newly_flagged"):
        # Strong signal — prefer remediation before wind-down when both stress and trajectory conflict.
        scores["repair_instead"] += 8.0
        scores["retire"] -= 3.0
        scores["archive"] -= 2.0
    if blk_pat in ("persisted", "newly_blocked"):
        scores["repair_instead"] += 3.0
    if inbox_ev.get("high_severity_active", 0) >= 1:
        scores["repair_instead"] += 3.0
    if int(inbox_ev.get("recurring_count") or 0) >= 1:
        scores["repair_instead"] += 2.0
    if strategic_posture == "repair":
        scores["repair_instead"] += 2.0
    if "blocked" in orch:
        scores["repair_instead"] += 1.5

    # --- retire: sustained poor trajectory / late lifecycle ---
    if traj == "negative":
        scores["retire"] += 6.0
    if malign == "negative":
        scores["retire"] += 3.0
    if ls in ("decline", "kill"):
        scores["retire"] += 5.0
    if strategic_posture == "retire":
        scores["retire"] += 3.0
    for ph in pattern_hits:
        if str(ph.get("severity") or "") == "high":
            scores["retire"] += 2.0
            break

    # --- harvest: positive trajectory, extraction window ---
    if traj == "positive":
        scores["harvest"] += 5.0
    if malign == "positive":
        scores["harvest"] += 2.0
    if "advance" in tier:
        scores["harvest"] += 3.0
    if strategic_posture == "harvest":
        scores["harvest"] += 3.0
    if ls in ("grow", "maintain") and traj == "positive":
        scores["harvest"] += 2.0

    # --- archive: stagnant / low motion, reduce surface ---
    if traj in ("no_meaningful_movement", "mixed"):
        scores["archive"] += 3.0
    if ls in ("maintain", "validate") and traj != "positive":
        scores["archive"] += 2.0
    if strategic_posture in ("consolidate", "retire"):
        scores["archive"] += 1.5
    if not pattern_hits and traj == "no_meaningful_movement" and int(inbox_ev.get("high_severity_active") or 0) == 0:
        scores["archive"] += 1.0

    return scores


def _select_posture(scores: dict[str, float]) -> str | None:
    m = max(scores.values())
    if m < _MIN_SCORE_TO_PROPOSE:
        return None
    best = [k for k, v in scores.items() if abs(v - m) < 1e-9]
    for k in _POSTURE_TIE_ORDER:
        if k in best:
            return k
    return None


def _confidence_for(scores: dict[str, float], chosen: str) -> str:
    vals = sorted(scores.values(), reverse=True)
    top = vals[0] if vals else 0.0
    second = vals[1] if len(vals) > 1 else 0.0
    margin = top - second
    if margin >= 4.0:
        return "high"
    if margin >= 2.0:
        return "medium"
    return "low"


def _rationale_and_step(
    posture: str,
    *,
    lifecycle_stage: str,
    outcome: dict[str, Any] | None,
    strategic_posture: str | None,
    inbox_ev: dict[str, Any],
    pattern_hits: list[dict[str, Any]],
) -> tuple[str, str]:
    traj = str((outcome or {}).get("overall_trajectory") or "unknown")
    parts = [
        f"Lifecycle `{lifecycle_stage}`; portfolio outcome trajectory `{traj}`.",
    ]
    if strategic_posture:
        parts.append(f"Portfolio strategic posture is `{strategic_posture}` (context only).")
    if inbox_ev.get("active_items"):
        parts.append(
            f"Intervention inbox: {len(inbox_ev['active_items'])} active item(s), "
            f"{inbox_ev.get('high_severity_active', 0)} high severity."
        )
    if pattern_hits:
        pids = [str(p.get("pattern_id")) for p in pattern_hits[:3]]
        parts.append(f"Cross-product patterns touching this product: {', '.join(pids)}.")
    rationale = " ".join(parts)

    steps = {
        "retire": (
            "Run explicit sunset planning: archive repo signals, document handoff, "
            "and remove from active portfolio loops when approved."
        ),
        "harvest": (
            "Capture remaining value (metrics, learnings, revenue) on a short horizon; "
            "then schedule orderly retirement if scope should shrink."
        ),
        "archive": (
            "Freeze feature work; keep minimal ops/read-only monitoring until ownership decides retire vs revive."
        ),
        "repair_instead": (
            "Defer deprecation: clear intervention items, unblock orchestration, and re-evaluate outcomes next cycle."
        ),
    }
    return rationale, steps.get(posture, "Review with operator.")


def evaluate_deprecation_proposals(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_salt = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    inv = build_inventory(root, products_dir=products_dir)
    pids = sorted(inv.valid.keys())

    outcomes_raw = _load_portfolio_outcomes(root)
    per_by_pid: dict[str, dict[str, Any]] = {}
    for row in (outcomes_raw or {}).get("per_product_outcomes") or []:
        if isinstance(row, dict) and row.get("product_id"):
            per_by_pid[str(row["product_id"])] = row

    patterns = _load_portfolio_patterns(root)
    queue_raw = _load_operator_queue(root)
    queue_by_pid: dict[str, dict[str, Any]] = {}
    for e in (queue_raw or {}).get("entries") or []:
        if isinstance(e, dict) and e.get("product_id"):
            queue_by_pid[str(e["product_id"])] = e

    inbox = build_intervention_inbox_payload(root)
    strat_posture, strat_payload = load_latest_strategic_posture(root)

    proposals: list[dict[str, Any]] = []
    for pid in pids:
        rec = inv.valid[pid]
        stage = rec.node.lifecycle.stage.value
        outcome = per_by_pid.get(pid)
        qe = queue_by_pid.get(pid)
        ib = _inbox_evidence_for_product(inbox, pid)
        ph = _pattern_hits_for_product(patterns, pid)

        scores = _score_postures(
            lifecycle_stage=str(stage),
            outcome=outcome,
            queue_entry=qe,
            inbox_ev=ib,
            pattern_hits=ph,
            strategic_posture=strat_posture,
        )
        chosen = _select_posture(scores)
        if chosen is None:
            continue

        rationale, next_step = _rationale_and_step(
            chosen,
            lifecycle_stage=str(stage),
            outcome=outcome,
            strategic_posture=strat_posture,
            inbox_ev=ib,
            pattern_hits=ph,
        )
        conf = _confidence_for(scores, chosen)
        prop_id = _proposal_id(pid, chosen, f"{run_salt}|{strat_posture or 'none'}")

        supporting = {
            "portfolio_outcomes": {
                "loaded": outcomes_raw is not None,
                "overall_trajectory": (outcome or {}).get("overall_trajectory"),
                "reason_codes": (outcome or {}).get("reason_codes") or [],
                "mission_alignment": ((outcome or {}).get("mission_interpretation") or {}).get(
                    "mission_alignment"
                ),
            },
            "portfolio_strategy": {
                "loaded": strat_payload is not None,
                "strategic_posture": strat_posture,
            },
            "intervention_inbox": {
                "active_item_count": len(ib.get("active_items") or []),
                "high_severity_active": ib.get("high_severity_active"),
                "recurring_count": ib.get("recurring_count"),
            },
            "operator_queue": {
                "loaded": queue_raw is not None,
                "readiness_tier": (qe or {}).get("readiness_tier"),
                "priority_score": (qe or {}).get("priority_score"),
            },
            "patterns": {"matching_patterns": ph},
            "score_breakdown": scores,
        }

        proposals.append(
            {
                "proposal_id": prop_id,
                "product_id": pid,
                "deprecation_posture": chosen,
                "rationale": rationale,
                "supporting_evidence": supporting,
                "confidence": conf,
                "recommended_next_step": next_step,
            }
        )

    proposals.sort(key=lambda p: (str(p.get("deprecation_posture")), str(p.get("product_id"))))

    return {
        "schema": PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "proposal_count": len(proposals),
        "proposals": proposals,
        "inputs": {
            "outcomes_loaded": outcomes_raw is not None,
            "patterns_loaded": patterns is not None,
            "operator_queue_loaded": queue_raw is not None,
            "intervention_inbox_built": str(inbox.get("schema") or "") == INTERVENTION_INBOX_SCHEMA,
            "strategic_posture": strat_posture,
        },
    }


def render_deprecation_proposals_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Product deprecation proposals",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**Proposal count:** {payload.get('proposal_count', 0)}",
        "",
        "Advisory only — does not change products or orchestration.",
        "",
    ]
    inp = payload.get("inputs") or {}
    lines.append(
        f"- **Outcomes artifact:** {'loaded' if inp.get('outcomes_loaded') else 'missing'} · "
        f"**Strategy posture:** `{inp.get('strategic_posture') or '—'}`"
    )
    lines.extend(["", "## Proposals", ""])
    for p in payload.get("proposals") or []:
        lines.append(f"### `{p.get('proposal_id')}` — `{p.get('product_id')}`")
        lines.append("")
        lines.append(f"- **Posture:** `{p.get('deprecation_posture')}` · **Confidence:** {p.get('confidence')}")
        lines.append(f"- **Rationale:** {p.get('rationale')}")
        lines.append(f"- **Next step:** {p.get('recommended_next_step')}")
        lines.append("")
    if not (payload.get("proposals") or []):
        lines.append("No deprecation proposals — insufficient combined signals (or portfolio empty).")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_deprecation_proposals_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = deprecation_proposals_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    pl = dict(payload)
    pl["run_id"] = rid
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_deprecation_proposals_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_deprecation_proposals(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    payload = evaluate_deprecation_proposals(repo_root, products_dir=products_dir)
    if write_artifacts:
        write_deprecation_proposals_artifacts(repo_root, payload)
    return payload


__all__ = [
    "PRODUCT_DEPRECATION_PROPOSALS_SCHEMA",
    "deprecation_proposals_dir",
    "evaluate_deprecation_proposals",
    "render_deprecation_proposals_markdown",
    "run_deprecation_proposals",
    "write_deprecation_proposals_artifacts",
]
