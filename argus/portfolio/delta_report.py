"""
Portfolio delta report — what changed across the last portfolio cycle vs a durable baseline.

Uses the same per-product fingerprints and material-change thresholds as
:mod:`argus.portfolio.quiescence` (loaded via :func:`argus.policy.operator_policy.load_operator_policy`).
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.policy.operator_policy import load_operator_policy
from argus.portfolio.artifact_index import list_timestamped_portfolio_json_files
from argus.portfolio.operator_queue import OPERATOR_QUEUE_SCHEMA, operator_queue_output_dir
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir
from argus.portfolio.quiescence import (
    PORTFOLIO_QUIESCENCE_SCHEMA,
    fingerprints_for_operator_queue_entries,
    is_newly_actionable,
    is_newly_blocked,
    is_still_blocked_both,
    material_change_codes,
    portfolio_quiescence_dir,
)

PORTFOLIO_DELTA_REPORT_SCHEMA = "argus.portfolio_delta_report.v1"

_TIER_RANK: dict[str, int] = {
    "unprofiled": 0,
    "import_incomplete": 1,
    "observe_gap": 2,
    "interpret_gap": 3,
    "advance_ready": 4,
}


def portfolio_delta_report_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "delta_report"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ts_json_files(d: Path) -> list[Path]:
    return list_timestamped_portfolio_json_files(d)


def _load_operator_queue(repo_root: Path) -> dict[str, Any] | None:
    p = operator_queue_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == OPERATOR_QUEUE_SCHEMA:
        return raw
    return None


def _load_progression_latest(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_progression_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == PORTFOLIO_PROGRESSION_SCHEMA:
        return raw
    return None


def _load_progression_prior_stamped(repo_root: Path) -> dict[str, Any] | None:
    files = _ts_json_files(portfolio_progression_dir(repo_root))
    if len(files) < 2:
        return None
    return _load_json(files[1])


def _load_quiescence_latest(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_quiescence_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == PORTFOLIO_QUIESCENCE_SCHEMA:
        return raw
    return None


def _load_delta_prior_baseline(repo_root: Path) -> tuple[dict[str, Any] | None, str | None, str]:
    """Return ``(per_product, baseline_evaluated_at, source_label)``."""
    p = portfolio_delta_report_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is None or raw.get("schema") != PORTFOLIO_DELTA_REPORT_SCHEMA:
        return None, None, "none"
    bl = raw.get("baseline_for_next_run")
    if not isinstance(bl, dict):
        return None, None, "none"
    pp = bl.get("per_product")
    if not isinstance(pp, dict) or not pp:
        return None, None, "none"
    ev = bl.get("evaluated_at_utc")
    return pp, str(ev) if ev else None, "delta_report_prior"


def _baseline_from_quiescence(repo_root: Path) -> tuple[dict[str, Any] | None, str | None, str]:
    q = _load_quiescence_latest(repo_root)
    if not q:
        return None, None, "none"
    bl = q.get("baseline_for_next_run")
    if not isinstance(bl, dict):
        return None, None, "none"
    pp = bl.get("per_product")
    if not isinstance(pp, dict) or not pp:
        return None, None, "none"
    ev = bl.get("evaluated_at_utc")
    return pp, str(ev) if ev else None, "quiescence_baseline"


def _tier_rank(tier: object) -> int:
    if tier is None:
        return -1
    return _TIER_RANK.get(str(tier).strip(), -1)


def _rank_change_row(
    pid: str,
    prior: dict[str, Any],
    cur: dict[str, Any],
    *,
    rank_shift_material: int,
) -> dict[str, Any] | None:
    pr = prior.get("queue_rank")
    cr = cur.get("queue_rank")
    try:
        pri = int(pr) if pr is not None else None
        cri = int(cr) if cr is not None else None
    except (TypeError, ValueError):
        return None
    if pri is None or cri is None:
        return None
    delta = cri - pri
    if delta == 0:
        return None
    return {
        "product_id": pid,
        "rank_before": pri,
        "rank_after": cri,
        "delta": delta,
        "material": abs(delta) >= rank_shift_material,
    }


def _confidence_pair(prior: dict[str, Any], cur: dict[str, Any]) -> tuple[float | None, float | None]:
    def _f(x: object) -> float | None:
        if x is None:
            return None
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    return _f(prior.get("top_decision_confidence")), _f(cur.get("top_decision_confidence"))


def _import_health_changed(prior: dict[str, Any], cur: dict[str, Any]) -> bool:
    for k in ("first_pass_status", "gating_tier"):
        if prior.get(k) != cur.get(k):
            return True
    return False


def evaluate_portfolio_delta_report(
    repo_root: Path,
    *,
    operator_policy: dict[str, Any] | None = None,
    operator_queue_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pol = operator_policy if operator_policy is not None else load_operator_policy(root)
    qm = pol["quiescence"]
    ddm = float(qm["debt_delta_material"])
    cdm = float(qm["confidence_delta_material"])
    rsm = int(qm["rank_shift_material"])
    sdm = float(qm["score_delta_material"])

    queue = operator_queue_payload if operator_queue_payload is not None else _load_operator_queue(root)
    entries = list(queue.get("entries") or []) if queue else []
    current_fps = fingerprints_for_operator_queue_entries(root, entries) if entries else {}

    prior_fps, baseline_eval_at, baseline_src = _load_delta_prior_baseline(root)
    if prior_fps is None:
        prior_fps, baseline_eval_at, baseline_src = _baseline_from_quiescence(root)

    has_baseline = prior_fps is not None and bool(prior_fps)
    prior_ids = set(prior_fps) if isinstance(prior_fps, dict) else set()
    cur_ids = set(current_fps.keys())
    new_in_queue = sorted(cur_ids - prior_ids)
    removed_from_queue = sorted(prior_ids - cur_ids) if has_baseline else []

    quiescence_latest = _load_quiescence_latest(root)

    prog_latest = _load_progression_latest(root)
    prog_prior = _load_progression_prior_stamped(root)

    advanced_now: list[str] = []
    if prog_latest:
        for row in prog_latest.get("products") or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("outcome") or "") == "advanced":
                pid = str(row.get("product_id") or "").strip()
                if pid:
                    advanced_now.append(pid)
    advanced_now = sorted(set(advanced_now))

    newly_actionable: list[str] = []
    newly_blocked: list[str] = []
    still_stuck: list[str] = []
    readiness_changed: list[dict[str, Any]] = []
    next_action_changed: list[dict[str, Any]] = []
    score_changed: list[dict[str, Any]] = []
    confidence_changed: list[dict[str, Any]] = []
    queue_movement: list[dict[str, Any]] = []
    import_health_changed: list[dict[str, Any]] = []
    material_rows: list[dict[str, Any]] = []
    delta_reason_codes: list[str] = []

    all_pids = sorted(prior_ids | cur_ids) if has_baseline else sorted(cur_ids)
    if not has_baseline:
        delta_reason_codes.append("delta_report.no_prior_baseline")

    if has_baseline:
        for pid in all_pids:
            cur = current_fps.get(pid)
            prior = prior_fps.get(pid) if isinstance(prior_fps, dict) else None
            if not isinstance(cur, dict):
                continue
            if not isinstance(prior, dict):
                material_rows.append({"product_id": pid, "note": "new_product_in_queue"})
                continue

            if is_newly_actionable(prior, cur):
                newly_actionable.append(pid)
            if is_newly_blocked(prior, cur):
                newly_blocked.append(pid)
            if is_still_blocked_both(prior, cur):
                still_stuck.append(pid)

            pt, ct = prior.get("readiness_tier"), cur.get("readiness_tier")
            if pt != ct:
                readiness_changed.append({"product_id": pid, "before": pt, "after": ct})

            pna, cna = prior.get("next_action"), cur.get("next_action")
            if pna != cna:
                next_action_changed.append({"product_id": pid, "before": pna, "after": cna})

            ps, cs = prior.get("priority_score"), cur.get("priority_score")
            try:
                psf = float(ps) if ps is not None else None
                csf = float(cs) if cs is not None else None
                if psf is not None and csf is not None and abs(csf - psf) >= sdm:
                    score_changed.append(
                        {
                            "product_id": pid,
                            "before": psf,
                            "after": csf,
                            "delta": round(csf - psf, 4),
                        }
                    )
            except (TypeError, ValueError):
                pass

            pconf, cconf = _confidence_pair(prior, cur)
            if pconf is not None and cconf is not None:
                if abs(cconf - pconf) >= cdm:
                    confidence_changed.append(
                        {
                            "product_id": pid,
                            "before": pconf,
                            "after": cconf,
                            "delta": round(cconf - pconf, 4),
                        }
                    )
            elif pconf != cconf:
                confidence_changed.append(
                    {"product_id": pid, "before": pconf, "after": cconf, "delta": None}
                )

            rc = _rank_change_row(pid, prior, cur, rank_shift_material=rsm)
            if rc:
                queue_movement.append(rc)

            if _import_health_changed(prior, cur):
                import_health_changed.append(
                    {
                        "product_id": pid,
                        "before": {
                            "first_pass_status": prior.get("first_pass_status"),
                            "gating_tier": prior.get("gating_tier"),
                        },
                        "after": {
                            "first_pass_status": cur.get("first_pass_status"),
                            "gating_tier": cur.get("gating_tier"),
                        },
                    }
                )

            changed, codes = material_change_codes(
                prior,
                cur,
                thresholds={
                    "debt_delta_material": ddm,
                    "confidence_delta_material": cdm,
                    "rank_shift_material": rsm,
                    "score_delta_material": sdm,
                },
            )
            if changed:
                material_rows.append({"product_id": pid, "material_change_codes": codes})

    # Portfolio-level progression headline (latest vs prior stamped file)
    prog_headline: dict[str, Any] = {
        "latest_run_id": prog_latest.get("run_id") if prog_latest else None,
        "latest_generated_at_utc": prog_latest.get("generated_at_utc") if prog_latest else None,
        "prior_stamped_run_id": prog_prior.get("run_id") if prog_prior else None,
        "prior_stamped_generated_at_utc": prog_prior.get("generated_at_utc") if prog_prior else None,
        "summary_counts_latest": prog_latest.get("summary_counts") if prog_latest else None,
    }

    # Improved / regressed / stuck (deterministic)
    improved: list[dict[str, Any]] = []
    regressed: list[dict[str, Any]] = []
    if has_baseline:
        for pid in all_pids:
            prior = prior_fps.get(pid) if isinstance(prior_fps, dict) else None
            cur = current_fps.get(pid)
            if not isinstance(prior, dict) or not isinstance(cur, dict):
                continue
            reasons_imp: list[str] = []
            reasons_reg: list[str] = []
            if is_newly_actionable(prior, cur):
                reasons_imp.append("newly_actionable")
            if is_newly_blocked(prior, cur):
                reasons_reg.append("newly_blocked")
            tr = _tier_rank(cur.get("readiness_tier")) - _tier_rank(prior.get("readiness_tier"))
            if tr > 0:
                reasons_imp.append("readiness_tier_improved")
            if tr < 0:
                reasons_reg.append("readiness_tier_regressed")
            pc, cc = _confidence_pair(prior, cur)
            if pc is not None and cc is not None and (cc - pc) >= cdm:
                reasons_imp.append("decision_confidence_up_materially")
            if pc is not None and cc is not None and (pc - cc) >= cdm:
                reasons_reg.append("decision_confidence_down_materially")
            pd, cd = prior.get("understanding_debt"), cur.get("understanding_debt")
            try:
                pdf = float(pd) if pd is not None else None
                cdf = float(cd) if cd is not None else None
                if pdf is not None and cdf is not None:
                    if pdf - cdf >= ddm:
                        reasons_imp.append("understanding_debt_down_materially")
                    if cdf - pdf >= ddm:
                        reasons_reg.append("understanding_debt_up_materially")
            except (TypeError, ValueError):
                pass
            if reasons_imp:
                improved.append({"product_id": pid, "reasons": sorted(set(reasons_imp))})
            if reasons_reg:
                regressed.append({"product_id": pid, "reasons": sorted(set(reasons_reg))})

    imp_ids = {str(d.get("product_id")) for d in improved if isinstance(d, dict)}
    reg_ids = {str(d.get("product_id")) for d in regressed if isinstance(d, dict)}
    stuck_only = sorted(pid for pid in still_stuck if pid not in imp_ids and pid not in reg_ids)

    rec = _recommend_next_action(
        has_baseline=has_baseline,
        newly_blocked=newly_blocked,
        newly_actionable=newly_actionable,
        import_health_changed=import_health_changed,
        regressed=len(regressed),
        improved=len(improved),
    )

    out: dict[str, Any] = {
        "schema": PORTFOLIO_DELTA_REPORT_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "baseline": {
            "source": baseline_src,
            "evaluated_at_utc": baseline_eval_at,
            "had_per_product": has_baseline,
        },
        "inputs": {
            "operator_queue_present": queue is not None,
            "operator_queue_generated_at_utc": queue.get("generated_at_utc") if queue else None,
            "quiescence_latest_evaluated_at_utc": quiescence_latest.get("evaluated_at_utc")
            if quiescence_latest
            else None,
        },
        "delta_reason_codes": sorted(set(delta_reason_codes)),
        "portfolio_progression": prog_headline,
        "thresholds": {
            "DEBT_DELTA_MATERIAL": ddm,
            "CONFIDENCE_DELTA_MATERIAL": cdm,
            "RANK_SHIFT_MATERIAL": rsm,
            "SCORE_DELTA_MATERIAL": sdm,
        },
        "portfolio_headline": {
            "products_in_current_queue": len(cur_ids),
            "products_compared": len(cur_ids & prior_ids) if has_baseline else 0,
            "products_new_in_queue": new_in_queue,
            "products_removed_from_queue": removed_from_queue,
        },
        "products_advanced_this_pass": advanced_now,
        "products_newly_actionable": sorted(newly_actionable),
        "products_newly_blocked": sorted(newly_blocked),
        "readiness_tier_changed": readiness_changed,
        "next_action_changed": next_action_changed,
        "priority_score_changed_materially": score_changed,
        "decision_confidence_changed_materially": confidence_changed,
        "queue_movement": queue_movement,
        "import_health_changed": import_health_changed,
        "products_with_material_change": material_rows,
        "what_improved": sorted(improved, key=lambda x: str(x.get("product_id"))),
        "what_regressed": sorted(regressed, key=lambda x: str(x.get("product_id"))),
        "what_stayed_stuck": stuck_only,
        "recommended_next_portfolio_action": rec,
        "baseline_for_next_run": {
            "evaluated_at_utc": evaluated_at,
            "queue_generated_at_utc": queue.get("generated_at_utc") if queue else None,
            "per_product": current_fps,
            "queue_product_order": [
                str(e.get("product_id"))
                for e in entries
                if isinstance(e, dict) and e.get("product_id")
            ],
        },
    }
    return out


def _recommend_next_action(
    *,
    has_baseline: bool,
    newly_blocked: list[str],
    newly_actionable: list[str],
    import_health_changed: list[dict[str, Any]],
    regressed: int,
    improved: int,
) -> str:
    if not has_baseline:
        return "establish_baseline"
    if import_health_changed:
        return "inspect_import_health"
    if newly_blocked:
        return "review_blockers"
    if newly_actionable or improved > regressed:
        return "run_progression_or_queue"
    if regressed > improved:
        return "review_regressions"
    return "continue_monitoring"


def render_portfolio_delta_report_markdown(payload: dict[str, Any]) -> str:
    bl = payload.get("baseline") or {}
    ph = payload.get("portfolio_headline") or {}
    lines = [
        "# Portfolio delta report",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Headline",
        "",
        f"- **Baseline source:** `{bl.get('source')}` (had fingerprints: **{bl.get('had_per_product')}**)",
        f"- **Products in queue:** {ph.get('products_in_current_queue')}",
        f"- **New in queue:** {', '.join(ph.get('products_new_in_queue') or []) or '—'}",
        f"- **Removed since baseline:** {', '.join(ph.get('products_removed_from_queue') or []) or '—'}",
        f"- **Advanced this pass (progression):** {', '.join(payload.get('products_advanced_this_pass') or []) or '—'}",
        f"- **Recommended next portfolio action:** `{payload.get('recommended_next_portfolio_action')}`",
        "",
        "## What improved",
        "",
    ]
    for row in payload.get("what_improved") or []:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('product_id')}` — {', '.join(row.get('reasons') or [])}")
    if not (payload.get("what_improved") or []):
        lines.append("—")
    lines.extend(["", "## What regressed", ""])
    for row in payload.get("what_regressed") or []:
        if isinstance(row, dict):
            lines.append(f"- `{row.get('product_id')}` — {', '.join(row.get('reasons') or [])}")
    if not (payload.get("what_regressed") or []):
        lines.append("—")
    lines.extend(["", "## What stayed stuck", ""])
    stuck = payload.get("what_stayed_stuck") or []
    lines.append(", ".join(f"`{p}`" for p in stuck) if stuck else "—")
    lines.extend(["", "## Product table (concise)", "", "| Product | Δ tier | Δ next_action | Δ score | Δ confidence | Queue Δ |", "|---------|--------|---------------|---------|--------------|---------|"])
    tiers = {str(x.get("product_id")): x for x in (payload.get("readiness_tier_changed") or []) if isinstance(x, dict)}
    nas = {str(x.get("product_id")): x for x in (payload.get("next_action_changed") or []) if isinstance(x, dict)}
    scores = {str(x.get("product_id")): x for x in (payload.get("priority_score_changed_materially") or []) if isinstance(x, dict)}
    confs = {str(x.get("product_id")): x for x in (payload.get("decision_confidence_changed_materially") or []) if isinstance(x, dict)}
    qmov = {str(x.get("product_id")): x for x in (payload.get("queue_movement") or []) if isinstance(x, dict)}
    pids = sorted(
        set(tiers) | set(nas) | set(scores) | set(confs) | set(qmov),
        key=lambda x: x,
    )
    for pid in pids:
        t = tiers.get(pid, {})
        n = nas.get(pid, {})
        s = scores.get(pid, {})
        c = confs.get(pid, {})
        q = qmov.get(pid, {})
        tier_s = f"`{t.get('before')}`→`{t.get('after')}`" if t else "—"
        na_s = f"`{n.get('before')}`→`{n.get('after')}`" if n else "—"
        sc_s = str(s.get("delta")) if s else "—"
        cf_s = str(c.get("delta")) if c and c.get("delta") is not None else "—"
        q_s = str(q.get("delta")) if q else "—"
        lines.append(f"| `{pid}` | {tier_s} | {na_s} | {sc_s} | {cf_s} | {q_s} |")
    if not pids:
        lines.append("| — | — | — | — | — | — |")
    lines.extend(["", "## Recommended next portfolio action", "", f"`{payload.get('recommended_next_portfolio_action')}`", ""])
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_delta_report_artifacts(
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
    d = portfolio_delta_report_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_delta_report_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_delta_report(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_delta_report(repo_root)
    if write_artifacts:
        write_portfolio_delta_report_artifacts(repo_root, payload)
    return payload
