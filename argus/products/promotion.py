"""
Traceable lifecycle promotion workflows — thin wrappers over creation scaffold, bootstrap, and deprecation plan.

All mutations go through existing modules; this layer adds a normalized ``argus.lifecycle_promotion_action.v1`` record.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.products.creation_bootstrap import run_product_creation_bootstrap
from argus.products.creation_scaffold import run_product_creation_scaffold
from argus.products.deprecation_plan import evaluate_deprecation_plan, run_deprecation_plan

LIFECYCLE_PROMOTION_ACTION_SCHEMA = "argus.lifecycle_promotion_action.v1"


def promotion_actions_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "promotion_actions"


def _now_ids() -> tuple[str, str]:
    ts = datetime.now(timezone.utc)
    return ts.isoformat(), ts.strftime("%Y%m%dT%H%M%SZ")


def _paths_from_scaffold(pl: dict[str, Any]) -> list[str]:
    if pl.get("dry_run"):
        return sorted((pl.get("planned_files") or {}).keys())
    return list(pl.get("paths_created") or [])


def _paths_from_bootstrap(pl: dict[str, Any]) -> list[str]:
    ac = pl.get("artifacts_created")
    if isinstance(ac, list) and ac:
        return sorted({str(x) for x in ac})
    if pl.get("ok"):
        return ["runs/products/creation_bootstrap/latest.json"]
    return []


def _paths_from_deprecation_plan(_repo: Path, pl: dict[str, Any]) -> list[str]:
    if pl.get("ok"):
        return ["runs/products/deprecation_plan/latest.json"]
    return []


def _wrap_payload(
    *,
    promotion_id: str,
    started_at_utc: str,
    finished_at_utc: str,
    source_type: str,
    source_id: str,
    target_action: str,
    actor: str,
    dry_run: bool,
    result_status: str,
    created_artifacts: list[str],
    rationale: str,
    notes: list[str],
    nested_results: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": LIFECYCLE_PROMOTION_ACTION_SCHEMA,
        "promotion_id": promotion_id,
        "started_at_utc": started_at_utc,
        "finished_at_utc": finished_at_utc,
        "source_type": source_type,
        "source_id": source_id,
        "target_action": target_action,
        "actor": actor,
        "dry_run": dry_run,
        "result_status": result_status,
        "created_artifacts": created_artifacts,
        "rationale": rationale,
        "notes": notes,
        "nested_results": nested_results,
    }


def render_lifecycle_promotion_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Lifecycle promotion action",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Promotion id:** `{payload.get('promotion_id')}`",
        f"**Started (UTC):** {payload.get('started_at_utc')}",
        f"**Finished (UTC):** {payload.get('finished_at_utc')}",
        "",
        "## Summary",
        "",
        f"- **Source:** `{payload.get('source_type')}` `{payload.get('source_id')}`",
        f"- **Target action:** `{payload.get('target_action')}`",
        f"- **Actor:** `{payload.get('actor')}`",
        f"- **Dry run:** {payload.get('dry_run')}",
        f"- **Result:** `{payload.get('result_status')}`",
        "",
        str(payload.get("rationale") or ""),
        "",
        "## Artifacts",
        "",
    ]
    for p in payload.get("created_artifacts") or []:
        lines.append(f"- `{p}`")
    if not payload.get("created_artifacts"):
        lines.append("- _(none)_")
    lines.extend(["", "## Notes", ""])
    for n in payload.get("notes") or []:
        lines.append(f"- {n}")
    lines.extend(["", "## Nested results (schemas)", ""])
    nr = payload.get("nested_results") or {}
    for key in sorted(nr.keys()):
        child = nr[key]
        if isinstance(child, dict):
            lines.append(f"- **{key}:** `{child.get('schema')}` · ok={child.get('ok')}")
        else:
            lines.append(f"- **{key}:** —")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_lifecycle_promotion_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    promotion_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    pid = promotion_id or str(payload.get("promotion_id") or "")
    if not pid:
        _, pid = _now_ids()
    pl = dict(payload)
    pl["promotion_id"] = pid
    d = promotion_actions_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{pid}.json"
    stamped_md = d / f"{pid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_lifecycle_promotion_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def _emit_promotion(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    write: bool,
    promotion_id: str,
) -> dict[str, Any]:
    if write:
        write_lifecycle_promotion_artifacts(repo_root, payload, promotion_id=promotion_id)
    return payload


def run_promote_creation(
    repo_root: Path,
    *,
    proposal_id: str,
    product_id: str | None = None,
    bootstrap: bool = False,
    bootstrap_minimal: bool = False,
    dry_run: bool = False,
    write_promotion_artifact: bool = True,
    write_stage_artifacts: bool = True,
    actor: str = "operator",
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Promotion: creation proposal → scaffold (disk), optionally → creation bootstrap.

    Refuses existing product dirs via underlying scaffold evaluation (no overwrite).
    """
    root = repo_root.resolve()
    started_at, promotion_id = _now_ids()
    notes: list[str] = []
    created: list[str] = []
    nested: dict[str, Any] = {}

    rationale = (
        "Explicit promotion: apply stored creation proposal to a new product scaffold under products/, "
        "optionally run first Argus bootstrap loop."
    )

    sc = run_product_creation_scaffold(
        root,
        proposal_id=proposal_id,
        product_id=product_id,
        dry_run=dry_run,
        write_artifacts=write_stage_artifacts,
    )
    nested["creation_scaffold"] = sc
    created.extend(_paths_from_scaffold(sc))

    if not sc.get("ok"):
        finished_at, _ = _now_ids()
        out = _wrap_payload(
            promotion_id=promotion_id,
            started_at_utc=started_at,
            finished_at_utc=finished_at,
            source_type="creation_proposal",
            source_id=proposal_id,
            target_action="creation_proposal_to_scaffold_and_bootstrap"
            if bootstrap
            else "creation_proposal_to_scaffold",
            actor=actor,
            dry_run=dry_run,
            result_status="failed",
            created_artifacts=sorted(set(created)),
            rationale=rationale,
            notes=notes + [f"Scaffold failed: {sc.get('error')}"],
            nested_results=nested,
        )
        return _emit_promotion(root, out, write=write_promotion_artifact, promotion_id=promotion_id)

    boot_pl: dict[str, Any] | None = None
    if bootstrap and not dry_run:
        pid = str(sc.get("product_id") or "")
        boot_pl = run_product_creation_bootstrap(
            root,
            product_id=pid,
            minimal=bootstrap_minimal,
            dry_run=False,
            write_artifacts=write_stage_artifacts,
            products_dir=products_dir,
        )
        nested["creation_bootstrap"] = boot_pl
        if boot_pl.get("ok"):
            created.extend(_paths_from_bootstrap(boot_pl))
        if not boot_pl.get("ok"):
            notes.append(f"Bootstrap failed after scaffold: {boot_pl.get('error')}")
            finished_at, _ = _now_ids()
            out = _wrap_payload(
                promotion_id=promotion_id,
                started_at_utc=started_at,
                finished_at_utc=finished_at,
                source_type="creation_proposal",
                source_id=proposal_id,
                target_action="creation_proposal_to_scaffold_and_bootstrap",
                actor=actor,
                dry_run=False,
                result_status="partial",
                created_artifacts=sorted(set(created)),
                rationale=rationale,
                notes=notes,
                nested_results=nested,
            )
            return _emit_promotion(root, out, write=write_promotion_artifact, promotion_id=promotion_id)
    elif bootstrap and dry_run:
        notes.append("Bootstrap skipped: scaffold was dry-run only.")

    finished_at, _ = _now_ids()
    status = "success"
    tgt = "creation_proposal_to_scaffold"
    if bootstrap and not dry_run and nested.get("creation_bootstrap"):
        tgt = "creation_proposal_to_scaffold_and_bootstrap"

    out = _wrap_payload(
        promotion_id=promotion_id,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        source_type="creation_proposal",
        source_id=proposal_id,
        target_action=tgt,
        actor=actor,
        dry_run=dry_run,
        result_status=status,
        created_artifacts=sorted(set(created)),
        rationale=rationale,
        notes=notes,
        nested_results=nested,
    )
    return _emit_promotion(root, out, write=write_promotion_artifact, promotion_id=promotion_id)


def run_promote_bootstrap(
    repo_root: Path,
    *,
    product_id: str,
    minimal: bool = False,
    dry_run: bool = False,
    write_promotion_artifact: bool = True,
    write_stage_artifacts: bool = True,
    actor: str = "operator",
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """Promotion: existing scaffolded product → creation bootstrap."""
    root = repo_root.resolve()
    started_at, promotion_id = _now_ids()
    rationale = (
        "Explicit promotion: run first Argus loop (signals, findings, optional decisions) on an existing product."
    )

    pl = run_product_creation_bootstrap(
        root,
        product_id=product_id,
        minimal=minimal,
        dry_run=dry_run,
        write_artifacts=write_stage_artifacts,
        products_dir=products_dir,
    )
    nested = {"creation_bootstrap": pl}
    created = _paths_from_bootstrap(pl) if pl.get("ok") else []
    finished_at, _ = _now_ids()
    status = "success" if pl.get("ok") else "failed"
    out = _wrap_payload(
        promotion_id=promotion_id,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        source_type="product",
        source_id=product_id,
        target_action="scaffolded_product_to_bootstrap",
        actor=actor,
        dry_run=dry_run,
        result_status=status,
        created_artifacts=created,
        rationale=rationale,
        notes=[] if pl.get("ok") else [str(pl.get("error") or "bootstrap failed")],
        nested_results=nested,
    )
    return _emit_promotion(root, out, write=write_promotion_artifact, promotion_id=promotion_id)


def run_promote_deprecation(
    repo_root: Path,
    *,
    proposal_id: str,
    dry_run: bool = False,
    write_promotion_artifact: bool = True,
    write_stage_artifacts: bool = True,
    actor: str = "operator",
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Promotion: deprecation proposal → deprecation plan artifact (plan-only; no product mutation).

    ``dry_run=True`` evaluates the plan without writing ``runs/products/deprecation_plan/*``.
    """
    root = repo_root.resolve()
    started_at, promotion_id = _now_ids()
    rationale = (
        "Explicit promotion: materialize a structured deprecation plan from a stored proposal "
        "(documentation only — no deletion, no product.yaml mutation)."
    )

    if dry_run:
        pl = evaluate_deprecation_plan(root, proposal_id, products_dir=products_dir)
    else:
        pl = run_deprecation_plan(
            root,
            proposal_id=proposal_id,
            write_artifacts=write_stage_artifacts,
            products_dir=products_dir,
        )
    nested = {"deprecation_plan": pl}
    created = [] if dry_run or not pl.get("ok") else _paths_from_deprecation_plan(root, pl)
    finished_at, _ = _now_ids()
    status = "success" if pl.get("ok") else "failed"
    out = _wrap_payload(
        promotion_id=promotion_id,
        started_at_utc=started_at,
        finished_at_utc=finished_at,
        source_type="deprecation_proposal",
        source_id=proposal_id,
        target_action="deprecation_proposal_to_plan",
        actor=actor,
        dry_run=dry_run,
        result_status=status,
        created_artifacts=created,
        rationale=rationale,
        notes=[] if pl.get("ok") else [str(pl.get("error") or "plan failed")],
        nested_results=nested,
    )
    return _emit_promotion(root, out, write=write_promotion_artifact, promotion_id=promotion_id)


__all__ = [
    "LIFECYCLE_PROMOTION_ACTION_SCHEMA",
    "promotion_actions_dir",
    "render_lifecycle_promotion_markdown",
    "run_promote_bootstrap",
    "run_promote_creation",
    "run_promote_deprecation",
    "write_lifecycle_promotion_artifacts",
]
