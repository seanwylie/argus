"""
Bridge deprecation proposals to structured retirement plans (documentation and process only).

Does not delete files, mutate ``product.yaml``, or run irreversible actions — emits a plan artifact only.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.products.deprecation import (
    DEPRECATION_POSTURES,
    PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
    deprecation_proposals_dir,
)
from argus.products.inventory import build_inventory

PRODUCT_DEPRECATION_PLAN_SCHEMA = "argus.product_deprecation_plan.v1"


def deprecation_plan_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "deprecation_plan"


def _load_json(path: Path) -> dict[str, Any] | None:
    import json

    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def find_deprecation_proposal(repo_root: Path, proposal_id: str) -> dict[str, Any] | None:
    """
    Locate a proposal dict by ``proposal_id`` in ``runs/products/deprecation/latest.json``
    or any stamped ``*.json`` in the same directory (newest-first after ``latest``).
    """
    root = repo_root.resolve()
    pid = str(proposal_id).strip()
    if not pid:
        return None
    d = deprecation_proposals_dir(root)
    candidates: list[Path] = []
    latest = d / "latest.json"
    if latest.is_file():
        candidates.append(latest)
    for p in sorted(d.glob("*.json"), reverse=True):
        if p.name == "latest.json":
            continue
        candidates.append(p)
    seen: set[str] = set()
    for path in candidates:
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        raw = _load_json(path)
        if not raw or str(raw.get("schema") or "") != PRODUCT_DEPRECATION_PROPOSALS_SCHEMA:
            continue
        for prop in raw.get("proposals") or []:
            if isinstance(prop, dict) and str(prop.get("proposal_id") or "") == pid:
                return prop
    return None


def _evidence_summary(proposal: dict[str, Any]) -> dict[str, Any]:
    se = proposal.get("supporting_evidence") or {}
    if not isinstance(se, dict):
        se = {}
    po = se.get("portfolio_outcomes") or {}
    ps = se.get("portfolio_strategy") or {}
    ib = se.get("intervention_inbox") or {}
    oq = se.get("operator_queue") or {}
    pat = se.get("patterns") or {}
    return {
        "proposal_confidence": proposal.get("confidence"),
        "overall_trajectory": po.get("overall_trajectory"),
        "mission_alignment": po.get("mission_alignment"),
        "reason_codes": po.get("reason_codes") or [],
        "strategic_posture_context": ps.get("strategic_posture"),
        "intervention_active_items": ib.get("active_item_count"),
        "intervention_high_severity": ib.get("high_severity_active"),
        "queue_readiness_tier": oq.get("readiness_tier"),
        "matching_pattern_count": len((pat.get("matching_patterns") or []) if isinstance(pat, dict) else []),
        "score_breakdown": se.get("score_breakdown") if isinstance(se.get("score_breakdown"), dict) else {},
    }


def _plan_steps(posture: str) -> list[dict[str, Any]]:
    """Deterministic ordered steps — all ``safe`` categories are non-destructive."""
    p = str(posture).strip().lower()
    if p == "retire":
        return [
            {
                "step_id": "dep_plan.retire.01",
                "phase": "inventory",
                "title": "Record baseline state",
                "description": (
                    "Document current lifecycle stage, mission block, and last known signals/findings paths "
                    "from product.yaml and runs/ (read-only inventory)."
                ),
                "safe_action_category": "documentation",
            },
            {
                "step_id": "dep_plan.retire.02",
                "phase": "preservation",
                "title": "Define preservation scope",
                "description": (
                    "List artifacts to retain (metrics history, doctrine, decision logs) and where copies "
                    "should live outside the active repo (planning only — no deletion)."
                ),
                "safe_action_category": "documentation",
            },
            {
                "step_id": "dep_plan.retire.03",
                "phase": "portfolio",
                "title": "Plan portfolio decoupling",
                "description": (
                    "Schedule removal from active operator queue prioritization and portfolio refresh scope; "
                    "confirm no new experiments depend on this product (process decision)."
                ),
                "safe_action_category": "process_only",
            },
            {
                "step_id": "dep_plan.retire.04",
                "phase": "closure",
                "title": "Sunset checklist (manual)",
                "description": (
                    "After operator approval: archive or tombstone per org policy; this plan does not perform "
                    "file deletion or lifecycle transitions."
                ),
                "safe_action_category": "process_only",
            },
        ]
    if p == "harvest":
        return [
            {
                "step_id": "dep_plan.harvest.01",
                "phase": "extraction",
                "title": "Define harvest window",
                "description": (
                    "Set a time-bounded window to capture remaining value (revenue, learnings, metrics) "
                    "with explicit success criteria (planning text only)."
                ),
                "safe_action_category": "documentation",
            },
            {
                "step_id": "dep_plan.harvest.02",
                "phase": "preservation",
                "title": "Export learnings and doctrine",
                "description": (
                    "Plan what to codify into runbooks or shared doctrine before scope reduction "
                    "(no repo writes in this step)."
                ),
                "safe_action_category": "documentation",
            },
            {
                "step_id": "dep_plan.harvest.03",
                "phase": "transition",
                "title": "Hand off to retire plan",
                "description": (
                    "When harvest goals are met, re-run deprecation proposals and adopt a retire plan — "
                    "do not conflate harvest completion with automatic deletion."
                ),
                "safe_action_category": "process_only",
            },
        ]
    if p == "archive":
        return [
            {
                "step_id": "dep_plan.archive.01",
                "phase": "freeze",
                "title": "Freeze scope",
                "description": (
                    "Document that new feature work and new experiments are out of scope until "
                    "ownership revokes archive posture."
                ),
                "safe_action_category": "documentation",
            },
            {
                "step_id": "dep_plan.archive.02",
                "phase": "ops",
                "title": "Minimal ops model",
                "description": (
                    "Define read-only or break-glass monitoring only; no automated teardown in Argus."
                ),
                "safe_action_category": "process_only",
            },
            {
                "step_id": "dep_plan.archive.03",
                "phase": "preservation",
                "title": "Retention and access",
                "description": (
                    "Specify who may access the product tree and for how long (governance note — no mutation)."
                ),
                "safe_action_category": "documentation",
            },
        ]
    if p == "repair_instead":
        return [
            {
                "step_id": "dep_plan.repair.01",
                "phase": "triage",
                "title": "Intervention and inbox triage",
                "description": (
                    "Resolve or explicitly snooze open intervention inbox items for this product; "
                    "document blockers from supporting evidence (no orchestration execution here)."
                ),
                "safe_action_category": "process_only",
            },
            {
                "step_id": "dep_plan.repair.02",
                "phase": "stabilize",
                "title": "Stabilize orchestration",
                "description": (
                    "Clear blocked-waiting states where possible; re-run portfolio outcomes after cycles "
                    "to validate trajectory before any wind-down discussion."
                ),
                "safe_action_category": "process_only",
            },
            {
                "step_id": "dep_plan.repair.03",
                "phase": "re_evaluate",
                "title": "Re-evaluate deprecation",
                "description": (
                    "Re-run `argus products propose-deprecation` after repair work; this product is **not** "
                    "scheduled for retirement in this plan — repair-first."
                ),
                "safe_action_category": "documentation",
            },
        ]
    return [
        {
            "step_id": "dep_plan.generic.01",
            "phase": "review",
            "title": "Operator review",
            "description": "Unknown posture — manual review required.",
            "safe_action_category": "process_only",
        }
    ]


def _preservation_needs(posture: str) -> list[str]:
    p = str(posture).strip().lower()
    base = [
        "Snapshot or reference paths under runs/ for signals, findings, and decisions (copy plan, not deletion).",
        "Preserve product.yaml and notes/ for audit trail.",
    ]
    if p == "retire":
        return base + [
            "Export or reference key metrics series and cost notes for historical record.",
        ]
    if p == "harvest":
        return base + [
            "Capture monetization or outcome metrics for the harvest window explicitly.",
        ]
    if p == "archive":
        return base + [
            "Retain read-only access instructions for dependencies that still consume this product.",
        ]
    if p == "repair_instead":
        return [
            "Preserve current evidence bundle for comparison after repair cycles (no archival of product tree required for repair-first).",
        ]
    return base


def _archive_tombstone(posture: str) -> str:
    p = str(posture).strip().lower()
    if p == "retire":
        return (
            "Recommend **archival** of the product directory per org policy after approval, with a clear "
            "**tombstone** README or manifest pointer indicating retirement date and successor (if any). "
            "Argus does not delete or move files from this plan."
        )
    if p == "harvest":
        return (
            "During harvest, keep the tree **active**; after harvest, prefer **archive** with documented "
            "extraction outcomes rather than silent deletion."
        )
    if p == "archive":
        return (
            "Recommend labeling the product as **archived** in operator communications; optional **read-only** "
            "tombstone file in-repo — no automated tombstone write here."
        )
    if p == "repair_instead":
        return (
            "**No archive or tombstone** recommended until repair cycles complete and deprecation is re-evaluated."
        )
    return "Review archive/tombstone policy manually."


def _rollback_note(posture: str) -> str:
    p = str(posture).strip().lower()
    if p == "repair_instead":
        return (
            "Repair-first plans are reversible by design: improving outcomes and clearing interventions "
            "restores normal portfolio treatment without restoring deleted assets (none deleted by this plan)."
        )
    return (
        "If the plan is cancelled early, restore prior operator prioritization and portfolio strategy context; "
        "no Argus state is mutated by generating this plan. Any archival actions performed outside Argus "
        "may require VCS or backup restore per org policy."
    )


def _operator_review_requirements(posture: str) -> list[str]:
    p = str(posture).strip().lower()
    common = [
        "Explicit human approval before any file deletion, lifecycle stage change, or repo archival.",
        "Confirm downstream consumers and mission owners are notified.",
    ]
    if p == "repair_instead":
        return [
            "Verify intervention inbox items and orchestration blockers with owners before retirement talk.",
            "Re-run deprecation proposals after agreed repair work.",
        ]
    return common + [
        "Align with portfolio strategy and mission registry before executing sunset.",
    ]


def evaluate_deprecation_plan(
    repo_root: Path,
    proposal_id: str,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Build a single-product deprecation plan from a stored proposal (read-only; no mutations).
    """
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    prop = find_deprecation_proposal(root, proposal_id)
    if prop is None:
        return {
            "schema": PRODUCT_DEPRECATION_PLAN_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": (
                f"proposal_id {proposal_id!r} not found under {deprecation_proposals_dir(root)} "
                f"(latest.json or stamped *.json with schema {PRODUCT_DEPRECATION_PROPOSALS_SCHEMA!r})"
            ),
        }

    posture = str(prop.get("deprecation_posture") or "").strip().lower()
    if posture not in DEPRECATION_POSTURES:
        return {
            "schema": PRODUCT_DEPRECATION_PLAN_SCHEMA,
            "run_id": run_id,
            "evaluated_at_utc": evaluated_at,
            "ok": False,
            "error": f"invalid deprecation_posture on proposal: {prop.get('deprecation_posture')!r}",
        }

    product_id = str(prop.get("product_id") or "").strip()
    inv = build_inventory(root, products_dir=products_dir)
    in_inventory = product_id in inv.valid

    payload: dict[str, Any] = {
        "schema": PRODUCT_DEPRECATION_PLAN_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "ok": True,
        "proposal_id": str(prop.get("proposal_id") or proposal_id),
        "product_id": product_id,
        "deprecation_posture": posture,
        "product_still_in_inventory": in_inventory,
        "rationale": str(prop.get("rationale") or ""),
        "evidence_summary": _evidence_summary(prop),
        "plan_steps": _plan_steps(posture),
        "preservation_needs": _preservation_needs(posture),
        "archive_or_tombstone_recommendation": _archive_tombstone(posture),
        "rollback_or_restore_note": _rollback_note(posture),
        "operator_review_requirements": _operator_review_requirements(posture),
        "safety": {
            "no_deletion": True,
            "no_product_mutation": True,
            "no_irreversible_actions": True,
            "plan_only": True,
        },
    }
    return payload


def render_deprecation_plan_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return (
            f"# Product deprecation plan\n\n"
            f"**Schema:** `{payload.get('schema')}`\n\n"
            f"**Error:** {payload.get('error')}\n"
        )
    lines = [
        "# Product deprecation plan",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        f"- **Proposal:** `{payload.get('proposal_id')}`",
        f"- **Product:** `{payload.get('product_id')}`",
        f"- **Posture:** `{payload.get('deprecation_posture')}`",
        f"- **In inventory:** {payload.get('product_still_in_inventory')}",
        "",
        "## Safety",
        "",
        "- This artifact is **plan-only** — no deletion, no `product.yaml` mutation, no irreversible actions.",
        "",
        "## Rationale (from proposal)",
        "",
        str(payload.get("rationale") or "—"),
        "",
        "## Plan steps",
        "",
    ]
    for s in payload.get("plan_steps") or []:
        if not isinstance(s, dict):
            continue
        lines.append(f"### {s.get('step_id')} — {s.get('title')}")
        lines.append("")
        lines.append(f"*Phase:* `{s.get('phase')}` · *Category:* `{s.get('safe_action_category')}`")
        lines.append("")
        lines.append(str(s.get("description") or ""))
        lines.append("")
    lines.extend(
        [
            "## Preservation needs",
            "",
        ]
    )
    for x in payload.get("preservation_needs") or []:
        lines.append(f"- {x}")
    lines.extend(
        [
            "",
            "## Archive / tombstone recommendation",
            "",
            str(payload.get("archive_or_tombstone_recommendation") or "—"),
            "",
            "## Rollback / restore note",
            "",
            str(payload.get("rollback_or_restore_note") or "—"),
            "",
            "## Operator review requirements",
            "",
        ]
    )
    for x in payload.get("operator_review_requirements") or []:
        lines.append(f"- {x}")
    lines.extend(["", "## Evidence summary", "", "```json"])
    lines.append(dumps_json(payload.get("evidence_summary") or {}))
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def write_deprecation_plan_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or str(payload.get("run_id") or "")
    if not rid:
        rid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["run_id"] = rid
    d = deprecation_plan_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_deprecation_plan_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_deprecation_plan(
    repo_root: Path,
    *,
    proposal_id: str,
    write_artifacts: bool = True,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    payload = evaluate_deprecation_plan(
        repo_root,
        proposal_id,
        products_dir=products_dir,
    )
    if write_artifacts and payload.get("schema") == PRODUCT_DEPRECATION_PLAN_SCHEMA and payload.get("ok"):
        write_deprecation_plan_artifacts(repo_root, payload)
    return payload


__all__ = [
    "PRODUCT_DEPRECATION_PLAN_SCHEMA",
    "deprecation_plan_dir",
    "evaluate_deprecation_plan",
    "find_deprecation_proposal",
    "render_deprecation_plan_markdown",
    "run_deprecation_plan",
    "write_deprecation_plan_artifacts",
]
