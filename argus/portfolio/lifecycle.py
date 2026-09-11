"""
Portfolio lifecycle synthesis — where products sit in creation / active / wind-down lanes.

Deterministic merge of inventory, creation proposals/scaffold/bootstrap, portfolio outcomes,
deprecation proposals/plans, and optional portfolio strategy posture.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.outcomes import PORTFOLIO_OUTCOMES_SCHEMA
from argus.portfolio.strategy_influence import load_latest_strategic_posture
from argus.products.creation import PRODUCT_CREATION_PROPOSALS_SCHEMA, creation_proposals_dir
from argus.products.creation_bootstrap import (
    PRODUCT_CREATION_BOOTSTRAP_SCHEMA,
    creation_bootstrap_dir,
)
from argus.products.creation_scaffold import (
    PRODUCT_CREATION_SCAFFOLD_SCHEMA,
    creation_scaffold_dir,
    evaluate_product_creation_scaffold,
)
from argus.products.deprecation import (
    PRODUCT_DEPRECATION_PROPOSALS_SCHEMA,
    deprecation_proposals_dir,
)
from argus.products.deprecation_plan import (
    PRODUCT_DEPRECATION_PLAN_SCHEMA,
    deprecation_plan_dir,
    evaluate_deprecation_plan,
)
from argus.products.instrumentation_feedback import (
    load_latest_signal_instrumentation_apply_by_product,
    refine_instrumentation_pressure_with_apply_context,
)
from argus.products.inventory import build_inventory
from argus.products.signal_instrumentation import (
    load_latest_signal_instrumentation_by_product,
    signal_instrumentation_latest_dir,
)

PORTFOLIO_LIFECYCLE_SCHEMA = "argus.portfolio_lifecycle.v1"
PROMOTION_OPPORTUNITIES_SCHEMA = "argus.promotion_opportunities.v1"

LIFECYCLE_STATUSES: tuple[str, ...] = (
    "proposed",
    "incubating",
    "active",
    "repairing",
    "harvesting",
    "retiring",
    "archived_candidate",
    "mixed_or_unclear",
)


def portfolio_lifecycle_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "lifecycle"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _all_schema_payloads(directory: Path, schema: str) -> list[dict[str, Any]]:
    if not directory.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(directory.glob("*.json")):
        raw = _load_json(p)
        if raw and str(raw.get("schema") or "") == schema:
            out.append(raw)
    return out


def _latest_scaffold_by_product(repo_root: Path) -> dict[str, dict[str, Any]]:
    """Most recent successful scaffold per product_id (by run_id string descending)."""
    d = creation_scaffold_dir(repo_root)
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw in _all_schema_payloads(d, PRODUCT_CREATION_SCAFFOLD_SCHEMA):
        if not raw.get("ok"):
            continue
        pid = str(raw.get("product_id") or "").strip()
        if not pid:
            continue
        rid = str(raw.get("run_id") or "")
        cur = best.get(pid)
        if cur is None or rid > cur[0]:
            best[pid] = (rid, raw)
    return {k: v[1] for k, v in best.items()}


def _scaffolded_proposal_ids(repo_root: Path) -> set[str]:
    out: set[str] = set()
    for raw in _all_schema_payloads(
        creation_scaffold_dir(repo_root), PRODUCT_CREATION_SCAFFOLD_SCHEMA
    ):
        if raw.get("ok") and raw.get("proposal_id"):
            out.add(str(raw["proposal_id"]))
    return out


def _bootstrap_ok_by_product(repo_root: Path) -> dict[str, bool]:
    """True if any stamped bootstrap run succeeded for product_id."""
    d = creation_bootstrap_dir(repo_root)
    ok_map: dict[str, bool] = {}
    for raw in _all_schema_payloads(d, PRODUCT_CREATION_BOOTSTRAP_SCHEMA):
        pid = str(raw.get("product_id") or "").strip()
        if not pid:
            continue
        if raw.get("ok") is True:
            ok_map[pid] = True
        elif pid not in ok_map:
            ok_map[pid] = False
    return ok_map


def _deprecation_posture_by_product(repo_root: Path) -> dict[str, str]:
    raw = _load_json(deprecation_proposals_dir(repo_root) / "latest.json")
    if not raw or str(raw.get("schema") or "") != PRODUCT_DEPRECATION_PROPOSALS_SCHEMA:
        return {}
    out: dict[str, str] = {}
    for p in raw.get("proposals") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("product_id") or "").strip()
        post = str(p.get("deprecation_posture") or "").strip().lower()
        if pid and post:
            out[pid] = post
    return out


def _deprecation_plan_latest_by_product(repo_root: Path) -> dict[str, dict[str, Any]]:
    d = deprecation_plan_dir(repo_root)
    best: dict[str, tuple[str, dict[str, Any]]] = {}
    for raw in _all_schema_payloads(d, PRODUCT_DEPRECATION_PLAN_SCHEMA):
        if not raw.get("ok"):
            continue
        pid = str(raw.get("product_id") or "").strip()
        if not pid:
            continue
        rid = str(raw.get("run_id") or "")
        cur = best.get(pid)
        if cur is None or rid > cur[0]:
            best[pid] = (rid, raw)
    return {k: v[1] for k, v in best.items()}


def _portfolio_outcomes_context(
    repo_root: Path,
) -> tuple[dict[str, dict[str, Any]], list[str], bool]:
    """Per-product outcome rows, negative-trajectory ids, whether schema matches."""
    raw = _load_json(Path(repo_root) / "runs" / "portfolio" / "outcomes" / "latest.json")
    if not raw or str(raw.get("schema") or "") != PORTFOLIO_OUTCOMES_SCHEMA:
        return {}, [], False
    out: dict[str, dict[str, Any]] = {}
    for row in raw.get("per_product_outcomes") or []:
        if isinstance(row, dict) and row.get("product_id"):
            out[str(row["product_id"])] = row
    neg = [str(x) for x in (raw.get("products_with_negative_trajectory") or [])]
    return out, neg, True


def _creation_proposals_latest(repo_root: Path) -> dict[str, Any] | None:
    raw = _load_json(creation_proposals_dir(repo_root) / "latest.json")
    if raw and str(raw.get("schema") or "") == PRODUCT_CREATION_PROPOSALS_SCHEMA:
        return raw
    return None


def _derive_inventory_status(
    *,
    stage: str,
    dep_posture: str | None,
    plan_posture: str | None,
    trajectory: str | None,
    scaffold_ok: bool,
    bootstrap_ok: bool,
) -> tuple[str, list[str]]:
    """Return (lifecycle_status, reason_codes)."""
    codes: list[str] = []
    posture = dep_posture or plan_posture
    if posture:
        codes.append(f"deprecation.posture.{posture}")
    st = stage.strip().lower()
    if posture == "repair_instead" and st == "kill":
        codes.append("lifecycle.conflict.kill_stage_vs_repair_instead")
        return "mixed_or_unclear", codes
    if posture == "repair_instead":
        return "repairing", codes
    if posture == "retire":
        return "retiring", codes
    if posture == "harvest":
        return "harvesting", codes
    if posture == "archive":
        return "archived_candidate", codes

    if st in ("idea", "build"):
        codes.append("inventory.stage.early")
        return "incubating", codes
    if scaffold_ok and not bootstrap_ok:
        codes.append("creation.scaffold_without_successful_bootstrap")
        return "incubating", codes

    if st == "kill":
        codes.append("inventory.stage.kill")
        return "retiring", codes

    traj = str(trajectory or "").strip().lower()
    if traj == "mixed":
        codes.append("outcomes.trajectory.mixed")
        return "mixed_or_unclear", codes

    if st == "decline":
        if traj == "positive":
            codes.append("inventory.decline_with_positive_trajectory")
            return "harvesting", codes
        codes.append("inventory.stage.decline")
        return "mixed_or_unclear", codes

    if st in ("validate", "grow", "maintain"):
        codes.append("inventory.stage.steady_state")
        return "active", codes

    codes.append("inventory.stage.unclassified")
    return "mixed_or_unclear", codes


def evaluate_portfolio_lifecycle(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    inv = build_inventory(root, products_dir=products_dir)
    pids = sorted(inv.valid.keys())

    strat_posture, strat_payload = load_latest_strategic_posture(root)
    outcomes_map, neg_traj, outcomes_ok = _portfolio_outcomes_context(root)
    dep_map = _deprecation_posture_by_product(root)
    plan_map = _deprecation_plan_latest_by_product(root)
    scaffold_by_pid = _latest_scaffold_by_product(root)
    boot_map = _bootstrap_ok_by_product(root)
    realized_proposals = _scaffolded_proposal_ids(root)
    creation = _creation_proposals_latest(root)

    inst_by_product = load_latest_signal_instrumentation_by_product(root)
    apply_by_product = load_latest_signal_instrumentation_apply_by_product(root)
    inst_ref = refine_instrumentation_pressure_with_apply_context(
        inst_by_product=inst_by_product,
        apply_by_product=apply_by_product,
    )
    instrumentation_pressure_ids = list(inst_ref["products_under_instrumentation_pressure_effective"])
    inst_latest_dir = signal_instrumentation_latest_dir(root)
    signal_instrumentation_dir_present = inst_latest_dir.is_dir()
    apply_latest_dir = root / "runs" / "products" / "signal_instrumentation_apply" / "latest"
    signal_instrumentation_apply_dir_present = apply_latest_dir.is_dir()

    per_product: list[dict[str, Any]] = []
    counts: dict[str, int] = {s: 0 for s in LIFECYCLE_STATUSES}

    for pid in pids:
        rec = inv.valid[pid]
        stage = str(rec.node.lifecycle.stage.value)
        sc = scaffold_by_pid.get(pid) or {}
        scaffold_ok = bool(sc.get("ok"))
        bootstrap_ok = bool(boot_map.get(pid))
        dep_posture = dep_map.get(pid)
        plan = plan_map.get(pid)
        plan_posture = str(plan.get("deprecation_posture") or "").strip().lower() if plan else None
        if not plan_posture:
            plan_posture = None
        oc = outcomes_map.get(pid)
        traj = str((oc or {}).get("overall_trajectory") or "") if oc else None

        status, codes = _derive_inventory_status(
            stage=stage,
            dep_posture=dep_posture,
            plan_posture=plan_posture,
            trajectory=traj,
            scaffold_ok=scaffold_ok,
            bootstrap_ok=bootstrap_ok,
        )
        codes_sorted = sorted(set(codes))
        counts[status] = counts.get(status, 0) + 1

        inst_ev: dict[str, Any]
        if pid in inst_by_product:
            ir = inst_by_product[pid]
            inst_ev = {
                "artifact_present": True,
                "instrumentation_status": ir.get("instrumentation_status"),
                "evaluated_at_utc": ir.get("evaluated_at_utc"),
                "ok": ir.get("ok"),
            }
        else:
            inst_ev = {"artifact_present": False}
        ap_row = apply_by_product.get(pid)
        if isinstance(ap_row, dict) and ap_row:
            inst_ev["latest_apply"] = {
                "apply_status": ap_row.get("apply_status"),
                "applied_at_utc": ap_row.get("applied_at_utc"),
                "post_apply": ap_row.get("post_apply"),
            }
        else:
            inst_ev["latest_apply"] = None

        per_product.append(
            {
                "product_id": pid,
                "creation_proposal_id": str(sc.get("proposal_id") or "") or None,
                "lifecycle_stage": stage,
                "lifecycle_status": status,
                "reason_codes": codes_sorted,
                "evidence": {
                    "deprecation_posture": dep_posture,
                    "deprecation_plan_posture": plan_posture,
                    "overall_trajectory": traj,
                    "creation_scaffold_ok": scaffold_ok,
                    "creation_bootstrap_ok": bootstrap_ok,
                    "signal_instrumentation": inst_ev,
                },
            }
        )

    # Concept-only creation proposals (not yet scaffolded)
    if creation:
        for prop in creation.get("proposals") or []:
            if not isinstance(prop, dict):
                continue
            pr_id = str(prop.get("proposal_id") or "").strip()
            if not pr_id or pr_id in realized_proposals:
                continue
            counts["proposed"] = counts.get("proposed", 0) + 1
            per_product.append(
                {
                    "product_id": None,
                    "creation_proposal_id": pr_id,
                    "concept_title": prop.get("concept_title"),
                    "lifecycle_stage": None,
                    "lifecycle_status": "proposed",
                    "reason_codes": ["creation.proposal_pending_scaffold"],
                    "evidence": {
                        "creation_mission_used": prop.get("creation_mission_used"),
                        "expected_role_in_portfolio": prop.get("expected_role_in_portfolio"),
                    },
                }
            )

    entering_ids: list[str] = []
    for x in per_product:
        if x.get("lifecycle_status") == "incubating" and x.get("product_id"):
            entering_ids.append(str(x["product_id"]))
        elif x.get("lifecycle_status") == "proposed" and x.get("creation_proposal_id"):
            entering_ids.append(f"proposal:{x['creation_proposal_id']}")
    entering = sorted(set(entering_ids))

    exiting = sorted(
        {
            str(x["product_id"])
            for x in per_product
            if x.get("product_id")
            and x.get("lifecycle_status") in ("retiring", "harvesting", "archived_candidate")
        }
    )

    repair_pressure = sorted(
        str(x["product_id"])
        for x in per_product
        if x.get("product_id") and x.get("lifecycle_status") == "repairing"
    )

    retirement_pressure = sorted(
        {
            str(x["product_id"])
            for x in per_product
            if x.get("product_id")
            and x.get("lifecycle_status") in ("retiring", "archived_candidate", "harvesting")
        }
    )

    summary_parts = [
        f"Evaluated {len(pids)} inventory product(s)",
        f"{counts.get('active', 0)} active",
        f"{counts.get('incubating', 0)} incubating",
        f"{counts.get('proposed', 0)} proposed concept(s)",
        f"{counts.get('repairing', 0)} under repair pressure",
        f"{len(exiting)} in exit-oriented lanes (harvest/retire/archive)",
    ]
    if strat_posture:
        summary_parts.append(f"portfolio strategy posture: {strat_posture}")
    if inst_ref["products_under_instrumentation_pressure_raw"]:
        summary_parts.append(
            f"{len(inst_ref['products_under_instrumentation_pressure_raw'])} weak/sparse/missing per latest instrumentation scan"
        )
    if instrumentation_pressure_ids:
        summary_parts.append(f"{len(instrumentation_pressure_ids)} still under effective instrumentation pressure")
    if inst_ref["products_instrumentation_resolved_via_apply"]:
        summary_parts.append(
            f"{len(inst_ref['products_instrumentation_resolved_via_apply'])} improved to adequate in latest worker apply validation"
        )
    if inst_ref["products_instrumentation_apply_followup"]:
        summary_parts.append(
            f"{len(inst_ref['products_instrumentation_apply_followup'])} with apply follow-up (partial or still weak post-apply)"
        )

    recommended: list[str] = []
    if counts.get("repairing", 0) > 0:
        recommended.append(
            "Clear intervention and orchestration blockers for products in the repairing lane before exit planning."
        )
    if counts.get("incubating", 0) + counts.get("proposed", 0) > 0:
        recommended.append(
            "Track creation scaffold/bootstrap completion for incubating products and review proposed concepts for mission fit."
        )
    if counts.get("retiring", 0) + counts.get("archived_candidate", 0) > 0:
        recommended.append(
            "Align deprecation plans with portfolio strategy before any irreversible archival outside Argus."
        )
    if counts.get("mixed_or_unclear", 0) > 0:
        recommended.append(
            "Resolve mixed trajectory or conflicting lifecycle signals with a focused portfolio pass (outcomes + intervention)."
        )
    if instrumentation_pressure_ids:
        recommended.append(
            f"{len(instrumentation_pressure_ids)} product(s) remain under effective signal instrumentation pressure "
            "(see `runs/products/signal_instrumentation/latest/` and apply outcomes under "
            "`runs/products/signal_instrumentation_apply/latest/`) — refresh instrumentation or enrich signals "
            "before treating inspect outcomes as optimization gaps."
        )
    if inst_ref["products_instrumentation_apply_followup"]:
        recommended.append(
            f"{len(inst_ref['products_instrumentation_apply_followup'])} product(s) have a worker instrumentation apply "
            "but still show weak coverage or partial apply — prefer richer real telemetry and `argus products instrument-signals` "
            "after contract changes land."
        )
    if inst_ref["products_instrumentation_resolved_via_apply"] and not instrumentation_pressure_ids:
        recommended.append(
            "Latest worker instrumentation applies recorded adequate post-apply validation for previously pressured ids; "
            "focus on learning from signals rather than expanding observability contracts."
        )

    if not recommended:
        recommended.append(
            "No urgent lifecycle attention flags from current artifacts; re-run after portfolio outcomes or deprecation proposals refresh."
        )

    narrative = " · ".join(summary_parts) + "."

    attention_structured: list[dict[str, Any]] = []
    for i, msg in enumerate(recommended):
        attention_structured.append({"attention_rank": i, "note": msg})

    if strat_posture == "retire":
        for pid in neg_traj:
            if pid in pids and pid not in retirement_pressure:
                retirement_pressure = sorted(set(retirement_pressure + [pid]))

    summary_dict: dict[str, Any] = {
        "narrative": narrative,
        "inventory_product_count": len(pids),
        "not_scaffolded_creation_proposals": counts.get("proposed", 0),
        "exit_oriented_product_count": len(exiting),
        "products_with_negative_trajectory": sorted(neg_traj),
        "products_needing_signal_instrumentation_count": len(instrumentation_pressure_ids),
        "products_instrumentation_raw_pressure_count": len(inst_ref["products_under_instrumentation_pressure_raw"]),
        "products_instrumentation_resolved_via_apply_count": len(inst_ref["products_instrumentation_resolved_via_apply"]),
        "products_instrumentation_apply_followup_count": len(inst_ref["products_instrumentation_apply_followup"]),
        "signal_instrumentation_artifact_files": len(inst_by_product),
        "signal_instrumentation_apply_artifact_files": len(apply_by_product),
        "instrumentation_pressure_note": (
            f"{len(instrumentation_pressure_ids)} product(s) under effective signal instrumentation pressure "
            f"({', '.join(instrumentation_pressure_ids[:12])}{', …' if len(instrumentation_pressure_ids) > 12 else ''})"
            if instrumentation_pressure_ids
            else None
        ),
    }

    return {
        "schema": PORTFOLIO_LIFECYCLE_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "portfolio_strategy_posture": strat_posture,
        "portfolio_strategy_loaded": strat_payload is not None,
        "creation_proposals_loaded": creation is not None,
        "deprecation_proposals_loaded": (
            str(
                (_load_json(deprecation_proposals_dir(root) / "latest.json") or {}).get("schema")
                or ""
            )
            == PRODUCT_DEPRECATION_PROPOSALS_SCHEMA
        ),
        "per_product_lifecycle": per_product,
        "lifecycle_counts": {k: counts.get(k, 0) for k in LIFECYCLE_STATUSES},
        "products_entering": entering,
        "products_exiting": exiting,
        "products_under_repair_pressure": repair_pressure,
        "products_under_retirement_pressure": retirement_pressure,
        "products_under_instrumentation_pressure": instrumentation_pressure_ids,
        "products_under_instrumentation_pressure_raw": inst_ref["products_under_instrumentation_pressure_raw"],
        "products_instrumentation_resolved_via_apply": inst_ref["products_instrumentation_resolved_via_apply"],
        "products_instrumentation_apply_followup": inst_ref["products_instrumentation_apply_followup"],
        "instrumentation_pressure_refinement": inst_ref,
        "signal_instrumentation_latest_dir_present": signal_instrumentation_dir_present,
        "signal_instrumentation_apply_latest_dir_present": signal_instrumentation_apply_dir_present,
        "portfolio_lifecycle_summary": summary_dict,
        "recommended_lifecycle_attention": attention_structured,
        "inputs": {
            "products_dir": str(products_dir) if products_dir is not None else None,
            "valid_product_count": len(pids),
            "outcomes_artifact_present": outcomes_ok,
            "signal_instrumentation_artifacts_loaded": len(inst_by_product),
            "signal_instrumentation_apply_artifacts_loaded": len(apply_by_product),
            "paths": {
                "creation_proposals": str(creation_proposals_dir(root)),
                "creation_scaffold": str(creation_scaffold_dir(root)),
                "creation_bootstrap": str(creation_bootstrap_dir(root)),
                "deprecation_proposals": str(deprecation_proposals_dir(root)),
                "deprecation_plan": str(deprecation_plan_dir(root)),
                "portfolio_outcomes": str(root / "runs" / "portfolio" / "outcomes" / "latest.json"),
                "portfolio_strategy": str(root / "runs" / "portfolio" / "strategy" / "latest.json"),
                "signal_instrumentation_latest": str(inst_latest_dir),
                "signal_instrumentation_apply_latest": str(apply_latest_dir),
            },
        },
    }


def render_portfolio_lifecycle_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio lifecycle synthesis",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**Run id:** `{payload.get('run_id')}`",
        "",
        "## Summary",
        "",
    ]
    summ = payload.get("portfolio_lifecycle_summary")
    if isinstance(summ, dict):
        lines.append(str(summ.get("narrative") or "—"))
    else:
        lines.append(str(summ or "—"))
    lines.extend(
        [
            "",
            "### Counts by lane",
            "",
        ]
    )
    for k, v in sorted((payload.get("lifecycle_counts") or {}).items(), key=lambda x: x[0]):
        lines.append(f"- **{k}:** {v}")
    lines.extend(
        [
            "",
            "### Entering / exit-oriented",
            "",
            f"- **Entering (incubating / proposed ids):** {', '.join(f'`{x}`' for x in (payload.get('products_entering') or [])[:24]) or '—'}",
            f"- **Exit-oriented (harvest / retire / archive):** {', '.join(f'`{x}`' for x in (payload.get('products_exiting') or [])[:24]) or '—'}",
            "",
            "### Pressure lists",
            "",
            f"- **Repair pressure:** {', '.join(f'`{x}`' for x in (payload.get('products_under_repair_pressure') or [])[:24]) or '—'}",
            f"- **Retirement pressure (incl. harvest):** {', '.join(f'`{x}`' for x in (payload.get('products_under_retirement_pressure') or [])[:24]) or '—'}",
            f"- **Signal instrumentation pressure (effective):** {', '.join(f'`{x}`' for x in (payload.get('products_under_instrumentation_pressure') or [])[:24]) or '—'}",
            f"- **Resolved via latest apply (adequate post-apply):** {', '.join(f'`{x}`' for x in (payload.get('products_instrumentation_resolved_via_apply') or [])[:24]) or '—'}",
            f"- **Apply follow-up (partial / still weak):** {', '.join(f'`{x}`' for x in (payload.get('products_instrumentation_apply_followup') or [])[:24]) or '—'}",
            "",
            "### Recommended attention",
            "",
        ]
    )
    for r in payload.get("recommended_lifecycle_attention") or []:
        if isinstance(r, dict):
            lines.append(f"- {r.get('note') or r}")
        else:
            lines.append(f"- {r}")
    lines.extend(["", "## Per product", ""])
    for row in sorted(
        payload.get("per_product_lifecycle") or [],
        key=lambda r: (
            r.get("product_id") is None,
            str(r.get("product_id") or ""),
            str(r.get("creation_proposal_id") or ""),
        ),
    ):
        pid = row.get("product_id")
        label = f"`{pid}`" if pid else f"proposal `{row.get('creation_proposal_id')}`"
        lines.append(
            f"- {label} — **{row.get('lifecycle_status')}** (stage: `{row.get('lifecycle_stage')}`)"
        )
    lines.append("")
    return "\n".join(lines)


def write_portfolio_lifecycle_artifacts(
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
    d = portfolio_lifecycle_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_lifecycle_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_lifecycle(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_lifecycle(repo_root, products_dir=products_dir)
    if write_artifacts:
        write_portfolio_lifecycle_artifacts(repo_root, payload)
    return payload


def collect_promotion_opportunities(
    repo_root: Path,
    *,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Deterministic scan for lifecycle promotions aligned with portfolio lifecycle synthesis:

    - Creation proposals not yet scaffolded (uses ``evaluate_product_creation_scaffold`` dry-run semantics).
    - Scaffolded products without a successful bootstrap run.
    - Deprecation proposals without a successful per-product deprecation plan artifact yet.

    Does not mutate disk beyond reading existing artifacts.
    """
    root = repo_root.resolve()
    inv = build_inventory(root, products_dir=products_dir)
    promotable: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    recommendations: list[str] = []

    realized = _scaffolded_proposal_ids(root)
    scaffold_by_pid = _latest_scaffold_by_product(root)
    boot_map = _bootstrap_ok_by_product(root)
    plan_by_pid = _deprecation_plan_latest_by_product(root)

    creation = _creation_proposals_latest(root)
    if creation:
        for prop in creation.get("proposals") or []:
            if not isinstance(prop, dict):
                continue
            pr_id = str(prop.get("proposal_id") or "").strip()
            if not pr_id or pr_id in realized:
                continue
            ev = evaluate_product_creation_scaffold(root, proposal_id=pr_id, dry_run=True)
            slug = str(ev.get("product_id") or "").strip()
            entry: dict[str, Any] = {
                "kind": "creation_proposal_to_scaffold",
                "proposal_id": pr_id,
                "derived_product_id": slug or None,
                "safe_for_auto": bool(ev.get("ok")),
                "preview": {"schema": ev.get("schema"), "ok": ev.get("ok"), "error": ev.get("error")},
            }
            if ev.get("ok"):
                promotable.append(entry)
                recommendations.append(
                    f"Scaffold creation proposal `{pr_id}` → product `{slug}` (no existing product directory)."
                )
            else:
                blocked.append(
                    {
                        "kind": "creation_proposal_to_scaffold",
                        "proposal_id": pr_id,
                        "derived_product_id": slug or None,
                        "reason": str(ev.get("error") or "creation scaffold preview failed"),
                    }
                )

    for pid, _sc in sorted(scaffold_by_pid.items()):
        if boot_map.get(pid) is True:
            continue
        entry = {
            "kind": "scaffolded_product_to_bootstrap",
            "product_id": pid,
            "safe_for_auto": pid in inv.valid,
            "preview": {
                "scaffold_ok": bool((_sc or {}).get("ok")),
                "bootstrap_ok": boot_map.get(pid),
            },
        }
        if pid in inv.valid:
            promotable.append(entry)
            recommendations.append(
                f"Run creation bootstrap for scaffolded product `{pid}` (bootstrap not yet successful)."
            )
        else:
            blocked.append(
                {
                    "kind": "scaffolded_product_to_bootstrap",
                    "product_id": pid,
                    "reason": "product not in valid inventory — fix manifest before bootstrap",
                }
            )

    dep_raw = _load_json(deprecation_proposals_dir(root) / "latest.json")
    dep_proposals: list[dict[str, Any]] = []
    if dep_raw and str(dep_raw.get("schema") or "") == PRODUCT_DEPRECATION_PROPOSALS_SCHEMA:
        for p in dep_raw.get("proposals") or []:
            if isinstance(p, dict) and str(p.get("proposal_id") or "").strip():
                dep_proposals.append(p)

    seen_product_no_plan: set[str] = set()
    for prop in sorted(dep_proposals, key=lambda x: str(x.get("proposal_id") or "")):
        pr_id = str(prop.get("proposal_id") or "").strip()
        prod_id = str(prop.get("product_id") or "").strip()
        if not pr_id or not prod_id:
            blocked.append(
                {
                    "kind": "deprecation_proposal_to_plan",
                    "proposal_id": pr_id,
                    "product_id": prod_id or None,
                    "reason": "missing proposal_id or product_id on deprecation proposal",
                }
            )
            continue
        existing = plan_by_pid.get(prod_id)
        if existing and existing.get("ok"):
            blocked.append(
                {
                    "kind": "deprecation_proposal_to_plan",
                    "proposal_id": pr_id,
                    "product_id": prod_id,
                    "reason": "deprecation plan artifact already exists for this product",
                }
            )
            continue
        if prod_id in seen_product_no_plan:
            blocked.append(
                {
                    "kind": "deprecation_proposal_to_plan",
                    "proposal_id": pr_id,
                    "product_id": prod_id,
                    "reason": "another deprecation proposal is already queued for promotion for this product in this scan",
                }
            )
            continue

        ev = evaluate_deprecation_plan(root, pr_id, products_dir=products_dir)
        entry = {
            "kind": "deprecation_proposal_to_plan",
            "proposal_id": pr_id,
            "product_id": prod_id,
            "safe_for_auto": bool(ev.get("ok")),
            "preview": {"schema": ev.get("schema"), "ok": ev.get("ok"), "error": ev.get("error")},
        }
        if ev.get("ok"):
            promotable.append(entry)
            seen_product_no_plan.add(prod_id)
            recommendations.append(
                f"Materialize deprecation plan for proposal `{pr_id}` (product `{prod_id}`) — plan-only artifact."
            )
        else:
            blocked.append(
                {
                    "kind": "deprecation_proposal_to_plan",
                    "proposal_id": pr_id,
                    "product_id": prod_id,
                    "reason": str(ev.get("error") or "deprecation plan preview failed"),
                }
            )

    return {
        "schema": PROMOTION_OPPORTUNITIES_SCHEMA,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "promotable_actions": promotable,
        "promotion_recommendations": recommendations,
        "blocked_promotions": blocked,
        "inputs": {"products_dir": str(products_dir) if products_dir is not None else None},
    }


__all__ = [
    "LIFECYCLE_STATUSES",
    "PORTFOLIO_LIFECYCLE_SCHEMA",
    "PROMOTION_OPPORTUNITIES_SCHEMA",
    "collect_promotion_opportunities",
    "evaluate_portfolio_lifecycle",
    "portfolio_lifecycle_dir",
    "render_portfolio_lifecycle_markdown",
    "run_portfolio_lifecycle",
    "write_portfolio_lifecycle_artifacts",
]
