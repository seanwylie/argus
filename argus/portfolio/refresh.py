"""
End-to-end portfolio refresh: validate products, collect signals, findings, decisions, reports.

No remote execution or orchestration — filesystem artifacts only.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.engine import generate_decisions
from argus.decision.evolution import build_decision_lineage_payload
from argus.decision.persistence import save_portfolio_report, save_product_decisions
from argus.decision.portfolio import PortfolioRow, rank_portfolio_from_decisions
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.decision_assessment.persistence import save_assessment
from argus.economics.analyze import analyze_product_economics
from argus.findings.engine import generate_findings
from argus.findings.persistence import save_findings_bundle
from argus.input.apply import apply_to_findings
from argus.lifecycle.model import LifecycleAssessment
from argus.products.external_bindings import parse_external_bindings
from argus.products.inventory import build_inventory
from argus.signals.adapters import default_builtin_adapters
from argus.signals.persistence import load_latest_bundle, save_collection
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_inventory, product_root_path


def _portfolio_dir(repo: Path) -> Path:
    return repo.resolve() / "runs" / "portfolio"


def _portfolio_latest(repo: Path) -> Path:
    """Path to ``runs/portfolio/latest`` (does not create the directory)."""
    return _portfolio_dir(repo) / "latest"


def _ensure_portfolio_latest(repo: Path) -> Path:
    d = _portfolio_latest(repo)
    d.mkdir(parents=True, exist_ok=True)
    return d


EMPTY_PORTFOLIO_OPERATOR_GUIDANCE: dict[str, Any] = {
    "summary": (
        "Validated product inventory is empty — there is no `products/<id>/product.yaml` yet. "
        "This is a normal zero-state before the first product is added; Argus is not broken."
    ),
    "next_actions": [
        "`argus products propose-creation` — mission-grounded proposals for what to add next",
        "`argus products create` — scaffold a new `products/<id>/` tree",
        "`python tools/import_product.py` — import an existing codebase (see docs/importer-operations.md)",
        "`argus portfolio refresh` — re-run after your first product validates",
    ],
}


def _format_empty_portfolio_summary_text(
    *,
    generated_at: str,
    invalid_count: int,
) -> str:
    lines = [
        "Argus portfolio summary (zero-state)",
        "====================================",
        "",
        f"Generated (UTC): {generated_at}",
        "Products (valid): 0",
        f"Products (invalid, skipped): {invalid_count}",
        "",
        EMPTY_PORTFOLIO_OPERATOR_GUIDANCE["summary"],
        "",
        "Suggested next steps",
        "--------------------",
    ]
    for a in EMPTY_PORTFOLIO_OPERATOR_GUIDANCE["next_actions"]:
        lines.append(f"  • {a}")
    lines.append("")
    return "\n".join(lines)


def _format_summary_text(
    *,
    generated_at: str,
    valid_count: int,
    invalid_count: int,
    signal_records: int,
    findings_products: int,
    decisions_products: int,
    ranked: list[PortfolioRow],
) -> str:
    lines = [
        "Argus portfolio summary",
        "=======================",
        "",
        f"Generated (UTC): {generated_at}",
        f"Products (valid): {valid_count}",
        f"Products (invalid, skipped): {invalid_count}",
        f"Signal records collected: {signal_records}",
        f"Findings bundles written: {findings_products}",
        f"Decision bundles written: {decisions_products}",
        "",
        "Ranked next actions (top candidate per product)",
        "-----------------------------------------------",
    ]
    if not ranked:
        lines.append("(none — no decision candidates produced; check findings and rules.)")
    else:
        for r in ranked:
            kc = " [kill_candidate]" if r.assessment.kill_candidate else ""
            lines.append(
                f"{r.rank}. {r.product_id} ({r.lifecycle_stage}) -> {r.top_intent} "
                f"score={r.priority_score:.2f}{kc}"
            )
            lines.append(f"   {r.summary}")
            for w in r.freshness_warnings:
                lines.append(f"   [freshness] {w}")
    lines.append("")
    return "\n".join(lines)


def _per_product_text(
    product_id: str,
    *,
    lifecycle: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> str:
    lines = [
        f"Product: {product_id}",
        "=========",
        "",
        f"Declared stage: {lifecycle.get('stage', '?')}",
        f"kill_candidate: {lifecycle.get('kill_candidate')}",
        f"scores: {lifecycle.get('scores')}",
        "",
        "Top candidates:",
    ]
    for c in candidates[:12]:
        md = c.get("metadata") or {}
        intent = md.get("intent", "?")
        lines.append(
            f"  [{intent}] score={c.get('priority_score')}  {c.get('summary', '')}"
        )
    lines.append("")
    return "\n".join(lines)


def run_portfolio_refresh(
    repo: Path,
    *,
    products_dir: Path | None = None,
    per_product_reports: bool = False,
    no_save: bool = False,
) -> tuple[int, dict[str, Any]]:
    """
    Run the full local pipeline and write portfolio summary + JSON (+ optional per-product files).

    Returns ``(exit_code, payload)`` where ``exit_code`` is 0 on success, 1 if inventory invalid
    or portfolio could not be built.
    """
    root = repo.resolve()
    started = datetime.now(timezone.utc).isoformat()
    steps: list[dict[str, Any]] = []

    inv = build_inventory(root, products_dir=products_dir)
    if inv.invalid:
        payload = {
            "schema": "argus.portfolio_refresh.v1",
            "started_at_utc": started,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
            "ok": False,
            "error": "invalid_products",
            "invalid": [
                {
                    "product_id": x.product_id,
                    "product_root": x.product_root,
                    "config_path": x.config_path,
                    "errors": x.errors,
                }
                for x in inv.invalid
            ],
            "steps": steps,
        }
        return 1, payload

    reg = AdapterRegistry(default_builtin_adapters())
    inv2, by_id = collect_inventory(root, reg, products_dir=products_dir)
    signal_total = sum(len(recs) for recs in by_id.values())
    steps.append(
        {
            "step": "signals_collect",
            "products": len(by_id),
            "records": signal_total,
        }
    )
    if not no_save:
        for pid, records in by_id.items():
            pnode = inv2.valid[pid].node
            _, _ = save_collection(
                root,
                pid,
                records,
                signal_manifest=pnode.signal_manifest,
                product_root=product_root_path(root, pnode),
                external_bindings=parse_external_bindings(pnode.raw_extensions),
            )

    findings_by_pid: dict[str, list] = {}
    findings_count = 0
    for pid, rec in inv2.valid.items():
        bundle = load_latest_bundle(root, pid)
        records: list = []
        if bundle is not None:
            records = bundle.records
        findings = generate_findings(rec.node, records, repo_root=root)
        findings = apply_to_findings(root, pid, findings)
        findings_by_pid[pid] = findings
        if not no_save:
            save_findings_bundle(root, pid, findings)
        findings_count += 1
    steps.append({"step": "findings_generate", "products": findings_count})

    decisions_raw: dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]] = {}
    decisions_count = 0
    decisions_detail: dict[str, dict[str, Any]] = {}
    for pid, rec in inv2.valid.items():
        findings_list = findings_by_pid[pid]
        a, cands = generate_decisions(rec.node, findings_list, repo_root=root)
        decisions_raw[pid] = (a, cands)
        dctx = evaluate_decision_context(root, pid)
        if not no_save:
            save_assessment(root, dctx)
            lin = build_decision_lineage_payload(root, pid, cands)
            save_product_decisions(
                root,
                pid,
                a,
                cands,
                decision_context=dctx.to_jsonable(),
                lineage_bundle_extras=lin["bundle_extras"],
                lineage_candidate_augmentations=lin["candidate_augmentations"],
            )
        decisions_count += 1
        decisions_detail[pid] = {
            "lifecycle": {
                "stage": a.stage.value,
                "scores": a.as_dict(),
                "kill_candidate": a.kill_candidate,
                "reasoning": dict(a.reasoning),
            },
            "candidates": [to_jsonable(c) for c in cands],
            "decision_context": dctx.to_jsonable(),
        }

    steps.append({"step": "decisions_generate", "products": decisions_count})

    rows, per_product = rank_portfolio_from_decisions(inv2, decisions_raw)

    if inv2.summary.valid_count == 0:
        finished = datetime.now(timezone.utc).isoformat()
        payload: dict[str, Any] = {
            "schema": "argus.portfolio_refresh.v1",
            "started_at_utc": started,
            "finished_at_utc": finished,
            "ok": True,
            "portfolio_state": "empty_portfolio",
            "zero_state": True,
            "operator_guidance": dict(EMPTY_PORTFOLIO_OPERATOR_GUIDANCE),
            "steps": steps,
            "inventory": {
                "valid_count": 0,
                "invalid_count": inv2.summary.invalid_count,
            },
            "ranked": [],
            "saved": not no_save,
        }
        if not no_save:
            latest = _ensure_portfolio_latest(root)
            payload["paths"] = {
                "portfolio_refresh": str((latest / "refresh.json").resolve()),
                "portfolio_summary": str((latest / "summary.txt").resolve()),
            }
            (latest / "refresh.json").write_text(dumps_json(payload), encoding="utf-8")
            (latest / "summary.txt").write_text(
                _format_empty_portfolio_summary_text(
                    generated_at=finished,
                    invalid_count=inv2.summary.invalid_count,
                ),
                encoding="utf-8",
            )
        return 0, payload

    if not rows:
        finished = datetime.now(timezone.utc).isoformat()
        payload = {
            "schema": "argus.portfolio_refresh.v1",
            "started_at_utc": started,
            "finished_at_utc": finished,
            "ok": False,
            "error": "no_ranked_portfolio",
            "hint": (
                "No ranked portfolio rows despite validated products — no decision candidates "
                "(e.g. empty findings/candidates for every product). Check signals and findings."
            ),
            "steps": steps,
            "inventory": {
                "valid_count": inv2.summary.valid_count,
                "invalid_count": inv2.summary.invalid_count,
            },
        }
        if not no_save:
            latest = _ensure_portfolio_latest(root)
            (latest / "refresh.json").write_text(dumps_json(payload), encoding="utf-8")
            (latest / "summary.txt").write_text(
                _format_summary_text(
                    generated_at=finished,
                    valid_count=inv2.summary.valid_count,
                    invalid_count=inv2.summary.invalid_count,
                    signal_records=signal_total,
                    findings_products=findings_count,
                    decisions_products=decisions_count,
                    ranked=[],
                ),
                encoding="utf-8",
            )
        return 1, payload

    if not no_save:
        save_portfolio_report(root, rows, per_product)

    finished = datetime.now(timezone.utc).isoformat()
    ranked_json = []
    economics_by_product: dict[str, Any] = {}
    for r in rows:
        rec = inv2.valid.get(r.product_id)
        econ_payload = None
        if rec is not None:
            bundle = load_latest_bundle(root, r.product_id)
            records = bundle.records if bundle else []
            pe = analyze_product_economics(rec.node, records)
            econ_payload = to_jsonable(pe)
            economics_by_product[r.product_id] = econ_payload
        ranked_json.append(
            {
                "rank": r.rank,
                "product_id": r.product_id,
                "lifecycle_stage": r.lifecycle_stage,
                "top_intent": r.top_intent,
                "priority_score": r.priority_score,
                "summary": r.summary,
                "kill_candidate": r.assessment.kill_candidate,
                "scores": r.assessment.as_dict(),
                "economics": econ_payload,
            }
        )

    payload = {
        "schema": "argus.portfolio_refresh.v1",
        "started_at_utc": started,
        "finished_at_utc": finished,
        "ok": True,
        "steps": steps,
        "inventory": {
            "valid_count": inv2.summary.valid_count,
            "invalid_count": inv2.summary.invalid_count,
        },
        "ranked": ranked_json,
        "economics_by_product": economics_by_product,
        "paths": (
            None
            if no_save
            else {
                "decisions_portfolio": str(
                    root / "runs" / "decisions" / "latest" / "portfolio.json"
                ),
                "portfolio_refresh": str(_portfolio_latest(root) / "refresh.json"),
                "portfolio_summary": str(_portfolio_latest(root) / "summary.txt"),
            }
        ),
        "saved": not no_save,
    }

    if not no_save:
        latest = _ensure_portfolio_latest(root)
        (latest / "refresh.json").write_text(dumps_json(payload), encoding="utf-8")
        (latest / "summary.txt").write_text(
            _format_summary_text(
                generated_at=finished,
                valid_count=inv2.summary.valid_count,
                invalid_count=inv2.summary.invalid_count,
                signal_records=signal_total,
                findings_products=findings_count,
                decisions_products=decisions_count,
                ranked=rows,
            ),
            encoding="utf-8",
        )
        if per_product_reports:
            pdir = latest / "products"
            pdir.mkdir(parents=True, exist_ok=True)
            for pid, detail in decisions_detail.items():
                (pdir / f"{pid}.txt").write_text(
                    _per_product_text(
                        pid,
                        lifecycle=detail["lifecycle"],
                        candidates=detail["candidates"],
                    ),
                    encoding="utf-8",
            )

    return 0, payload

