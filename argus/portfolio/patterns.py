"""
Cross-product pattern detection — shared structural issues and opportunities from outcomes,
intervention history, product shape, and import posture.

Deterministic; reads existing artifacts and product.yaml only (no subprocess / network).
"""

from __future__ import annotations

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.artifact_index import (
    list_timestamped_portfolio_json_files,
    stamp_run_id_from_path,
)
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.outcomes import evaluate_portfolio_outcomes
from argus.products.inventory import ProductInventory, build_inventory

PORTFOLIO_PATTERNS_SCHEMA = "argus.portfolio_patterns.v1"

SYSTEMIC_MIN_PRODUCTS = 2
DEFAULT_LIMIT_HISTORY = 50
INTERVENTION_HISTORY_FILES = 12


def portfolio_patterns_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "patterns"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _product_shape_label(raw_extensions: Any) -> str:
    if not isinstance(raw_extensions, dict):
        return "unknown"
    ob = raw_extensions.get("argus_onboarding")
    if isinstance(ob, dict):
        ps = ob.get("product_shape")
        if isinstance(ps, dict):
            lab = ps.get("label")
            if isinstance(lab, str) and lab.strip():
                return lab.strip()
    return "unknown"


def _first_pass_status(raw_extensions: Any) -> str | None:
    if not isinstance(raw_extensions, dict):
        return None
    imp = raw_extensions.get("import_state")
    if not isinstance(imp, dict):
        return None
    fps = imp.get("first_pass_status")
    if fps is None:
        return None
    return str(fps).strip().lower()


def _product_meta_map(inv: ProductInventory) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for pid, rec in inv.valid.items():
        raw = rec.node.raw_extensions
        out[pid] = {
            "product_shape": _product_shape_label(raw),
            "first_pass_status": _first_pass_status(raw),
        }
    return out


def _load_intervention_history(repo_root: Path, *, limit: int) -> list[tuple[str, dict[str, Any]]]:
    d = portfolio_intervention_dir(repo_root)
    files = list_timestamped_portfolio_json_files(d)[: max(0, limit)]
    out: list[tuple[str, dict[str, Any]]] = []
    for p in files:
        rid = stamp_run_id_from_path(p)
        raw = _load_json(p)
        if raw is not None and str(raw.get("schema") or "") == PORTFOLIO_INTERVENTION_SCHEMA:
            out.append((rid, raw))
    return out


def _category_counts_across_intervention_history(
    history: list[tuple[str, dict[str, Any]]],
) -> dict[str, set[str]]:
    """Per category, which products appeared flagged across files."""
    cat_products: dict[str, set[str]] = defaultdict(set)
    for _rid, payload in history:
        for row in payload.get("flagged_products") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "").strip()
            cat = str(row.get("intervention_category") or "").strip()
            if pid and cat:
                cat_products[cat].add(pid)
    return dict(cat_products)


def detect_cross_product_patterns(
    *,
    per_product_outcomes: list[dict[str, Any]],
    product_meta: dict[str, dict[str, Any]],
    intervention_latest: dict[str, Any] | None,
    intervention_history: list[tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    """
    Pure detection from structured inputs (testable without full repo).
    """
    by_id: dict[str, dict[str, Any]] = {str(p.get("product_id")): p for p in per_product_outcomes if isinstance(p, dict)}
    patterns: list[dict[str, Any]] = []

    def add(
        pattern_id: str,
        *,
        title: str,
        severity: str,
        recurrence: Any,
        affected: list[str],
        action: str,
        evidence: dict[str, Any],
    ) -> None:
        patterns.append(
            {
                "pattern_id": pattern_id,
                "title": title,
                "severity": severity,
                "recurrence": recurrence,
                "affected_products": sorted(set(affected)),
                "recommended_systemic_action": action,
                "evidence": evidence,
            }
        )

    # --- Import posture (product.yaml) ---
    bad_fps = ("partial", "failed")
    import_stuck = [pid for pid, m in product_meta.items() if m.get("first_pass_status") in bad_fps]
    if len(import_stuck) >= SYSTEMIC_MIN_PRODUCTS:
        add(
            "patterns.import.multiple_products_partial_or_failed_first_pass",
            title="Multiple products stuck in partial or failed first-pass import",
            severity="high",
            recurrence={"kind": "import_state_snapshot"},
            affected=import_stuck,
            action=(
                "Review importer sync/excludes and first-pass tooling across these products; "
                "consider a shared importer regression run or template fix rather than per-product one-offs."
            ),
            evidence={"first_pass_statuses": {p: product_meta[p].get("first_pass_status") for p in import_stuck}},
        )

    stayed_bad = [
        pid
        for pid, row in by_id.items()
        if row.get("import_health_trajectory") in ("stayed_bad", "regressed")
    ]
    if len(stayed_bad) >= SYSTEMIC_MIN_PRODUCTS:
        add(
            "patterns.outcomes.import_health_stagnation_across_products",
            title="Import health not recovering across multiple products (outcomes)",
            severity="high",
            recurrence={"kind": "outcomes_trajectory"},
            affected=stayed_bad,
            action=(
                "Prioritize importer/gating fixes and shared first-pass evaluation; align product.yaml import_state "
                "with a common recovery checklist."
            ),
            evidence={"import_health_trajectory": {p: by_id[p].get("import_health_trajectory") for p in stayed_bad}},
        )

    # --- Low confidence cluster ---
    conf_worse = [pid for pid, row in by_id.items() if row.get("decision_confidence_trajectory") == "worsened"]
    if len(conf_worse) >= SYSTEMIC_MIN_PRODUCTS:
        add(
            "patterns.outcomes.decision_confidence_worsening_cluster",
            title="Decision confidence worsening across multiple products",
            severity="medium",
            recurrence={"kind": "outcomes_trajectory"},
            affected=conf_worse,
            action=(
                "Refresh signals/temporal inputs and decision-assessment cadence portfolio-wide; consider policy "
                "threshold review for confidence materiality if this persists."
            ),
            evidence={},
        )

    # --- Blocked progression ---
    blocked_persist = [pid for pid, row in by_id.items() if row.get("blocked_pattern") == "persisted"]
    if len(blocked_persist) >= SYSTEMIC_MIN_PRODUCTS:
        add(
            "patterns.outcomes.blocked_progression_persisted_multi_product",
            title="Blocked orchestration posture persisting across multiple products",
            severity="high",
            recurrence={"kind": "outcomes_trajectory"},
            affected=blocked_persist,
            action=(
                "Review shared approval/input gates and autonomy policy; unblock or adjust guardrails in a portfolio "
                "batch rather than per-product triage only."
            ),
            evidence={},
        )

    # --- Stagnation signature ---
    stagnation = [
        pid
        for pid, row in by_id.items()
        if (
            row.get("understanding_debt_trajectory") == "flat"
            and row.get("readiness_trajectory") == "unchanged"
            and row.get("overall_trajectory") == "no_meaningful_movement"
        )
    ]
    if len(stagnation) >= SYSTEMIC_MIN_PRODUCTS:
        add(
            "patterns.outcomes.parallel_stagnation_debt_and_readiness",
            title="Parallel stagnation: flat debt and unchanged readiness across multiple products",
            severity="medium",
            recurrence={"kind": "outcomes_trajectory"},
            affected=stagnation,
            action=(
                "Run a portfolio evidence refresh (signals, temporal, findings) with shared templates; "
                "check for systemic signal collection gaps."
            ),
            evidence={"count": len(stagnation)},
        )

    # --- Latest intervention: shared categories ---
    latest_shared_categories: set[str] = set()
    if intervention_latest:
        cat_to_pids: dict[str, list[str]] = defaultdict(list)
        for row in intervention_latest.get("flagged_products") or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("product_id") or "").strip()
            cat = str(row.get("intervention_category") or "").strip()
            if pid and cat:
                cat_to_pids[cat].append(pid)
        for cat, pids in cat_to_pids.items():
            if len(set(pids)) >= SYSTEMIC_MIN_PRODUCTS:
                latest_shared_categories.add(cat)
                add(
                    f"patterns.intervention.shared_category.{cat}",
                    title=f"Multiple products share intervention category `{cat}` (latest run)",
                    severity="medium",
                    recurrence={"kind": "intervention_latest", "category": cat},
                    affected=list(set(pids)),
                    action=_systemic_action_for_category(cat),
                    evidence={"intervention_category": cat, "source_run_id": intervention_latest.get("run_id")},
                )

    cat_prods = _category_counts_across_intervention_history(intervention_history)
    for cat, pids in cat_prods.items():
        if len(pids) >= SYSTEMIC_MIN_PRODUCTS and cat not in latest_shared_categories:
            runs_with = sum(
                1
                for _rid, pl in intervention_history
                if any(
                    str(r.get("product_id")) in pids
                    and str(r.get("intervention_category") or "") == cat
                    for r in (pl.get("flagged_products") or [])
                    if isinstance(r, dict)
                )
            )
            if runs_with >= 2:
                add(
                    f"patterns.intervention.recurring_category.{cat}",
                    title=f"Recurring intervention category `{cat}` across products over multiple runs",
                    severity="medium",
                    recurrence={"kind": "intervention_history", "runs_touched": runs_with},
                    affected=list(pids),
                    action=_systemic_action_for_category(cat),
                    evidence={"category": cat, "products_flagged_in_history": sorted(pids)},
                )

    # --- Shape × negative trajectory ---
    by_shape: dict[str, list[str]] = defaultdict(list)
    for pid, row in by_id.items():
        sh = product_meta.get(pid, {}).get("product_shape") or "unknown"
        if row.get("overall_trajectory") == "negative":
            by_shape[sh].append(pid)
    for sh, pids in by_shape.items():
        if sh != "unknown" and len(pids) >= SYSTEMIC_MIN_PRODUCTS:
            add(
                f"patterns.shape.correlated_negative_outcomes.{sh}",
                title=f"Multiple `{sh}` products show negative overall outcomes",
                severity="medium",
                recurrence={"kind": "shape_outcomes", "product_shape": sh},
                affected=pids,
                action=(
                    f"Treat `{sh}` as a hypothesis cluster: review shared build/test/import assumptions for this shape; "
                    "adjust importer heuristics or docs for that family."
                ),
                evidence={"product_shape": sh},
            )

    patterns.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}.get(x.get("severity"), 9), x.get("pattern_id")))
    return patterns


def _systemic_action_for_category(cat: str) -> str:
    if cat == "import_repair":
        return (
            "Batch importer and first-pass remediation using a shared playbook; validate product.yaml import_state "
            "patterns across the set."
        )
    if cat in ("human_review",):
        return (
            "Portfolio batch for approvals/refinement: align escalation templates and human review SLAs across products."
        )
    if cat in ("evidence_refresh",):
        return "Shared signals/temporal refresh campaign across products before more orchestration cycles."
    if cat in ("policy_tuning",):
        return "Review operator policy and next-action policy once for the portfolio; avoid per-product thrash."
    if cat in ("product_cleanup",):
        return "Portfolio hygiene: archive or merge low-value products sharing the same cleanup pattern."
    return "Address root cause portfolio-wide where possible before repeating per-product cycles."


def evaluate_portfolio_patterns(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    inv = build_inventory(root, products_dir=products_dir)
    meta = _product_meta_map(inv)
    outcomes = evaluate_portfolio_outcomes(root, limit_history=lim)
    per = [p for p in (outcomes.get("per_product_outcomes") or []) if isinstance(p, dict)]

    intervention_latest = _load_json(portfolio_intervention_dir(root) / "latest.json")
    if intervention_latest and str(intervention_latest.get("schema") or "") != PORTFOLIO_INTERVENTION_SCHEMA:
        intervention_latest = None

    iv_hist = _load_intervention_history(root, limit=INTERVENTION_HISTORY_FILES)

    detected = detect_cross_product_patterns(
        per_product_outcomes=per,
        product_meta=meta,
        intervention_latest=intervention_latest,
        intervention_history=iv_hist,
    )

    isolated_negative = [
        p.get("product_id")
        for p in per
        if isinstance(p, dict) and p.get("overall_trajectory") == "negative"
    ]
    systemic_affected = {x for pat in detected for x in (pat.get("affected_products") or [])}
    isolated_only = [pid for pid in isolated_negative if pid not in systemic_affected]

    return {
        "schema": PORTFOLIO_PATTERNS_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "inputs": {
            "limit_history": lim,
            "products_in_inventory": len(inv.valid),
            "intervention_history_files": len(iv_hist),
            "outcomes_run_id": outcomes.get("run_id"),
        },
        "detected_patterns": detected,
        "isolated_negative_products": sorted(isolated_only),
        "notes": [
            "Patterns require at least two products unless noted; single-product issues appear under isolated_negative_products.",
            "Product shape comes from raw_extensions.argus_onboarding.product_shape.label when present.",
        ],
    }


def render_portfolio_patterns_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio patterns (cross-product)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Systemic issues",
        "",
    ]
    dpat = payload.get("detected_patterns") or []
    high = [p for p in dpat if isinstance(p, dict) and p.get("severity") == "high"]
    if not high:
        lines.append("—")
    else:
        for p in high:
            lines.append(f"- **{p.get('title')}** — `{p.get('pattern_id')}`")
            lines.append(f"  - Products: {', '.join(f'`{x}`' for x in (p.get('affected_products') or []))}")
    lines.extend(["", "## Recurring failure modes", ""])
    med = [p for p in dpat if isinstance(p, dict) and p.get("severity") == "medium"]
    if not med:
        lines.append("—")
    else:
        for p in med:
            lines.append(f"- **{p.get('title')}** — `{p.get('pattern_id')}`")
    lines.extend(["", "## Shared improvement opportunities", ""])
    if not dpat:
        lines.append("—")
    else:
        for p in dpat:
            if isinstance(p, dict):
                lines.append(f"- `{p.get('pattern_id')}` → {p.get('recommended_systemic_action')}")
    lines.extend(["", "## Possible importer / policy gaps", ""])
    gap_ids = [
        p.get("pattern_id")
        for p in dpat
        if isinstance(p, dict)
        and (
            str(p.get("pattern_id") or "").startswith("patterns.import.")
            or "policy" in str(p.get("recommended_systemic_action") or "").lower()
            or "importer" in str(p.get("recommended_systemic_action") or "").lower()
        )
    ]
    if not gap_ids:
        lines.append("—")
    else:
        for g in gap_ids:
            lines.append(f"- `{g}`")
    iso = payload.get("isolated_negative_products") or []
    if iso:
        lines.extend(["", "## Isolated (not systemic) negative trajectories", ""])
        lines.append(", ".join(f"`{x}`" for x in iso))
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_patterns_artifacts(
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
    d = portfolio_patterns_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_patterns_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_patterns(
    repo_root: Path,
    *,
    limit_history: int = DEFAULT_LIMIT_HISTORY,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_patterns(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
    )
    if write_artifacts:
        write_portfolio_patterns_artifacts(repo_root, payload)
    return payload
