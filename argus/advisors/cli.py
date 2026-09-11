"""CLI: ``argus advisors``."""

from __future__ import annotations

import logging
import sys
from typing import Any

from argus.advisors.consensus import run_consensus, run_consultation
from argus.advisors.registry import global_advisors, resolve_advisors
from argus.advisors.runner import run_advisors
from argus.capabilities.requests.integrations import record_advisor_llm_gap
from argus.cli.repo import repo_root
from argus.core.serialize import dumps_json, to_jsonable
from argus.products.inventory import build_inventory

logger = logging.getLogger(__name__)


def run_advisors_command(args: Any) -> int:
    r = repo_root()
    sub = args.advisors_command

    if sub == "list":
        rows = global_advisors()
        if getattr(args, "list_product_id", None):
            rows = resolve_advisors(r, args.list_product_id)
        if args.json:
            print(dumps_json([to_jsonable(a) for a in rows]))
        else:
            for a in rows:
                print(f"{a.id}\t{a.archetype.value}\tweight={a.weight}")
                print(f"  {a.description}")
            if getattr(args, "list_product_id", None):
                print(f"(resolved for product {args.list_product_id})", file=sys.stderr)
        return 0

    if sub == "run":
        pid = args.product_id
        inv = build_inventory(r)
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        result = run_advisors(r, pid)
        if args.json:
            print(dumps_json(to_jsonable(result)))
        else:
            print(f"product_id: {result.product_id}")
            print(f"context: {result.context_summary}")
            if result.temporal_grounding is not None:
                tg = result.temporal_grounding
                print(
                    f"temporal: {tg.one_line_summary()} "
                    f"(consultation_as_of={tg.consultation_as_of_utc[:19]}Z)"
                )
            for resp in result.responses:
                print(f"\n[{resp.advisor_id} / {resp.archetype.value}]")
                print(f"  recommendation: {resp.recommendation}")
                if resp.risks:
                    print(f"  risks: {'; '.join(resp.risks[:6])}")
                else:
                    print(f"  risk: {resp.risk_assessment}")
                if resp.confidence is not None:
                    print(f"  confidence: {resp.confidence:.3f}")
                if resp.freshness_risk:
                    print(f"  freshness_risk: {resp.freshness_risk}")
                if resp.temporal_assumptions:
                    print(f"  temporal_assumptions: {'; '.join(resp.temporal_assumptions[:3])}")
                te = (resp.metadata or {}).get("temporal_summary_line")
                if te:
                    print(f"  temporal_evidence: {te}")
        if result.consultation_log_dir:
            print(f"Consultation log: {result.consultation_log_dir}", file=sys.stderr)
        try:
            gap = record_advisor_llm_gap(r, pid, stub_only=False, use_llm=None)
            if gap is not None and not args.json:
                print(
                    f"Recorded capability request {gap.request_id} (configure LLM for non-stub advisors)",
                    file=sys.stderr,
                )
        except OSError as e:
            logger.warning("Could not record advisor LLM capability gap: %s", e)
        return 0

    if sub == "consult":
        pid = args.product_id
        inv = build_inventory(r)
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        if getattr(args, "stub_only", False):
            use_llm = False
        elif getattr(args, "llm", False):
            use_llm = True
        else:
            use_llm = None
        run, cons = run_consultation(r, pid, use_llm=use_llm)
        out_dir = r / "runs" / "advisors"
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / f"{pid}.latest.json"
        payload = {
            "run": to_jsonable(run),
            "consensus": to_jsonable(cons),
        }
        artifact.write_text(dumps_json(payload), encoding="utf-8")
        try:
            gap = record_advisor_llm_gap(
                r,
                pid,
                stub_only=bool(getattr(args, "stub_only", False)),
                use_llm=True if getattr(args, "llm", False) else None,
            )
            if gap is not None and not args.json:
                print(
                    f"Recorded capability request {gap.request_id} (configure LLM for non-stub advisors)",
                    file=sys.stderr,
                )
        except OSError as e:
            logger.warning("Could not record advisor LLM capability gap: %s", e)
        if args.json:
            print(dumps_json(payload))
        else:
            print(f"product_id: {cons.product_id}")
            print(f"final_recommendation: {cons.final_recommendation}")
            print(f"consensus_decision: {cons.consensus_decision}")
            print(f"confidence_score: {cons.confidence_score}")
            if cons.temporal_evidence_summary:
                print(f"temporal_evidence: {cons.temporal_evidence_summary}")
            if cons.freshness_adjustment_applied:
                print(f"freshness_adjustment_applied: {cons.freshness_adjustment_applied:.3f}")
            print(f"disagreement_summary: {cons.disagreement_summary}")
            if cons.disagreement_signals:
                print("disagreement_signals:")
                for d in cons.disagreement_signals:
                    print(f"  - {d}")
            if run.consultation_log_dir:
                print(f"Consultation log: {run.consultation_log_dir}", file=sys.stderr)
            print(f"Wrote {artifact.relative_to(r)}", file=sys.stderr)
        return 0

    if sub == "consensus":
        pid = args.product_id
        inv = build_inventory(r)
        if pid not in inv.valid:
            print(f"Unknown or invalid product: {pid!r}", file=sys.stderr)
            return 1
        run, cons = run_consensus(r, pid)
        out_dir = r / "runs" / "advisors"
        out_dir.mkdir(parents=True, exist_ok=True)
        artifact = out_dir / f"{pid}.latest.json"
        payload = {
            "run": to_jsonable(run),
            "consensus": to_jsonable(cons),
        }
        artifact.write_text(dumps_json(payload), encoding="utf-8")
        try:
            gap = record_advisor_llm_gap(r, pid, stub_only=False, use_llm=None)
            if gap is not None and not args.json:
                print(
                    f"Recorded capability request {gap.request_id} (configure LLM for non-stub advisors)",
                    file=sys.stderr,
                )
        except OSError as e:
            logger.warning("Could not record advisor LLM capability gap: %s", e)
        if args.json:
            print(dumps_json(payload))
        else:
            print(f"final_recommendation: {cons.final_recommendation}")
            print(f"consensus_decision: {cons.consensus_decision}")
            print(f"confidence_score: {cons.confidence_score}")
            if cons.temporal_evidence_summary:
                print(f"temporal_evidence: {cons.temporal_evidence_summary}")
            if cons.freshness_adjustment_applied:
                print(f"freshness_adjustment_applied: {cons.freshness_adjustment_applied:.3f}")
            print(f"disagreement_summary: {cons.disagreement_summary}")
            print(f"disagreement_signals: {len(cons.disagreement_signals)}")
            for d in cons.disagreement_signals:
                print(f"  - {d}")
            if run.consultation_log_dir:
                print(f"Consultation log: {run.consultation_log_dir}", file=sys.stderr)
            print(f"Wrote {artifact.relative_to(r)}", file=sys.stderr)
        return 0

    print("Unknown advisors subcommand.", file=sys.stderr)
    return 2
