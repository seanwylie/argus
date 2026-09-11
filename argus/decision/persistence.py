"""Filesystem persistence for decision outputs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.serialize import decision_candidate_to_dict, dumps_json
from argus.lifecycle.model import LifecycleAssessment


def generations_dir(repo_root: Path) -> Path:
    return repo_root / "runs" / "decisions" / "generations"


def latest_product_path(repo_root: Path, product_id: str) -> Path:
    return repo_root / "runs" / "decisions" / "latest" / f"{product_id}.json"


def latest_portfolio_path(repo_root: Path) -> Path:
    return repo_root / "runs" / "decisions" / "latest" / "portfolio.json"


def assessment_to_dict(a: LifecycleAssessment) -> dict[str, Any]:
    return {
        "product_id": a.product_id,
        "stage": a.stage.value,
        "scores": a.as_dict(),
        "reasoning": dict(a.reasoning),
        "kill_candidate": a.kill_candidate,
        "metadata": dict(a.metadata),
    }


def save_product_decisions(
    repo_root: Path,
    product_id: str,
    assessment: LifecycleAssessment,
    candidates: list[DecisionCandidate],
    *,
    write_latest: bool = True,
    decision_context: dict[str, Any] | None = None,
    lineage_bundle_extras: dict[str, Any] | None = None,
    lineage_candidate_augmentations: list[dict[str, Any]] | None = None,
) -> Path:
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = generations_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    aug = lineage_candidate_augmentations or []
    cand_rows: list[dict[str, Any]] = []
    for i, c in enumerate(candidates):
        row = dict(decision_candidate_to_dict(c))
        if i < len(aug):
            row.update(aug[i])
        cand_rows.append(row)
    bundle: dict[str, Any] = {
        "schema": "argus.decisions_bundle.v1",
        "product_id": product_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "lifecycle": assessment_to_dict(assessment),
        "candidates": cand_rows,
    }
    if decision_context is not None:
        bundle["decision_context"] = decision_context
    if lineage_bundle_extras:
        for k, v in lineage_bundle_extras.items():
            bundle[k] = v
    path = base / f"{ts}_{product_id}.json"
    path.write_text(dumps_json(bundle), encoding="utf-8")
    if write_latest:
        lp = latest_product_path(root, product_id)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(dumps_json(bundle), encoding="utf-8")
    return path


def save_portfolio_report(
    repo_root: Path,
    portfolio_rows: list[Any],
    per_product: dict[str, Any],
) -> Path:
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = generations_dir(root)
    base.mkdir(parents=True, exist_ok=True)

    def row_to_dict(r: Any) -> dict[str, Any]:
        out = {
            "rank": r.rank,
            "product_id": r.product_id,
            "lifecycle_stage": r.lifecycle_stage,
            "top_intent": r.top_intent,
            "priority_score": r.priority_score,
            "summary": r.summary,
            "lifecycle_scores": r.assessment.as_dict(),
            "kill_candidate": r.assessment.kill_candidate,
        }
        fw = getattr(r, "freshness_warnings", ()) or ()
        if fw:
            out["freshness_warnings"] = list(fw)
        return out

    bundle = {
        "schema": "argus.portfolio_report.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "ranked": [row_to_dict(r) for r in portfolio_rows],
        "per_product_ids": list(per_product.keys()),
    }
    path = base / f"{ts}_portfolio.json"
    path.write_text(dumps_json(bundle), encoding="utf-8")
    lp = latest_portfolio_path(root)
    lp.parent.mkdir(parents=True, exist_ok=True)
    lp.write_text(dumps_json(bundle), encoding="utf-8")
    return path


def load_latest_product_decisions(repo_root: Path, product_id: str) -> dict[str, Any] | None:
    p = latest_product_path(repo_root.resolve(), product_id)
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def load_latest_portfolio(repo_root: Path) -> dict[str, Any] | None:
    p = latest_portfolio_path(repo_root.resolve())
    if not p.is_file():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
