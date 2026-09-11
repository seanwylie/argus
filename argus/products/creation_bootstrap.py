"""
First Argus loop after product creation — validate, collect signals, generate findings,
and optionally decisions + ideas. Reuses the same engines as the standalone CLIs.
"""

from __future__ import annotations

import shutil
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import SeverityLevel
from argus.core.serialize import dumps_json
from argus.experiments.execution_apply import apply_execution_outcomes
from argus.findings.engine import generate_findings
from argus.findings.experiment_surfaced import merged_findings_for_decisions
from argus.findings.persistence import save_findings_bundle
from argus.input.apply import apply_to_findings
from argus.products.inventory import build_inventory
from argus.products.loader import attach_paths, load_yaml_file
from argus.products.validate import validate_manifest

PRODUCT_CREATION_BOOTSTRAP_SCHEMA = "argus.product_creation_bootstrap.v1"

_SEVERITY_ORDER: dict[SeverityLevel, int] = {
    SeverityLevel.CRITICAL: 0,
    SeverityLevel.HIGH: 1,
    SeverityLevel.MEDIUM: 2,
    SeverityLevel.LOW: 3,
    SeverityLevel.INFO: 4,
}


def creation_bootstrap_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "products" / "creation_bootstrap"


def _sorted_findings_preview(findings: list[Any], *, limit: int = 5) -> list[dict[str, Any]]:
    def rank(f: Any) -> tuple[int, str]:
        sev = f.severity if hasattr(f, "severity") else SeverityLevel.MEDIUM
        r = _SEVERITY_ORDER.get(sev, 99)
        return (r, str(getattr(f, "title", "")))

    out: list[dict[str, Any]] = []
    for f in sorted(findings, key=rank)[:limit]:
        out.append({
            "title": str(getattr(f, "title", "")),
            "severity": f.severity.value if hasattr(f, "severity") else "",
            "kind": f.kind.value if hasattr(f, "kind") else "",
        })
    return out


def evaluate_product_creation_bootstrap(
    repo_root: Path,
    product_id: str,
    *,
    minimal: bool = False,
    dry_run: bool = False,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """
    Run post-creation bootstrap for one product.

    * **minimal:** signals collect + findings generate only.
    * **full:** also decisions generate + ideas pipeline (lightweight flags: no LLM idea expansion).
    * **dry_run:** validate product only; no signals/findings/decisions/ideas execution.
    """
    root = repo_root.resolve()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evaluated_at = datetime.now(timezone.utc).isoformat()
    pid = str(product_id).strip()
    mode = "minimal" if minimal else "full"

    base: dict[str, Any] = {
        "schema": PRODUCT_CREATION_BOOTSTRAP_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "ok": False,
        "dry_run": dry_run,
        "product_id": pid,
        "bootstrap_mode": mode,
        "steps_executed": [],
        "artifacts_created": [],
        "initial_direction_summary": {},
    }

    inv = build_inventory(root, products_dir=products_dir)
    if pid not in inv.valid:
        errs = []
        for x in inv.invalid:
            if x.product_id == pid:
                errs = list(x.errors)
                break
        base["error"] = (
            f"Unknown or invalid product {pid!r}"
            + (f": {'; '.join(errs)}" if errs else "")
        )
        base["steps_executed"].append({
            "step": "inventory",
            "status": "failed",
            "detail": base["error"],
        })
        return base

    node = inv.valid[pid].node
    product_root = (root / node.product_root).resolve()
    config_path = product_root / "product.yaml"
    raw, err = load_yaml_file(config_path)
    if err is not None or raw is None:
        base["error"] = f"Cannot load product.yaml: {err}"
        base["steps_executed"].append({
            "step": "validate",
            "status": "failed",
            "detail": base["error"],
        })
        return base

    merged = attach_paths(
        raw,
        repo_root=root,
        product_root=product_root,
        config_path=config_path,
    )
    vresult = validate_manifest(
        merged,
        repo_root=root,
        product_root=product_root,
        config_path=config_path,
    )
    if vresult.errors:
        base["error"] = "validation failed: " + "; ".join(vresult.errors)
        base["steps_executed"].append({
            "step": "validate",
            "status": "failed",
            "detail": base["error"],
        })
        return base

    try:
        v_detail = str(config_path.relative_to(root))
    except ValueError:
        v_detail = str(config_path)
    base["steps_executed"].append({
        "step": "validate",
        "status": "ok",
        "detail": v_detail,
        "warnings": list(vresult.warnings),
    })

    if dry_run:
        base["ok"] = True
        base["steps_executed"].extend([
            {"step": "signals_collect", "status": "skipped", "reason": "dry_run"},
            {"step": "findings_generate", "status": "skipped", "reason": "dry_run"},
            {"step": "decisions_generate", "status": "skipped", "reason": "dry_run"},
            {"step": "ideas_pipeline", "status": "skipped", "reason": "dry_run"},
        ])
        base["initial_direction_summary"] = {
            "note": "Dry run — validation only; no signals, findings, or direction artifacts.",
            "bootstrap_mode": mode,
        }
        return base

    artifacts: list[str] = []
    steps: list[dict[str, Any]] = list(base["steps_executed"])

    # --- signals (reuse signals runner + persistence) ---
    try:
        from argus.products.external_bindings import parse_external_bindings
        from argus.signals.adapters import default_builtin_adapters
        from argus.signals.persistence import save_collection
        from argus.signals.registry import AdapterRegistry
        from argus.signals.runner import collect_for_product, product_root_path

        reg = AdapterRegistry(default_builtin_adapters())
        records = collect_for_product(
            root,
            node,
            reg,
            merge_adapter_layer=False,
        )
        coll_path, norm = save_collection(
            root,
            pid,
            records,
            signal_manifest=node.signal_manifest,
            product_root=product_root_path(root, node),
            external_bindings=parse_external_bindings(node.raw_extensions),
        )
        apply_execution_outcomes(root)
        rel = str(coll_path.relative_to(root))
        artifacts.append(rel)
        steps.append({
            "step": "signals_collect",
            "status": "ok",
            "detail": {"record_count": len(norm), "collection_path": rel},
        })
        signal_count = len(norm)
    except Exception as e:
        base["error"] = f"{type(e).__name__}: {e}"
        base["traceback"] = traceback.format_exc()
        steps.append({
            "step": "signals_collect",
            "status": "failed",
            "detail": base["error"],
        })
        base["steps_executed"] = steps
        return base

    # --- findings ---
    try:
        findings = generate_findings(node, records, repo_root=root)
        findings = apply_to_findings(root, pid, findings)
        fpath = save_findings_bundle(root, pid, findings)
        rel_f = str(fpath.relative_to(root))
        artifacts.append(rel_f)
        latest_f = str((root / "runs" / "findings" / "latest" / f"{pid}.json").relative_to(root))
        if latest_f not in artifacts:
            artifacts.append(latest_f)
        steps.append({
            "step": "findings_generate",
            "status": "ok",
            "detail": {"finding_count": len(findings), "saved_path": rel_f},
        })
    except Exception as e:
        base["error"] = f"{type(e).__name__}: {e}"
        base["traceback"] = traceback.format_exc()
        steps.append({
            "step": "findings_generate",
            "status": "failed",
            "detail": base["error"],
        })
        base["steps_executed"] = steps
        base["artifacts_created"] = artifacts
        return base

    direction: dict[str, Any] = {
        "bootstrap_mode": mode,
        "signal_record_count": signal_count,
        "finding_count": len(findings),
        "top_findings": _sorted_findings_preview(findings),
    }

    if minimal:
        base.update({
            "ok": True,
            "steps_executed": steps,
            "artifacts_created": sorted(set(artifacts)),
            "initial_direction_summary": direction,
        })
        return base

    # --- decisions ---
    try:
        from argus.decision.engine import generate_decisions
        from argus.decision.evolution import build_decision_lineage_payload
        from argus.decision.persistence import save_product_decisions
        from argus.decision_assessment.evaluate import evaluate_decision_context
        from argus.decision_assessment.persistence import save_assessment

        merged_findings = merged_findings_for_decisions(root, pid)
        assessment, candidates = generate_decisions(node, merged_findings, repo_root=root)
        ctx_obj = evaluate_decision_context(root, pid)
        ctx_payload = ctx_obj.to_jsonable()
        save_assessment(root, ctx_obj)
        lin = build_decision_lineage_payload(root, pid, candidates)
        dpath = save_product_decisions(
            root,
            pid,
            assessment,
            candidates,
            decision_context=ctx_payload,
            lineage_bundle_extras=lin["bundle_extras"],
            lineage_candidate_augmentations=lin["candidate_augmentations"],
        )
        rel_d = str(dpath.relative_to(root))
        artifacts.append(rel_d)
        latest_d = str((root / "runs" / "decisions" / "latest" / f"{pid}.json").relative_to(root))
        if latest_d not in artifacts:
            artifacts.append(latest_d)
        steps.append({
            "step": "decisions_generate",
            "status": "ok",
            "detail": {
                "lifecycle_stage": assessment.stage.value,
                "candidate_count": len(candidates),
                "saved_path": rel_d,
            },
        })
        direction["lifecycle_stage"] = assessment.stage.value
        direction["top_decision_candidates"] = [
            {
                "summary": str(c.summary)[:200],
                "intent": (c.metadata or {}).get("intent"),
                "priority_score": c.priority_score,
            }
            for c in candidates[:5]
        ]
    except Exception as e:
        base["error"] = f"{type(e).__name__}: {e}"
        base["traceback"] = traceback.format_exc()
        steps.append({
            "step": "decisions_generate",
            "status": "failed",
            "detail": base["error"],
        })
        base["steps_executed"] = steps
        base["artifacts_created"] = sorted(set(artifacts))
        base["initial_direction_summary"] = direction
        return base

    # --- ideas (lightweight: no LLM expansion, no advisor expansion) ---
    try:
        from argus.idea_generation.pipeline import run_pipeline as run_ideas_pipeline

        ideas_path, ideas_bundle = run_ideas_pipeline(
            root,
            pid,
            seed=f"creation_bootstrap:{run_id}",
            include_mutation=True,
            max_ideas=10,
            max_per_diversity_bucket=3,
            advisor_expansion=False,
            llm_idea_expansion=False,
            findings_rows=findings,
        )
        rel_i = str(ideas_path.relative_to(root))
        artifacts.append(rel_i)
        latest_i = str((root / "runs" / "ideas" / "latest.json").relative_to(root))
        if latest_i not in artifacts:
            artifacts.append(latest_i)
        steps.append({
            "step": "ideas_pipeline",
            "status": "ok",
            "detail": {
                "idea_count": len(ideas_bundle.ideas),
                "saved_path": rel_i,
            },
        })
        direction["top_ideas"] = [
            {"title": i.title[:200]}
            for i in ideas_bundle.ideas[:5]
        ]
    except Exception as e:
        base["error"] = f"{type(e).__name__}: {e}"
        base["traceback"] = traceback.format_exc()
        steps.append({
            "step": "ideas_pipeline",
            "status": "failed",
            "detail": base["error"],
        })
        base["steps_executed"] = steps
        base["artifacts_created"] = sorted(set(artifacts))
        base["initial_direction_summary"] = direction
        return base

    base.update({
        "ok": True,
        "steps_executed": steps,
        "artifacts_created": sorted(set(artifacts)),
        "initial_direction_summary": direction,
    })
    return base


def render_product_creation_bootstrap_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Product creation bootstrap",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
    ]
    if not payload.get("ok"):
        lines.append(f"**Error:** {payload.get('error')}")
        lines.append("")
        return "\n".join(lines)

    lines.append(f"- **Product:** `{payload.get('product_id')}`")
    lines.append(f"- **Mode:** {payload.get('bootstrap_mode')}")
    lines.append(f"- **Dry run:** {payload.get('dry_run')}")
    summ = payload.get("initial_direction_summary") or {}
    if summ and not payload.get("dry_run"):
        lines.append(f"- **Signals (records):** {summ.get('signal_record_count', '—')}")
        lines.append(f"- **Findings:** {summ.get('finding_count', '—')}")
        if summ.get("lifecycle_stage"):
            lines.append(f"- **Lifecycle (from decisions):** `{summ.get('lifecycle_stage')}`")
    lines.extend(["", "## Steps", ""])
    for s in payload.get("steps_executed") or []:
        st = s.get("status", "")
        name = s.get("step", "")
        lines.append(f"- **{name}** — {st}")
    lines.extend(["", "## Artifacts", ""])
    for a in payload.get("artifacts_created") or []:
        lines.append(f"- `{a}`")
    lines.append("")
    return "\n".join(lines)


def write_product_creation_bootstrap_artifacts(
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
    d = creation_bootstrap_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_product_creation_bootstrap_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_product_creation_bootstrap(
    repo_root: Path,
    *,
    product_id: str,
    minimal: bool = False,
    dry_run: bool = False,
    write_artifacts: bool = True,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """Evaluate bootstrap and optionally write ``runs/products/creation_bootstrap/*``."""
    payload = evaluate_product_creation_bootstrap(
        repo_root,
        product_id,
        minimal=minimal,
        dry_run=dry_run,
        products_dir=products_dir,
    )
    if (
        write_artifacts
        and not dry_run
        and payload.get("schema") == PRODUCT_CREATION_BOOTSTRAP_SCHEMA
        and payload.get("ok")
    ):
        write_product_creation_bootstrap_artifacts(repo_root, payload)
    return payload


def bootstrap_created_product(
    repo_root: Path,
    product_id: str,
    *,
    minimal: bool = False,
    dry_run: bool = False,
    write_artifacts: bool = True,
    products_dir: Path | None = None,
) -> dict[str, Any]:
    """Alias for :func:`run_product_creation_bootstrap` (explicit API for callers)."""
    return run_product_creation_bootstrap(
        repo_root,
        product_id=product_id,
        minimal=minimal,
        dry_run=dry_run,
        write_artifacts=write_artifacts,
        products_dir=products_dir,
    )


__all__ = [
    "PRODUCT_CREATION_BOOTSTRAP_SCHEMA",
    "bootstrap_created_product",
    "creation_bootstrap_dir",
    "evaluate_product_creation_bootstrap",
    "render_product_creation_bootstrap_markdown",
    "run_product_creation_bootstrap",
    "write_product_creation_bootstrap_artifacts",
]
