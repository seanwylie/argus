"""
Stage runners for ``argus loop run``.

**Implemented:** ``run_discover``, ``run_signals``, ``run_findings``, ``run_decisions`` — full work that
writes under ``runs/signals/``, ``runs/findings/``, ``runs/decisions/`` (and per-loop stage dirs).

Planning and escalation are **not** loop stages; use ``argus planning weekly`` and
``argus escalation generate <product>`` (see orchestrator README).
"""

from __future__ import annotations

import json
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.decision import DecisionCandidate
from argus.core.serialize import dumps_json, to_jsonable
from argus.decision.engine import generate_decisions
from argus.decision.evolution import build_decision_lineage_payload
from argus.decision.persistence import save_portfolio_report, save_product_decisions
from argus.decision.portfolio import rank_portfolio_from_decisions
from argus.decision_assessment.evaluate import evaluate_decision_context
from argus.decision_assessment.persistence import save_assessment
from argus.findings.engine import generate_findings
from argus.findings.persistence import save_findings_bundle
from argus.lifecycle.model import LifecycleAssessment
from argus.orchestrator.stages import LoopStage, StageResult
from argus.products.external_bindings import parse_external_bindings
from argus.products.inventory import ProductInventory, build_inventory
from argus.signals.adapters import default_builtin_adapters
from argus.signals.persistence import load_latest_bundle, save_collection
from argus.signals.registry import AdapterRegistry
from argus.signals.runner import collect_for_product, collect_inventory, product_root_path


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, sort_keys=True) + "\n")


@dataclass
class StageLogger:
    """Structured JSONL log for one stage (``log.jsonl``)."""

    stage_dir: Path
    stage: LoopStage

    def _line(self, level: str, event: str, **fields: Any) -> None:
        _append_jsonl(
            self.stage_dir / "log.jsonl",
            {
                "timestamp_utc": _utc_now(),
                "stage": self.stage.value,
                "level": level,
                "event": event,
                **fields,
            },
        )

    def info(self, event: str, **fields: Any) -> None:
        self._line("INFO", event, **fields)

    def error(self, event: str, **fields: Any) -> None:
        self._line("ERROR", event, **fields)


@dataclass
class LoopContext:
    """Inputs shared across stages for one loop run."""

    repo: Path
    run_dir: Path
    products_dir: Path | None
    """If set, only these product ids participate (must be valid)."""
    target_product_ids: list[str]
    inventory: ProductInventory | None = None


def _stage_dir(run_dir: Path, stage: LoopStage) -> Path:
    return run_dir / "stages" / stage.value


def _write_output(stage_dir: Path, payload: dict[str, Any]) -> Path:
    stage_dir.mkdir(parents=True, exist_ok=True)
    out = stage_dir / "output.json"
    out.write_text(dumps_json(payload), encoding="utf-8")
    return out


def _rel_to_run(run_dir: Path, path: Path) -> str:
    return str(path.resolve().relative_to(run_dir.resolve()))


def run_discover(ctx: LoopContext, run_dir: Path) -> StageResult:
    stage = LoopStage.DISCOVER
    sd = _stage_dir(run_dir, stage)
    log = StageLogger(sd, stage)
    started = _utc_now()
    log.info("stage_start", run_id=run_dir.name)

    inv = build_inventory(ctx.repo, products_dir=ctx.products_dir)
    ctx.inventory = inv

    for pid in ctx.target_product_ids:
        if pid not in inv.valid:
            msg = (
                f"Product {pid!r} is not in the valid inventory. "
                "Fix product.yaml or omit --product to run all valid products."
            )
            log.error("invalid_product_filter", product_id=pid)
            finished = _utc_now()
            payload = {
                "schema": "argus.loop.stage.v1",
                "stage": stage.value,
                "ok": False,
                "started_at_utc": started,
                "finished_at_utc": finished,
                "error": msg,
            }
            out = _write_output(sd, payload)
            return StageResult(
                stage=stage,
                ok=False,
                started_at_utc=started,
                finished_at_utc=finished,
                error=msg,
                output_relpath=_rel_to_run(run_dir, out),
            )

    if inv.invalid:
        lines = [
            f"{x.product_id or '(unknown)'}: {x.product_root} — " + "; ".join(x.errors)
            for x in inv.invalid
        ]
        msg = "Invalid product manifests:\n" + "\n".join(lines)
        log.error("invalid_inventory", invalid_count=len(inv.invalid))
        finished = _utc_now()
        payload = {
            "schema": "argus.loop.stage.v1",
            "stage": stage.value,
            "ok": False,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "error": msg,
            "invalid": [
                {
                    "product_id": x.product_id,
                    "product_root": x.product_root,
                    "errors": x.errors,
                }
                for x in inv.invalid
            ],
        }
        out = _write_output(sd, payload)
        return StageResult(
            stage=stage,
            ok=False,
            started_at_utc=started,
            finished_at_utc=finished,
            error=msg,
            output_relpath=_rel_to_run(run_dir, out),
        )

    payload = {
        "schema": "argus.loop.stage.v1",
        "stage": stage.value,
        "ok": True,
        "started_at_utc": started,
        "finished_at_utc": _utc_now(),
        "valid_product_ids": sorted(ctx.target_product_ids),
        "summary": {
            "valid_count": inv.summary.valid_count,
            "invalid_count": inv.summary.invalid_count,
        },
        "products_dir": str(inv.products_dir),
    }
    out = _write_output(sd, payload)
    log.info("stage_complete", valid=len(ctx.target_product_ids))
    finished = payload["finished_at_utc"]
    return StageResult(
        stage=stage,
        ok=True,
        started_at_utc=started,
        finished_at_utc=str(finished),
        output_relpath=_rel_to_run(run_dir, out),
    )


def run_signals(ctx: LoopContext, run_dir: Path) -> StageResult:
    stage = LoopStage.SIGNALS
    sd = _stage_dir(run_dir, stage)
    log = StageLogger(sd, stage)
    started = _utc_now()
    log.info("stage_start")

    assert ctx.inventory is not None
    inv = ctx.inventory
    reg = AdapterRegistry(default_builtin_adapters())

    by_id: dict[str, list] = {}
    try:
        if len(ctx.target_product_ids) == 1:
            pid = ctx.target_product_ids[0]
            node = inv.valid[pid].node
            by_id[pid] = collect_for_product(ctx.repo, node, reg)
        else:
            _inv2, by_id = collect_inventory(
                ctx.repo, reg, products_dir=ctx.products_dir
            )
            by_id = {pid: by_id[pid] for pid in ctx.target_product_ids if pid in by_id}
    except Exception as e:
        finished = _utc_now()
        tb = traceback.format_exc()
        log.error("stage_failed", exc_type=type(e).__name__, message=str(e))
        payload = {
            "schema": "argus.loop.stage.v1",
            "stage": stage.value,
            "ok": False,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb,
        }
        out = _write_output(sd, payload)
        return StageResult(
            stage=stage,
            ok=False,
            started_at_utc=started,
            finished_at_utc=finished,
            error=payload["error"],
            output_relpath=_rel_to_run(run_dir, out),
            extra={"traceback": tb},
        )

    canonical: dict[str, str] = {}
    for pid, records in by_id.items():
        node = inv.valid[pid].node
        path, _norm = save_collection(
            ctx.repo,
            pid,
            records,
            signal_manifest=node.signal_manifest,
            product_root=product_root_path(ctx.repo, node),
            external_bindings=parse_external_bindings(node.raw_extensions),
        )
        canonical[pid] = str(path.relative_to(ctx.repo.resolve()))

    total = sum(len(r) for r in by_id.values())
    finished = _utc_now()
    payload = {
        "schema": "argus.loop.stage.v1",
        "stage": stage.value,
        "ok": True,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "record_count": total,
        "by_product": {pid: len(recs) for pid, recs in sorted(by_id.items())},
        "collection_paths_repo_relative": canonical,
    }
    out = _write_output(sd, payload)
    log.info("stage_complete", records=total, products=len(by_id))
    return StageResult(
        stage=stage,
        ok=True,
        started_at_utc=started,
        finished_at_utc=finished,
        output_relpath=_rel_to_run(run_dir, out),
    )


def run_findings(ctx: LoopContext, run_dir: Path) -> StageResult:
    stage = LoopStage.FINDINGS
    sd = _stage_dir(run_dir, stage)
    log = StageLogger(sd, stage)
    started = _utc_now()
    log.info("stage_start")

    assert ctx.inventory is not None
    inv = ctx.inventory

    findings_by_pid: dict[str, list] = {}
    try:
        for pid in ctx.target_product_ids:
            rec = inv.valid[pid]
            bundle = load_latest_bundle(ctx.repo, pid)
            records = bundle.records if bundle is not None else []
            findings = generate_findings(rec.node, records, repo_root=ctx.repo)
            findings_by_pid[pid] = findings
            save_findings_bundle(ctx.repo, pid, findings)
    except Exception as e:
        finished = _utc_now()
        tb = traceback.format_exc()
        log.error("stage_failed", exc_type=type(e).__name__, message=str(e))
        payload = {
            "schema": "argus.loop.stage.v1",
            "stage": stage.value,
            "ok": False,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb,
        }
        out = _write_output(sd, payload)
        return StageResult(
            stage=stage,
            ok=False,
            started_at_utc=started,
            finished_at_utc=finished,
            error=payload["error"],
            output_relpath=_rel_to_run(run_dir, out),
            extra={"traceback": tb},
        )

    finished = _utc_now()
    payload = {
        "schema": "argus.loop.stage.v1",
        "stage": stage.value,
        "ok": True,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "by_product": {
            pid: {
                "finding_count": len(fs),
                "kinds": _count_kinds(fs),
            }
            for pid, fs in findings_by_pid.items()
        },
        "findings_preview": {
            pid: [to_jsonable(f) for f in fs[:12]]
            for pid, fs in findings_by_pid.items()
        },
    }
    out = _write_output(sd, payload)
    log.info(
        "stage_complete",
        products=len(findings_by_pid),
        findings=sum(len(f) for f in findings_by_pid.values()),
    )
    return StageResult(
        stage=stage,
        ok=True,
        started_at_utc=started,
        finished_at_utc=finished,
        output_relpath=_rel_to_run(run_dir, out),
        extra={"findings_by_product": findings_by_pid},
    )


def _count_kinds(findings: list) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in findings:
        k = f.kind.value
        out[k] = out.get(k, 0) + 1
    return out


def run_decisions(ctx: LoopContext, run_dir: Path, findings_by_pid: dict[str, list]) -> StageResult:
    stage = LoopStage.DECISIONS
    sd = _stage_dir(run_dir, stage)
    log = StageLogger(sd, stage)
    started = _utc_now()
    log.info("stage_start")

    assert ctx.inventory is not None
    inv = ctx.inventory

    decisions_raw: dict[str, tuple[LifecycleAssessment, list[DecisionCandidate]]] = {}
    try:
        for pid in ctx.target_product_ids:
            findings_list = findings_by_pid[pid]
            node = inv.valid[pid].node
            a, cands = generate_decisions(node, findings_list, repo_root=ctx.repo)
            decisions_raw[pid] = (a, cands)
            dctx = evaluate_decision_context(ctx.repo, pid)
            save_assessment(ctx.repo, dctx)
            lin = build_decision_lineage_payload(ctx.repo, pid, cands)
            save_product_decisions(
                ctx.repo,
                pid,
                a,
                cands,
                decision_context=dctx.to_jsonable(),
                lineage_bundle_extras=lin["bundle_extras"],
                lineage_candidate_augmentations=lin["candidate_augmentations"],
            )
    except Exception as e:
        finished = _utc_now()
        tb = traceback.format_exc()
        log.error("stage_failed", exc_type=type(e).__name__, message=str(e))
        payload = {
            "schema": "argus.loop.stage.v1",
            "stage": stage.value,
            "ok": False,
            "started_at_utc": started,
            "finished_at_utc": finished,
            "error": f"{type(e).__name__}: {e}",
            "traceback": tb,
        }
        out = _write_output(sd, payload)
        return StageResult(
            stage=stage,
            ok=False,
            started_at_utc=started,
            finished_at_utc=finished,
            error=payload["error"],
            output_relpath=_rel_to_run(run_dir, out),
            extra={"traceback": tb},
        )

    rows, _per = rank_portfolio_from_decisions(inv, decisions_raw)
    portfolio_path: str | None = None
    if rows:
        path = save_portfolio_report(ctx.repo, rows, _per)
        portfolio_path = str(path.relative_to(ctx.repo.resolve()))
    else:
        log.info("portfolio_empty", hint="No ranked rows (no candidates or empty decisions).")

    finished = _utc_now()
    payload = {
        "schema": "argus.loop.stage.v1",
        "stage": stage.value,
        "ok": True,
        "started_at_utc": started,
        "finished_at_utc": finished,
        "ranked": [
            {
                "rank": r.rank,
                "product_id": r.product_id,
                "lifecycle_stage": r.lifecycle_stage,
                "top_intent": r.top_intent,
                "priority_score": r.priority_score,
                "summary": r.summary,
                "kill_candidate": r.assessment.kill_candidate,
            }
            for r in rows
        ],
        "portfolio_report_repo_relative": portfolio_path,
        "lifecycle_by_product": {
            pid: {
                "stage": a.stage.value,
                "kill_candidate": a.kill_candidate,
                "scores": a.as_dict(),
            }
            for pid, (a, _) in decisions_raw.items()
        },
    }
    out = _write_output(sd, payload)
    log.info("stage_complete", ranked=len(rows))
    return StageResult(
        stage=stage,
        ok=True,
        started_at_utc=started,
        finished_at_utc=finished,
        output_relpath=_rel_to_run(run_dir, out),
    )
