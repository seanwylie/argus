"""
Portfolio quiescence — whether another operator-queue / progression pass is likely warranted.

Compares current artifacts to a **prior baseline** stored in the last quiescence output, and optionally
to the two most recent **timestamped** portfolio progression runs for stuck/repeat detection.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.orchestrator.operator_snapshot import (
    OPERATOR_SNAPSHOT_SCHEMA,
    operator_snapshot_json_path,
)
from argus.orchestrator.state_models import (
    ORCH_STATUS_BLOCKED_WAITING_APPROVAL,
    ORCH_STATUS_BLOCKED_WAITING_INPUT,
)
from argus.policy.operator_policy import default_operator_policy, load_operator_policy
from argus.portfolio.artifact_index import list_timestamped_portfolio_json_files
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    load_operator_view,
    operator_queue_output_dir,
)
from argus.portfolio.progression import PORTFOLIO_PROGRESSION_SCHEMA, portfolio_progression_dir

PORTFOLIO_QUIESCENCE_SCHEMA = "argus.portfolio_quiescence.v1"

# --- Material-change thresholds (import-time defaults; runtime uses :func:`load_operator_policy`) ---
_q0 = default_operator_policy()["quiescence"]
DEBT_DELTA_MATERIAL = float(_q0["debt_delta_material"])
CONFIDENCE_DELTA_MATERIAL = float(_q0["confidence_delta_material"])
RANK_SHIFT_MATERIAL = int(_q0["rank_shift_material"])
SCORE_DELTA_MATERIAL = float(_q0["score_delta_material"])

_BLOCKED_OUTCOMES = frozenset({"blocked_waiting", "blocked_approval"})


def portfolio_quiescence_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "quiescence"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _ts_progression_files(repo_root: Path) -> list[Path]:
    return list_timestamped_portfolio_json_files(portfolio_progression_dir(repo_root))


def _load_operator_queue(repo_root: Path) -> dict[str, Any] | None:
    p = operator_queue_output_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == OPERATOR_QUEUE_SCHEMA:
        return raw
    return None


def _load_latest_progression(repo_root: Path) -> dict[str, Any] | None:
    p = portfolio_progression_dir(repo_root) / "latest.json"
    raw = _load_json(p)
    if raw is not None and raw.get("schema") == PORTFOLIO_PROGRESSION_SCHEMA:
        return raw
    return None


def _snapshot_fingerprint(snap: dict[str, Any]) -> dict[str, Any]:
    rd = snap.get("readiness") if isinstance(snap.get("readiness"), dict) else {}
    wb = snap.get("waiting_and_blocking") if isinstance(snap.get("waiting_and_blocking"), dict) else {}
    ds = snap.get("decision_summary") if isinstance(snap.get("decision_summary"), dict) else {}
    ih = snap.get("import_health") if isinstance(snap.get("import_health"), dict) else {}
    wi = wb.get("waiting_inputs") if isinstance(wb.get("waiting_inputs"), list) else []
    bl = wb.get("blockers") if isinstance(wb.get("blockers"), list) else []
    debt = rd.get("understanding_debt")
    try:
        debt_f = round(float(debt), 4) if debt is not None else None
    except (TypeError, ValueError):
        debt_f = None
    tc = ds.get("top_decision_confidence")
    try:
        tc_f = float(tc) if tc is not None else None
    except (TypeError, ValueError):
        tc_f = None
    return {
        "readiness_tier": rd.get("readiness_tier"),
        "understanding_debt": debt_f,
        "next_action": snap.get("next_action"),
        "orchestration_status": wb.get("orchestration_status") or snap.get("orchestration_status"),
        "waiting_inputs_count": len(wi),
        "blockers_count": len(bl),
        "top_decision_confidence": tc_f,
        "first_pass_status": ih.get("first_pass_status"),
        "gating_tier": ih.get("gating_tier"),
    }


def _merge_queue_and_snapshot(
    entry: dict[str, Any],
    snap_fp: dict[str, Any] | None,
) -> dict[str, Any]:
    base = dict(snap_fp) if snap_fp else {}
    pid = str(entry.get("product_id") or "")
    base.setdefault("queue_rank", entry.get("queue_rank"))
    base.setdefault("priority_score", entry.get("priority_score"))
    base.setdefault("readiness_tier", entry.get("readiness_tier"))
    base.setdefault("next_action", entry.get("next_action"))
    base.setdefault("orchestration_status", entry.get("orchestration_status"))
    base["product_id"] = pid
    return base


def _build_current_fingerprints(
    repo_root: Path,
    queue_entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    root = repo_root.resolve()
    out: dict[str, dict[str, Any]] = {}
    for e in queue_entries:
        if not isinstance(e, dict):
            continue
        pid = str(e.get("product_id") or "").strip()
        if not pid:
            continue
        snap_fp: dict[str, Any] | None = None
        fresh_view, _src = load_operator_view(root, pid)
        if fresh_view is not None and fresh_view.get("schema") == OPERATOR_SNAPSHOT_SCHEMA:
            snap_fp = _snapshot_fingerprint(fresh_view)
        out[pid] = _merge_queue_and_snapshot(e, snap_fp)
    return out


def _material_diff(
    prior: dict[str, Any],
    cur: dict[str, Any],
    *,
    thresholds: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    t = thresholds or {
        "debt_delta_material": DEBT_DELTA_MATERIAL,
        "confidence_delta_material": CONFIDENCE_DELTA_MATERIAL,
        "rank_shift_material": RANK_SHIFT_MATERIAL,
        "score_delta_material": SCORE_DELTA_MATERIAL,
    }
    ddm = float(t["debt_delta_material"])
    cdm = float(t["confidence_delta_material"])
    rsm = int(t["rank_shift_material"])
    sdm = float(t["score_delta_material"])
    codes: list[str] = []
    keys = (
        "readiness_tier",
        "next_action",
        "orchestration_status",
        "first_pass_status",
        "gating_tier",
    )
    for k in keys:
        if prior.get(k) != cur.get(k):
            codes.append(f"quiescence.material_change.{k}")

    pw = prior.get("waiting_inputs_count")
    cw = cur.get("waiting_inputs_count")
    if pw != cw:
        codes.append("quiescence.material_change.waiting_inputs_count")

    pb = prior.get("blockers_count")
    cb = cur.get("blockers_count")
    if pb != cb:
        codes.append("quiescence.material_change.blockers_count")

    pd = prior.get("understanding_debt")
    cd = cur.get("understanding_debt")
    if pd is not None and cd is not None:
        try:
            if abs(float(pd) - float(cd)) >= ddm:
                codes.append("quiescence.material_change.understanding_debt_delta")
        except (TypeError, ValueError):
            pass
    elif pd != cd:
        codes.append("quiescence.material_change.understanding_debt")

    pconf = prior.get("top_decision_confidence")
    cconf = cur.get("top_decision_confidence")
    if pconf is not None and cconf is not None:
        try:
            if abs(float(pconf) - float(cconf)) >= cdm:
                codes.append("quiescence.material_change.decision_confidence_delta")
        except (TypeError, ValueError):
            pass
    elif pconf != cconf:
        codes.append("quiescence.material_change.decision_confidence")

    pr = prior.get("queue_rank")
    cr = cur.get("queue_rank")
    try:
        if pr is not None and cr is not None and abs(int(cr) - int(pr)) >= rsm:
            codes.append("quiescence.material_change.queue_rank_shift")
    except (TypeError, ValueError):
        pass

    ps = prior.get("priority_score")
    cs = cur.get("priority_score")
    try:
        if ps is not None and cs is not None and abs(float(cs) - float(ps)) >= sdm:
            codes.append("quiescence.material_change.priority_score_shift")
    except (TypeError, ValueError):
        pass

    return (len(codes) > 0, codes)


def _is_blocked_status(st: str | None) -> bool:
    s = str(st or "").strip().lower()
    return s in (
        ORCH_STATUS_BLOCKED_WAITING_INPUT.lower(),
        ORCH_STATUS_BLOCKED_WAITING_APPROVAL.lower(),
        "blocked_waiting_input",
        "blocked_waiting_approval",
    ) or "blocked_waiting" in s


def _newly_actionable(prior: dict[str, Any], cur: dict[str, Any]) -> bool:
    p_na = str(prior.get("next_action") or "").strip().lower()
    c_na = str(cur.get("next_action") or "").strip().lower()
    was_blocked = _is_blocked_status(str(prior.get("orchestration_status")))
    now_ok = not _is_blocked_status(str(cur.get("orchestration_status")))
    gained_action = (p_na in ("", "none")) and c_na not in ("", "none")
    return (was_blocked and now_ok and c_na not in ("", "none")) or (was_blocked and gained_action)


def _still_blocked(prior: dict[str, Any], cur: dict[str, Any]) -> bool:
    return _is_blocked_status(str(prior.get("orchestration_status"))) and _is_blocked_status(
        str(cur.get("orchestration_status"))
    )


# --- Public helpers for :mod:`argus.portfolio.delta_report` (same fingerprints / thresholds) ---


def snapshot_fingerprint_for_delta(snap: dict[str, Any]) -> dict[str, Any]:
    """Stable subset of operator snapshot fields for comparing portfolio passes."""
    return _snapshot_fingerprint(snap)


def fingerprints_for_operator_queue_entries(
    repo_root: Path,
    entries: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Per-product fingerprints merged with queue row (same as quiescence baseline)."""
    return _build_current_fingerprints(repo_root, entries)


def material_change_codes(
    prior: dict[str, Any],
    cur: dict[str, Any],
    *,
    repo_root: Path | None = None,
    thresholds: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    """Whether ``cur`` differs materially from ``prior`` using quiescence thresholds."""
    th: dict[str, Any] | None
    if thresholds is not None:
        th = thresholds
    elif repo_root is not None:
        qm = load_operator_policy(repo_root)["quiescence"]
        th = {
            "debt_delta_material": qm["debt_delta_material"],
            "confidence_delta_material": qm["confidence_delta_material"],
            "rank_shift_material": qm["rank_shift_material"],
            "score_delta_material": qm["score_delta_material"],
        }
    else:
        th = None
    return _material_diff(prior, cur, thresholds=th)


def is_newly_actionable(prior: dict[str, Any], cur: dict[str, Any]) -> bool:
    return _newly_actionable(prior, cur)


def is_newly_blocked(prior: dict[str, Any], cur: dict[str, Any]) -> bool:
    ps = str(prior.get("orchestration_status") or "")
    cs = str(cur.get("orchestration_status") or "")
    return not _is_blocked_status(ps) and _is_blocked_status(cs)


def is_still_blocked_both(prior: dict[str, Any], cur: dict[str, Any]) -> bool:
    return _still_blocked(prior, cur)


def _progression_outcome_map(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in payload.get("products") or []:
        if not isinstance(row, dict):
            continue
        pid = str(row.get("product_id") or "").strip()
        oc = str(row.get("outcome") or "").strip()
        if pid:
            out[pid] = oc
    return out


def _stuck_or_repeating(repo_root: Path) -> list[dict[str, Any]]:
    """Same blocked outcome in two consecutive timestamped progression runs."""
    files = _ts_progression_files(repo_root)
    if len(files) < 2:
        return []
    cur = _load_json(files[0])
    prev = _load_json(files[1])
    if not cur or not prev:
        return []
    m_cur = _progression_outcome_map(cur)
    m_prev = _progression_outcome_map(prev)
    rows: list[dict[str, Any]] = []
    for pid in sorted(set(m_cur) & set(m_prev)):
        a, b = m_cur[pid], m_prev[pid]
        if a in _BLOCKED_OUTCOMES and b in _BLOCKED_OUTCOMES and a == b:
            rows.append(
                {
                    "product_id": pid,
                    "last_outcome": a,
                    "prior_run_id": prev.get("run_id"),
                    "current_run_id": cur.get("run_id"),
                }
            )
    return rows


def _queue_rank_changes(
    prior_order: list[str],
    current_fps: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rank changes derived from prior ``queue_product_order`` vs current ``queue_rank``."""
    prior_rank = {pid: i + 1 for i, pid in enumerate(prior_order)}
    rows: list[dict[str, Any]] = []
    for pid, fp in current_fps.items():
        cr = fp.get("queue_rank")
        try:
            cr_i = int(cr) if cr is not None else None
        except (TypeError, ValueError):
            cr_i = None
        pr_i = prior_rank.get(pid)
        if pr_i is None or cr_i is None:
            continue
        delta = cr_i - pr_i
        if delta != 0:
            rows.append(
                {
                    "product_id": pid,
                    "rank_before": pr_i,
                    "rank_after": cr_i,
                    "delta": delta,
                }
            )
    return sorted(rows, key=lambda x: (abs(x["delta"]), str(x["product_id"])), reverse=True)


def _recommendation(
    *,
    has_prior_baseline: bool,
    material_codes: list[str],
    newly_actionable: list[str],
    stuck: list[dict[str, Any]],
    import_codes: list[str],
) -> str:
    if not has_prior_baseline:
        return "run_again"
    if any("import" in c or "first_pass" in c or "gating" in c for c in material_codes) or import_codes:
        return "import_refresh"
    if stuck:
        return "human_review"
    if newly_actionable:
        return "run_again"
    if any("queue_rank" in c or "priority_score" in c for c in material_codes):
        return "run_again"
    if material_codes:
        return "run_again"
    return "wait"


def evaluate_portfolio_quiescence(
    repo_root: Path,
    *,
    operator_policy: dict[str, Any] | None = None,
    operator_queue_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    pol_for = operator_policy if operator_policy is not None else load_operator_policy(root)
    qm = pol_for["quiescence"]
    quiescence_thresholds = {
        "debt_delta_material": qm["debt_delta_material"],
        "confidence_delta_material": qm["confidence_delta_material"],
        "rank_shift_material": qm["rank_shift_material"],
        "score_delta_material": qm["score_delta_material"],
    }

    queue = operator_queue_payload if operator_queue_payload is not None else _load_operator_queue(root)
    progression_latest = _load_latest_progression(root)
    prior_quiescence = _load_json(portfolio_quiescence_dir(root) / "latest.json")

    baseline_prior = (
        prior_quiescence.get("baseline_for_next_run")
        if isinstance(prior_quiescence, dict)
        else None
    )
    has_prior = isinstance(baseline_prior, dict) and bool(baseline_prior.get("per_product"))

    entries = list(queue.get("entries") or []) if queue else []
    current_fps = _build_current_fingerprints(root, entries) if entries else {}

    prior_fps: dict[str, Any] = {}
    prior_order: list[str] = []
    if has_prior:
        prior_fps = baseline_prior.get("per_product") or {}
        if not isinstance(prior_fps, dict):
            prior_fps = {}
        prior_order = list(baseline_prior.get("queue_product_order") or [])
        if not isinstance(prior_order, list):
            prior_order = []

    products_material: list[dict[str, Any]] = []
    all_mc: list[str] = []
    import_codes: list[str] = []

    for pid, cur in sorted(current_fps.items()):
        prior = prior_fps.get(pid)
        if prior is None:
            all_mc.append("quiescence.material_change.new_product_in_queue")
            products_material.append({"product_id": pid, "codes": ["quiescence.material_change.new_product_in_queue"]})
            continue
        if not isinstance(prior, dict):
            continue
        changed, codes = _material_diff(prior, cur, thresholds=quiescence_thresholds)
        if changed:
            products_material.append({"product_id": pid, "codes": codes})
            all_mc.extend(codes)
            for c in codes:
                if "first_pass" in c or "gating" in c:
                    import_codes.append(c)

    newly: list[str] = []
    blocked_still: list[str] = []
    for pid in set(prior_fps) | set(current_fps):
        pr = prior_fps.get(pid)
        cu = current_fps.get(pid)
        if not isinstance(pr, dict) or not isinstance(cu, dict):
            continue
        if _newly_actionable(pr, cu):
            newly.append(pid)
        if _still_blocked(pr, cu):
            blocked_still.append(pid)

    stuck_rows = _stuck_or_repeating(root)

    rank_changes = _queue_rank_changes(prior_order, current_fps) if prior_order else []

    reason_codes: list[str] = []
    if not has_prior:
        reason_codes.append("quiescence.no_prior_baseline")
    reason_codes.extend(sorted(set(all_mc)))
    if stuck_rows:
        reason_codes.append("quiescence.progression_stuck_repeat")
    if not queue:
        reason_codes.append("quiescence.missing_operator_queue")

    if not queue:
        rec = "inspect" if has_prior else "run_again"
    else:
        rec = _recommendation(
            has_prior_baseline=bool(has_prior),
            material_codes=all_mc,
            newly_actionable=newly,
            stuck=stuck_rows,
            import_codes=import_codes,
        )

    missing_snaps = [
        pid
        for pid in current_fps
        if not operator_snapshot_json_path(root, pid).is_file()
    ]

    portfolio_quiescent = (
        has_prior
        and not products_material
        and not stuck_rows
        and rec == "wait"
        and queue is not None
    )
    if missing_snaps and has_prior:
        portfolio_quiescent = False
        reason_codes.append("quiescence.missing_operator_snapshots")
        if rec == "wait":
            rec = "inspect"

    out: dict[str, Any] = {
        "schema": PORTFOLIO_QUIESCENCE_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "portfolio_quiescent": portfolio_quiescent,
        "quiescence_reason_codes": sorted(set(reason_codes)),
        "recommendation": rec,
        "ranked_queue_slice": [
            {
                "product_id": e.get("product_id"),
                "queue_rank": e.get("queue_rank"),
                "priority_score": e.get("priority_score"),
                "readiness_tier": e.get("readiness_tier"),
                "next_action": e.get("next_action"),
            }
            for e in (entries[:24] if entries else [])
            if isinstance(e, dict)
        ],
        "inputs": {
            "operator_queue_present": queue is not None,
            "operator_queue_generated_at_utc": queue.get("generated_at_utc") if queue else None,
            "progression_latest_run_id": progression_latest.get("run_id") if progression_latest else None,
            "progression_latest_generated_at_utc": progression_latest.get("generated_at_utc")
            if progression_latest
            else None,
            "prior_quiescence_evaluated_at_utc": prior_quiescence.get("evaluated_at_utc")
            if isinstance(prior_quiescence, dict)
            else None,
        },
        "products_with_material_change": products_material,
        "products_newly_actionable": sorted(newly),
        "products_still_blocked": sorted(blocked_still),
        "products_stuck_or_repeating": stuck_rows,
        "queue_rank_changes": rank_changes,
        "products_missing_operator_snapshot": sorted(missing_snaps),
        "thresholds": {
            "DEBT_DELTA_MATERIAL": quiescence_thresholds["debt_delta_material"],
            "CONFIDENCE_DELTA_MATERIAL": quiescence_thresholds["confidence_delta_material"],
            "RANK_SHIFT_MATERIAL": quiescence_thresholds["rank_shift_material"],
            "SCORE_DELTA_MATERIAL": quiescence_thresholds["score_delta_material"],
        },
        "baseline_for_next_run": {
            "evaluated_at_utc": evaluated_at,
            "queue_generated_at_utc": (queue.get("generated_at_utc") if queue else None),
            "progression_run_id": progression_latest.get("run_id") if progression_latest else None,
            "per_product": current_fps,
            "queue_product_order": [
                str(e.get("product_id"))
                for e in entries
                if isinstance(e, dict) and e.get("product_id")
            ],
        },
    }
    return out


def render_portfolio_quiescence_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio quiescence",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        f"**Portfolio quiescent:** {payload.get('portfolio_quiescent')}",
        f"**Recommendation:** `{payload.get('recommendation')}`",
        "",
        "## Reason codes",
        "",
    ]
    for c in payload.get("quiescence_reason_codes") or []:
        lines.append(f"- `{c}`")
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- **Products with material change:** {len(payload.get('products_with_material_change') or [])}",
            f"- **Newly actionable:** {', '.join(payload.get('products_newly_actionable') or []) or '—'}",
            f"- **Still blocked:** {', '.join(payload.get('products_still_blocked') or []) or '—'}",
            f"- **Stuck / repeating (progression):** {len(payload.get('products_stuck_or_repeating') or [])}",
            "",
            "## Queue rank changes (vs prior baseline order)",
            "",
        ]
    )
    for r in payload.get("queue_rank_changes") or []:
        if isinstance(r, dict):
            lines.append(
                f"- `{r.get('product_id')}`: {r.get('rank_before')} → {r.get('rank_after')} (Δ{r.get('delta')})"
            )
    if not (payload.get("queue_rank_changes") or []):
        lines.append("—")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_portfolio_quiescence_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    d = portfolio_quiescence_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    pl = dict(payload)
    pl["run_id"] = rid
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_quiescence_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_quiescence(
    repo_root: Path,
    *,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = evaluate_portfolio_quiescence(repo_root)
    if write_artifacts:
        write_portfolio_quiescence_artifacts(repo_root, payload)
    return payload
