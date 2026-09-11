"""Per-action product-evidence watermark timestamps for orchestration execution-feedback deprioritization."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable

from argus.decision.persistence import latest_product_path as decisions_latest_path
from argus.experiments.store import list_experiments
from argus.findings.persistence import latest_path as findings_latest_path
from argus.orchestrator.artifact_paths import (
    execution_product_dir,
    experiments_prioritization_latest_product_path,
    experiments_proposals_latest_product_path,
    ideas_bundle_latest_path,
    runs_escalations_latest_dir,
)
from argus.orchestrator.artifact_snapshot import parse_iso_timestamp
from argus.orchestrator.state_models import (
    ACTION_AUDIT_RUN,
    ACTION_DECISIONS_GENERATE,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_ESCALATION_CONSIDER,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXECUTION_OUTCOMES_APPLY,
    ACTION_EXPERIMENTS_ACTIVATE,
    ACTION_EXPERIMENTS_CLOSE_STALE,
    ACTION_EXPERIMENTS_CREATE,
    ACTION_EXPERIMENTS_EVALUATE,
    ACTION_EXPERIMENTS_PRIORITIZE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS,
    ACTION_IMPLEMENTATION_PLAN_GENERATE,
    ACTION_ORCHESTRATION_STATE_REFRESH,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY,
    ACTION_REFINEMENT_RUN,
    ACTION_REFINEMENT_START_IDEA,
    ACTION_REFINEMENT_START_PRODUCT_SPEC,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION,
    ACTION_TEMPORAL_REFRESH,
)
from argus.planning.snapshot import planning_latest_path
from argus.strategy.snapshot import strategy_latest_path


def _max_utc(*candidates: datetime | None) -> datetime | None:
    xs = [x for x in candidates if x is not None]
    return max(xs) if xs else None


def watermark_signals_collect_or_temporal_refresh(ctx: Any) -> datetime | None:
    sig = ctx.sig
    temp = ctx.temp
    return _max_utc(
        parse_iso_timestamp(sig.collected_at_utc),
        parse_iso_timestamp(temp.collected_at_utc),
    )


def watermark_audit_run(ctx: Any) -> datetime | None:
    return parse_iso_timestamp(ctx.aud.generated_at_utc)


def watermark_orchestration_state_refresh(ctx: Any) -> datetime | None:
    sig = ctx.sig
    temp = ctx.temp
    aud = ctx.aud
    return _max_utc(
        parse_iso_timestamp(sig.collected_at_utc),
        parse_iso_timestamp(temp.collected_at_utc),
        parse_iso_timestamp(aud.generated_at_utc),
    )


def watermark_implementation_plan_generate(ctx: Any) -> datetime | None:
    ps = ctx.ps_sess
    ip = ctx.ip_sess
    return _max_utc(
        parse_iso_timestamp(ps.updated_at_utc) if ps else None,
        parse_iso_timestamp(ip.updated_at_utc) if ip else None,
    )


def watermark_refinement_start_product_spec(ctx: Any) -> datetime | None:
    ps = ctx.ps_sess
    return parse_iso_timestamp(ps.updated_at_utc) if ps else None


def watermark_refinement_run_or_submit_reviews_in(ctx: Any) -> datetime | None:
    sessions = ctx.sessions
    return _max_utc(*[parse_iso_timestamp(s.updated_at_utc) for s in sessions])


def watermark_escalation_consider(ctx: Any) -> datetime | None:
    sig = ctx.sig
    temp = ctx.temp
    aud = ctx.aud
    return _max_utc(
        parse_iso_timestamp(sig.collected_at_utc),
        parse_iso_timestamp(temp.collected_at_utc),
        parse_iso_timestamp(aud.generated_at_utc),
    )


def watermark_execution_outcomes_apply(ctx: Any) -> datetime | None:
    base = execution_product_dir(ctx.root, ctx.product_id)
    if not base.is_dir():
        return None
    mtimes: list[datetime] = []
    for path in base.glob("*.json"):
        if path.name.startswith("_"):
            continue
        try:
            mtimes.append(datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc))
        except OSError:
            continue
    return max(mtimes) if mtimes else None


def watermark_findings_generate(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw = None
        if isinstance(raw, dict):
            ts_fin = parse_iso_timestamp(str(raw.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin)


def watermark_decisions_generate(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec)


def watermark_decisions_refresh_from_surfaced_findings(ctx: Any) -> datetime | None:
    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ts_surf: datetime | None = None
    sp = experiment_surfaced_latest_path(ctx.root, ctx.product_id)
    if sp.is_file():
        try:
            raw_sf = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_sf = None
        if isinstance(raw_sf, dict):
            ts_surf = parse_iso_timestamp(str(raw_sf.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_surf)


def watermark_ideas_generate_or_refinement_start_idea(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas)


def watermark_ideas_refresh_from_surfaced_findings(ctx: Any) -> datetime | None:
    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    ts_surf: datetime | None = None
    sp = experiment_surfaced_latest_path(ctx.root, ctx.product_id)
    if sp.is_file():
        try:
            raw_sf = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_sf = None
        if isinstance(raw_sf, dict):
            ts_surf = parse_iso_timestamp(str(raw_sf.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_surf)


def watermark_strategy_refresh_from_decision_evolution(ctx: Any) -> datetime | None:
    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ts_surf: datetime | None = None
    sp = experiment_surfaced_latest_path(ctx.root, ctx.product_id)
    if sp.is_file():
        try:
            raw_sf = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_sf = None
        if isinstance(raw_sf, dict):
            ts_surf = parse_iso_timestamp(str(raw_sf.get("generated_at_utc") or ""))
    ts_strat: datetime | None = None
    stp = strategy_latest_path(ctx.root, ctx.product_id)
    if stp.is_file():
        try:
            raw_st = json.loads(stp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_st = None
        if isinstance(raw_st, dict):
            ts_strat = parse_iso_timestamp(str(raw_st.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_surf, ts_strat)


def watermark_planning_refresh_from_strategy(ctx: Any) -> datetime | None:
    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ts_surf: datetime | None = None
    sp = experiment_surfaced_latest_path(ctx.root, ctx.product_id)
    if sp.is_file():
        try:
            raw_sf = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_sf = None
        if isinstance(raw_sf, dict):
            ts_surf = parse_iso_timestamp(str(raw_sf.get("generated_at_utc") or ""))
    ts_strat: datetime | None = None
    stp = strategy_latest_path(ctx.root, ctx.product_id)
    if stp.is_file():
        try:
            raw_st = json.loads(stp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_st = None
        if isinstance(raw_st, dict):
            ts_strat = parse_iso_timestamp(str(raw_st.get("generated_at_utc") or ""))
    ts_plan: datetime | None = None
    plp = planning_latest_path(ctx.root, ctx.product_id)
    if plp.is_file():
        try:
            raw_pl = json.loads(plp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pl = None
        if isinstance(raw_pl, dict):
            ts_plan = parse_iso_timestamp(str(raw_pl.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_surf, ts_strat, ts_plan)


def watermark_experiments_propose(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop)


def watermark_experiments_prioritize(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    prip = experiments_prioritization_latest_product_path(ctx.root, ctx.product_id)
    ts_pri: datetime | None = None
    if prip.is_file():
        try:
            raw_pri = json.loads(prip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pri = None
        if isinstance(raw_pri, dict):
            ts_pri = parse_iso_timestamp(str(raw_pri.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop, ts_pri)


def watermark_experiments_create(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    prip = experiments_prioritization_latest_product_path(ctx.root, ctx.product_id)
    ts_pri: datetime | None = None
    if prip.is_file():
        try:
            raw_pri = json.loads(prip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pri = None
        if isinstance(raw_pri, dict):
            ts_pri = parse_iso_timestamp(str(raw_pri.get("generated_at_utc") or ""))
    ts_exp: datetime | None = None
    for e in list_experiments(ctx.root, product_id=ctx.product_id):
        t = parse_iso_timestamp(e.created_at)
        if t is not None:
            ts_exp = t if ts_exp is None else max(ts_exp, t)
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop, ts_pri, ts_exp)


def watermark_experiments_activate(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    prip = experiments_prioritization_latest_product_path(ctx.root, ctx.product_id)
    ts_pri: datetime | None = None
    if prip.is_file():
        try:
            raw_pri = json.loads(prip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pri = None
        if isinstance(raw_pri, dict):
            ts_pri = parse_iso_timestamp(str(raw_pri.get("generated_at_utc") or ""))
    ts_exp: datetime | None = None
    for e in list_experiments(ctx.root, product_id=ctx.product_id):
        t = parse_iso_timestamp(e.created_at)
        if t is not None:
            ts_exp = t if ts_exp is None else max(ts_exp, t)
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop, ts_pri, ts_exp)


def watermark_experiments_evaluate(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    prip = experiments_prioritization_latest_product_path(ctx.root, ctx.product_id)
    ts_pri: datetime | None = None
    if prip.is_file():
        try:
            raw_pri = json.loads(prip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pri = None
        if isinstance(raw_pri, dict):
            ts_pri = parse_iso_timestamp(str(raw_pri.get("generated_at_utc") or ""))
    ts_exp: datetime | None = None
    ts_ev: datetime | None = None
    for e in list_experiments(ctx.root, product_id=ctx.product_id):
        t = parse_iso_timestamp(e.created_at)
        if t is not None:
            ts_exp = t if ts_exp is None else max(ts_exp, t)
        if e.last_evaluation_at:
            t2 = parse_iso_timestamp(e.last_evaluation_at)
            if t2 is not None:
                ts_ev = t2 if ts_ev is None else max(ts_ev, t2)
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop, ts_pri, ts_exp, ts_ev)


def watermark_experiments_close_stale(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    prip = experiments_prioritization_latest_product_path(ctx.root, ctx.product_id)
    ts_pri: datetime | None = None
    if prip.is_file():
        try:
            raw_pri = json.loads(prip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pri = None
        if isinstance(raw_pri, dict):
            ts_pri = parse_iso_timestamp(str(raw_pri.get("generated_at_utc") or ""))
    ts_exp: datetime | None = None
    ts_ev: datetime | None = None
    for e in list_experiments(ctx.root, product_id=ctx.product_id):
        t = parse_iso_timestamp(e.created_at)
        if t is not None:
            ts_exp = t if ts_exp is None else max(ts_exp, t)
        if e.last_evaluation_at:
            t2 = parse_iso_timestamp(e.last_evaluation_at)
            if t2 is not None:
                ts_ev = t2 if ts_ev is None else max(ts_ev, t2)
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop, ts_pri, ts_exp, ts_ev)


def watermark_experiments_surface_findings(ctx: Any) -> datetime | None:
    from argus.findings.experiment_surfaced import experiment_surfaced_latest_path

    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    ip = ideas_bundle_latest_path(ctx.root)
    ts_ideas: datetime | None = None
    if ip.is_file():
        try:
            raw_i = json.loads(ip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_i = None
        if isinstance(raw_i, dict) and str(raw_i.get("product_id") or "") == ctx.product_id:
            ts_ideas = parse_iso_timestamp(str(raw_i.get("generated_at_utc") or ""))
    prp = experiments_proposals_latest_product_path(ctx.root, ctx.product_id)
    ts_prop: datetime | None = None
    if prp.is_file():
        try:
            raw_pr = json.loads(prp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pr = None
        if isinstance(raw_pr, dict):
            ts_prop = parse_iso_timestamp(str(raw_pr.get("generated_at_utc") or ""))
    prip = experiments_prioritization_latest_product_path(ctx.root, ctx.product_id)
    ts_pri: datetime | None = None
    if prip.is_file():
        try:
            raw_pri = json.loads(prip.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_pri = None
        if isinstance(raw_pri, dict):
            ts_pri = parse_iso_timestamp(str(raw_pri.get("generated_at_utc") or ""))
    ts_exp: datetime | None = None
    ts_ev: datetime | None = None
    for e in list_experiments(ctx.root, product_id=ctx.product_id):
        t = parse_iso_timestamp(e.created_at)
        if t is not None:
            ts_exp = t if ts_exp is None else max(ts_exp, t)
        if e.last_evaluation_at:
            t2 = parse_iso_timestamp(e.last_evaluation_at)
            if t2 is not None:
                ts_ev = t2 if ts_ev is None else max(ts_ev, t2)
    ts_surf: datetime | None = None
    sp = experiment_surfaced_latest_path(ctx.root, ctx.product_id)
    if sp.is_file():
        try:
            raw_sf = json.loads(sp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_sf = None
        if isinstance(raw_sf, dict):
            ts_surf = parse_iso_timestamp(str(raw_sf.get("generated_at_utc") or ""))
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_ideas, ts_prop, ts_pri, ts_exp, ts_ev, ts_surf)


def watermark_escalation_packet_generate(ctx: Any) -> datetime | None:
    sig = ctx.sig
    ts_sig = parse_iso_timestamp(sig.collected_at_utc)
    fp = findings_latest_path(ctx.root, ctx.product_id)
    ts_fin: datetime | None = None
    if fp.is_file():
        try:
            raw_f = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_f = None
        if isinstance(raw_f, dict):
            ts_fin = parse_iso_timestamp(str(raw_f.get("generated_at_utc") or ""))
    dp = decisions_latest_path(ctx.root, ctx.product_id)
    ts_dec: datetime | None = None
    if dp.is_file():
        try:
            raw_d = json.loads(dp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            raw_d = None
        if isinstance(raw_d, dict):
            ts_dec = parse_iso_timestamp(str(raw_d.get("generated_at_utc") or ""))
    lat = runs_escalations_latest_dir(ctx.root)
    ts_pkt: datetime | None = None
    if lat.is_dir():
        for p in sorted(lat.glob("esc_*.json")):
            try:
                raw_p = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                continue
            if not isinstance(raw_p, dict):
                continue
            if str(raw_p.get("product_id") or "") != ctx.product_id:
                continue
            t = parse_iso_timestamp(str(raw_p.get("created_at") or ""))
            if t is not None:
                ts_pkt = t if ts_pkt is None else max(ts_pkt, t)
    return _max_utc(ts_sig, ts_fin, ts_dec, ts_pkt)


WATERMARK_FOR_ACTION_ID: dict[str, Callable[[Any], datetime | None]] = {
    ACTION_SIGNALS_COLLECT: watermark_signals_collect_or_temporal_refresh,
    ACTION_TEMPORAL_REFRESH: watermark_signals_collect_or_temporal_refresh,
    ACTION_AUDIT_RUN: watermark_audit_run,
    ACTION_ORCHESTRATION_STATE_REFRESH: watermark_orchestration_state_refresh,
    ACTION_IMPLEMENTATION_PLAN_GENERATE: watermark_implementation_plan_generate,
    ACTION_REFINEMENT_START_PRODUCT_SPEC: watermark_refinement_start_product_spec,
    ACTION_REFINEMENT_RUN: watermark_refinement_run_or_submit_reviews_in,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN: watermark_refinement_run_or_submit_reviews_in,
    ACTION_ESCALATION_CONSIDER: watermark_escalation_consider,
    ACTION_EXECUTION_OUTCOMES_APPLY: watermark_execution_outcomes_apply,
    ACTION_FINDINGS_GENERATE: watermark_findings_generate,
    ACTION_DECISIONS_GENERATE: watermark_decisions_generate,
    ACTION_DECISIONS_REFRESH_FROM_SURFACED_FINDINGS: watermark_decisions_refresh_from_surfaced_findings,
    ACTION_IDEAS_GENERATE: watermark_ideas_generate_or_refinement_start_idea,
    ACTION_REFINEMENT_START_IDEA: watermark_ideas_generate_or_refinement_start_idea,
    ACTION_IDEAS_REFRESH_FROM_SURFACED_FINDINGS: watermark_ideas_refresh_from_surfaced_findings,
    ACTION_STRATEGY_REFRESH_FROM_DECISION_EVOLUTION: watermark_strategy_refresh_from_decision_evolution,
    ACTION_PLANNING_REFRESH_FROM_STRATEGY: watermark_planning_refresh_from_strategy,
    ACTION_EXPERIMENTS_PROPOSE: watermark_experiments_propose,
    ACTION_EXPERIMENTS_PRIORITIZE: watermark_experiments_prioritize,
    ACTION_EXPERIMENTS_CREATE: watermark_experiments_create,
    ACTION_EXPERIMENTS_ACTIVATE: watermark_experiments_activate,
    ACTION_EXPERIMENTS_EVALUATE: watermark_experiments_evaluate,
    ACTION_EXPERIMENTS_CLOSE_STALE: watermark_experiments_close_stale,
    ACTION_EXPERIMENTS_SURFACE_FINDINGS: watermark_experiments_surface_findings,
    ACTION_ESCALATION_PACKET_GENERATE: watermark_escalation_packet_generate,
}


def relevant_product_evidence_watermark_utc(action_id: str, ctx: Any) -> datetime | None:
    """
    Latest timestamp among existing artifacts that can change preconditions for ``action_id``.

    Used only to decide whether a **failed** feedback deprioritization should be skipped
    (newer evidence than the failure time). Narrow per-action mapping — unrelated artifacts
    must not clear another action's failure posture.
    """
    fn = WATERMARK_FOR_ACTION_ID.get(action_id)
    return fn(ctx) if fn is not None else None
