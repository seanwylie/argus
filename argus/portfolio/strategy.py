"""
Portfolio strategy — deterministic whole-portfolio posture from operator queue, outcomes,
patterns, intervention inbox, policy effectiveness, delta materiality, and optional creation proposals.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import AbstractSet, Any

from argus.core.serialize import dumps_json
from argus.policy.effectiveness import (
    OPERATOR_POLICY_EFFECTIVENESS_SCHEMA,
    evaluate_operator_policy_effectiveness,
)
from argus.portfolio.delta_report import PORTFOLIO_DELTA_REPORT_SCHEMA, portfolio_delta_report_dir
from argus.portfolio.intervention import PORTFOLIO_INTERVENTION_SCHEMA, portfolio_intervention_dir
from argus.portfolio.intervention_actions import intervention_inbox_dir
from argus.portfolio.intervention_inbox import (
    INTERVENTION_INBOX_SCHEMA,
    build_intervention_inbox_payload,
)
from argus.portfolio.operator_queue import (
    OPERATOR_QUEUE_SCHEMA,
    build_operator_queue_payload,
    operator_queue_output_dir,
)
from argus.portfolio.outcomes import (
    PORTFOLIO_OUTCOMES_SCHEMA,
    evaluate_portfolio_outcomes,
    portfolio_outcomes_dir,
)
from argus.portfolio.patterns import (
    PORTFOLIO_PATTERNS_SCHEMA,
    evaluate_portfolio_patterns,
    portfolio_patterns_dir,
)
from argus.products.creation import PRODUCT_CREATION_PROPOSALS_SCHEMA, creation_proposals_dir
from argus.products.inventory import build_inventory

PORTFOLIO_STRATEGY_SCHEMA = "argus.portfolio_strategy.v1"

# Deterministic posture resolution: higher index in tuple wins ties (earlier = higher priority).
POSTURE_TIE_ORDER: tuple[str, ...] = (
    "repair",
    "retire",
    "consolidate",
    "create",
    "expand",
    "harvest",
)

STRATEGIC_POSTURES: frozenset[str] = frozenset(POSTURE_TIE_ORDER)


def portfolio_strategy_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "strategy"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_canonical_portfolio_outcomes_latest(root: Path) -> dict[str, Any] | None:
    """
    Read ``runs/portfolio/outcomes/latest.json`` when schema matches.

    Same behavior as :func:`argus.portfolio.outcomes.load_canonical_portfolio_outcomes`.
    Kept in this module so ``strategy`` does not depend on importing that name from
    ``outcomes`` at load time (avoids circular-import / partial-init failures when the
    dashboard imports ``operator_summary`` → ``artifact_coherence`` → ``strategy``).
    """
    raw = _load_json(portfolio_outcomes_dir(Path(root).resolve()) / "latest.json")
    if raw and str(raw.get("schema") or "") == PORTFOLIO_OUTCOMES_SCHEMA:
        return raw
    return None


def _safe_float(x: Any) -> float | None:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _n_ratio(num: int, denom: int) -> float:
    d = max(1, int(denom))
    return float(num) / float(d)


def _avg_metric_from_policy(
    policy: dict[str, Any] | None, key: str
) -> float | None:
    if not policy:
        return None
    cur = policy.get("current") or {}
    bo = cur.get("by_objective") or {}
    vals: list[float] = []
    for v in bo.values():
        if not isinstance(v, dict):
            continue
        f = _safe_float(v.get(key))
        if f is not None:
            vals.append(f)
    if not vals:
        return None
    return sum(vals) / len(vals)


def _delta_new_product_signal(delta_report: dict[str, Any] | None) -> bool:
    if not delta_report:
        return False
    if str(delta_report.get("schema") or "") != PORTFOLIO_DELTA_REPORT_SCHEMA:
        return False
    for row in delta_report.get("products_with_material_change") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("note") or "") == "new_product_in_queue":
            return True
    return False


def collect_strategy_signals(
    *,
    outcomes: dict[str, Any] | None,
    patterns: dict[str, Any] | None,
    operator_queue: dict[str, Any] | None,
    intervention_inbox: dict[str, Any] | None,
    policy_effectiveness: dict[str, Any] | None,
    delta_report: dict[str, Any] | None,
    creation_proposals: dict[str, Any] | None,
    inventory_valid_product_count: int | None = None,
) -> dict[str, Any]:
    """Normalize inputs into a compact signal dict for :func:`derive_portfolio_strategy_fields`."""
    summ = (outcomes or {}).get("portfolio_outcome_summary") or {}
    mas = (outcomes or {}).get("mission_alignment_summary") or {}
    per = [p for p in ((outcomes or {}).get("per_product_outcomes") or []) if isinstance(p, dict)]

    products_n = int(summ.get("products_evaluated") or len(per) or 0)
    positive_count = int(summ.get("positive_count") or 0)
    negative_count = int(summ.get("negative_count") or 0)
    mixed_count = int(summ.get("mixed_count") or 0)
    flat_count = int(summ.get("no_meaningful_movement_count") or 0)

    mission_align_pos = int(mas.get("positive") or 0)
    mission_align_neg = int(mas.get("negative") or 0)
    mission_align_neutral = int(mas.get("neutral") or 0)

    pmp = (outcomes or {}).get("portfolio_mission_provenance") or {}
    mix = pmp.get("mission_mix_summary") or {}
    counts_by_mid = mix.get("counts_by_mission_id") or {}
    distinct_mission_ids = len(
        [k for k in (mix.get("distinct_mission_ids") or []) if str(k).strip()]
    )
    if not distinct_mission_ids and isinstance(counts_by_mid, dict):
        distinct_mission_ids = len([k for k in counts_by_mid if str(k).strip()])

    blocked_stress = sum(
        1
        for p in per
        if str(p.get("blocked_pattern") or "") in ("persisted", "newly_blocked")
    )
    intervention_stress = sum(
        1
        for p in per
        if str(p.get("intervention_pattern") or "") in ("repeated", "newly_flagged")
    )

    dpat = [p for p in ((patterns or {}).get("detected_patterns") or []) if isinstance(p, dict)]
    high_sev = sum(1 for p in dpat if str(p.get("severity") or "") == "high")
    med_sev = sum(1 for p in dpat if str(p.get("severity") or "") == "medium")
    pattern_ids = sorted({str(p.get("pattern_id") or "") for p in dpat if p.get("pattern_id")})

    entries = [e for e in ((operator_queue or {}).get("entries") or []) if isinstance(e, dict)]
    advance_ready_count = 0
    debts: list[float] = []
    for e in entries:
        tier = str(e.get("readiness_tier") or "").lower()
        if "advance" in tier:
            advance_ready_count += 1
        d = _safe_float(e.get("understanding_debt"))
        if d is not None:
            debts.append(d)

    avg_debt = sum(debts) / len(debts) if debts else None

    inbox_ok = intervention_inbox and str(intervention_inbox.get("schema") or "") == INTERVENTION_INBOX_SCHEMA
    open_items = (
        [x for x in ((intervention_inbox or {}).get("open_items") or []) if isinstance(x, dict)]
        if inbox_ok
        else []
    )
    open_inbox_count = len(open_items)
    active_queue_inbox_count = sum(1 for x in open_items if x.get("in_active_queue"))
    high_severity_inbox_count = sum(
        1 for x in open_items if str(x.get("severity") or "") == "high"
    )

    pol_ok = policy_effectiveness and str(policy_effectiveness.get("schema") or "") == OPERATOR_POLICY_EFFECTIVENESS_SCHEMA
    notable = (policy_effectiveness or {}).get("notable_patterns") or [] if pol_ok else []
    policy_notable_count = len(notable) if isinstance(notable, list) else 0
    intervention_strain_rate = _avg_metric_from_policy(
        policy_effectiveness if pol_ok else None, "intervention_strain_rate"
    )
    stagnation_rate_mean = _avg_metric_from_policy(
        policy_effectiveness if pol_ok else None, "stagnation_rate"
    )

    new_product_in_queue = _delta_new_product_signal(delta_report)

    cp = creation_proposals
    cp_ok = cp and str(cp.get("schema") or "") == PRODUCT_CREATION_PROPOSALS_SCHEMA
    creation_proposal_count = int(cp.get("proposal_count") or 0) if cp_ok else 0

    inv_n: int | None
    if inventory_valid_product_count is None:
        inv_n = None
    else:
        inv_n = max(0, int(inventory_valid_product_count))

    return {
        "products_n": products_n,
        "positive_count": positive_count,
        "negative_count": negative_count,
        "mixed_count": mixed_count,
        "flat_count": flat_count,
        "mission_align_pos": mission_align_pos,
        "mission_align_neg": mission_align_neg,
        "mission_align_neutral": mission_align_neutral,
        "distinct_mission_ids": max(0, int(distinct_mission_ids)),
        "counts_by_mission_id": dict(counts_by_mid) if isinstance(counts_by_mid, dict) else {},
        "blocked_stress_count": blocked_stress,
        "intervention_stress_count": intervention_stress,
        "high_severity_patterns": high_sev,
        "medium_severity_patterns": med_sev,
        "pattern_ids": pattern_ids,
        "advance_ready_count": advance_ready_count,
        "avg_understanding_debt": avg_debt,
        "open_inbox_count": open_inbox_count,
        "active_queue_inbox_count": active_queue_inbox_count,
        "high_severity_inbox_count": high_severity_inbox_count,
        "policy_notable_count": policy_notable_count,
        "intervention_strain_rate": intervention_strain_rate,
        "stagnation_rate_mean": stagnation_rate_mean,
        "new_product_in_queue": new_product_in_queue,
        "creation_proposal_count": creation_proposal_count,
        "queue_entry_count": len(entries),
        "inventory_valid_product_count": inv_n,
    }


def _score_postures(signals: dict[str, Any]) -> dict[str, float]:
    n = max(1, int(signals.get("products_n") or 1))
    pos = int(signals.get("positive_count") or 0)
    neg = int(signals.get("negative_count") or 0)
    mix = int(signals.get("mixed_count") or 0)
    flat = int(signals.get("flat_count") or 0)
    ma_p = int(signals.get("mission_align_pos") or 0)
    ma_n = int(signals.get("mission_align_neg") or 0)
    distinct = int(signals.get("distinct_mission_ids") or 0)
    high_pat = int(signals.get("high_severity_patterns") or 0)
    pat_ids: list[str] = list(signals.get("pattern_ids") or [])
    adv = int(signals.get("advance_ready_count") or 0)
    avg_debt = signals.get("avg_understanding_debt")
    open_ib = int(signals.get("open_inbox_count") or 0)
    hi_ib = int(signals.get("high_severity_inbox_count") or 0)
    pol_note = int(signals.get("policy_notable_count") or 0)
    int_strain = _safe_float(signals.get("intervention_strain_rate"))
    stag_mean = _safe_float(signals.get("stagnation_rate_mean"))
    new_p = bool(signals.get("new_product_in_queue"))
    prop_n = int(signals.get("creation_proposal_count") or 0)
    blocked_stress = int(signals.get("blocked_stress_count") or 0)
    int_stress = int(signals.get("intervention_stress_count") or 0)

    rp = _n_ratio(pos, n)
    rn = _n_ratio(neg, n)
    rmix = _n_ratio(mix, n)
    rflat = _n_ratio(flat, n)
    rma_p = _n_ratio(ma_p, n)
    rma_n = _n_ratio(ma_n, n)
    radv = _n_ratio(adv, n)
    high_pat_n = min(1.0, high_pat / max(1.0, float(n)))

    import_pat = sum(1 for p in pat_ids if "patterns.import." in p or ".import." in p)
    cleanup_pat = sum(
        1 for p in pat_ids if "cleanup" in p.lower() or "retire" in p.lower() or "sunset" in p.lower()
    )

    scores = {p: 0.0 for p in POSTURE_TIE_ORDER}

    # expand — growth and positive mission alignment
    scores["expand"] += 3.2 * rp - 2.4 * rn + 2.0 * rma_p - 1.6 * rma_n
    scores["expand"] += 1.0 * radv - 1.3 * high_pat_n
    # Growth corridor: strong positives, no negatives — prefer expand over harvest.
    if int(neg) == 0 and rp >= 0.55:
        scores["expand"] += 0.75

    # consolidate — mixed / flat dominance, many missions
    scores["consolidate"] += 2.0 * rmix + 1.6 * rflat
    if distinct >= 3:
        scores["consolidate"] += 0.6 * float(distinct - 2)
    if n >= 6:
        scores["consolidate"] += 0.35
    scores["consolidate"] -= 0.9 * rn

    # repair — negative outcomes, systemic patterns, inbox and policy stress
    scores["repair"] += 4.0 * rn + 2.8 * high_pat_n + 1.2 * float(import_pat)
    scores["repair"] += 1.8 * _n_ratio(hi_ib, max(1, open_ib)) if open_ib else 0.0
    scores["repair"] += 1.1 * _n_ratio(blocked_stress, n)
    scores["repair"] += 1.1 * _n_ratio(int_stress, n)
    if int_strain is not None:
        scores["repair"] += 2.8 * max(0.0, min(1.0, int_strain))
    scores["repair"] += 0.25 * float(pol_note)

    # harvest — winners ready to monetize / complete (slightly muted vs expand when alignment is strongly positive)
    scores["harvest"] += 2.65 * rp + 2.35 * radv
    if isinstance(avg_debt, (int, float)):
        scores["harvest"] -= 0.04 * max(0.0, float(avg_debt))
    if stag_mean is not None:
        scores["harvest"] += 1.4 * max(0.0, 1.0 - min(1.0, stag_mean))
    scores["harvest"] -= 1.0 * rn
    if rma_p >= 0.65 and rn < 0.15:
        scores["harvest"] -= 0.45

    # create — material new work and proposals
    if new_p:
        scores["create"] += 8.5
    scores["create"] += 0.85 * float(min(prop_n, 6))
    if prop_n >= 2:
        scores["create"] += 1.8
    # retire — tail risk and negative dominance
    if neg >= pos:
        scores["retire"] += 2.8 * rn + 0.9 * rmix
    scores["retire"] += 1.5 * rflat + 1.5 * float(cleanup_pat)
    scores["retire"] -= 1.6 * radv
    scores["retire"] += 0.8 * rma_n

    return scores


def _select_posture(scores: dict[str, float]) -> str:
    m = max(scores.values())
    for p in POSTURE_TIE_ORDER:
        if abs(scores[p] - m) <= 1e-9:
            return p
    return "consolidate"


def _outcomes_mission_mix_block(
    outcomes: dict[str, Any] | None,
    products_evaluated: int,
) -> dict[str, Any] | None:
    """Trajectory/history mission mix from the outcomes artifact (may include removed products)."""
    o_pmp = (outcomes or {}).get("portfolio_mission_provenance") or {}
    o_mix = o_pmp.get("mission_mix_summary") or {}
    o_counts = o_mix.get("counts_by_mission_id") or {}
    if not isinstance(o_counts, dict) or not o_counts:
        return None
    items_o = [(str(k), int(v)) for k, v in o_counts.items() if str(k).strip()]
    if not items_o:
        return None
    items_o.sort(key=lambda x: (-x[1], x[0]))
    top_o, top_co = items_o[0]
    return {
        "counts_by_mission_id": dict(o_counts),
        "products_evaluated": products_evaluated,
        "summary": (
            f"`{top_o}` leads with **{top_co}** product slot(s) in **outcomes-evaluated trajectory** "
            f"(`products_evaluated`={products_evaluated}; historical mix; may include removed products)."
        ),
    }


def _dominant_mission_mix(
    signals: dict[str, Any],
    operator_queue: dict[str, Any] | None,
    outcomes: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Headline mission mix is **inventory-scoped** when operator queue exposes provenance; trajectory
    history is reported separately under ``outcomes_mission_mix`` when the outcomes artifact carries mix.
    """
    inv_raw = signals.get("inventory_valid_product_count")
    inv_n_int: int | None = None if inv_raw is None else max(0, int(inv_raw))
    products_n = int(signals.get("products_n") or 0)

    outcomes_block = _outcomes_mission_mix_block(outcomes, products_n)

    q_pmp = (operator_queue or {}).get("portfolio_mission_provenance") or {}
    q_mix = q_pmp.get("mission_mix_summary") or {}
    q_counts = q_mix.get("counts_by_mission_id") or {}
    if not isinstance(q_counts, dict):
        q_counts = {}

    o_pmp = (outcomes or {}).get("portfolio_mission_provenance") or {}
    o_mix = o_pmp.get("mission_mix_summary") or {}
    o_counts = o_mix.get("counts_by_mission_id") or {}
    if not isinstance(o_counts, dict):
        o_counts = {}

    if q_counts:
        items_q = [(str(k), int(v)) for k, v in q_counts.items() if str(k).strip()]
        items_q.sort(key=lambda x: (-x[1], x[0]))
        top_q, _top_cq = items_q[0]
        distinct_q = len(items_q)
        sum_q = sum(int(v) for _, v in items_q)
        summary = (
            f"`{top_q}` is the dominant mission among **{sum_q}** validated product(s) in **current inventory**; "
            f"{distinct_q} distinct mission id(s)."
        )
        if inv_n_int is not None and sum_q != inv_n_int:
            summary += (
                f" Inventory scan reports **{inv_n_int}** validated manifest(s) under `products/` — reconcile if this differs."
            )
        out: dict[str, Any] = {
            "top_mission_id": top_q,
            "counts_by_mission_id": dict(q_counts),
            "summary": summary,
            "scope": "inventory",
        }
        if outcomes_block:
            out["outcomes_mission_mix"] = outcomes_block
        return out

    if inv_n_int is not None:
        summary = (
            f"**{inv_n_int}** validated product(s) in inventory — mission mix was not available from the operator queue "
            "artifact (re-run `argus portfolio cycle` or refresh the operator queue)."
        )
        out = {
            "top_mission_id": None,
            "counts_by_mission_id": {},
            "summary": summary,
            "scope": "inventory",
        }
        if outcomes_block:
            out["outcomes_mission_mix"] = outcomes_block
        return out

    if o_counts:
        items_o = [(str(k), int(v)) for k, v in o_counts.items() if str(k).strip()]
        items_o.sort(key=lambda x: (-x[1], x[0]))
        top_o, top_co = items_o[0]
        return {
            "top_mission_id": top_o,
            "counts_by_mission_id": dict(o_counts),
            "summary": (
                f"`{top_o}` leads with **{top_co}** product slot(s) in **outcomes-evaluated trajectory** only "
                "(inventory count was not passed; historical)."
            ),
            "scope": "outcomes_history",
        }

    return {
        "top_mission_id": None,
        "counts_by_mission_id": {},
        "summary": "— (no mission mix)",
        "scope": "unavailable",
    }


def _portfolio_pressures(signals: dict[str, Any], outcomes: dict[str, Any] | None) -> list[str]:
    out: list[str] = []
    n = max(1, int(signals.get("products_n") or 1))
    neg = int(signals.get("negative_count") or 0)
    if neg >= 2:
        out.append(f"Negative trajectory concentration: {neg}/{n} products.")
    if int(signals.get("high_severity_patterns") or 0) >= 1:
        out.append(
            f"Cross-product pattern severity: {signals.get('high_severity_patterns')} high-severity pattern(s)."
        )
    oi = int(signals.get("open_inbox_count") or 0)
    aq = int(signals.get("active_queue_inbox_count") or 0)
    if oi >= 1:
        out.append(f"Intervention load: {oi} open inbox item(s), {aq} in active queue.")
    man = int(signals.get("mission_align_neg") or 0)
    if man >= 2:
        out.append(f"Mission alignment stress: {man} product(s) with negative alignment.")
    bs = int(signals.get("blocked_stress_count") or 0)
    if bs >= 1:
        out.append(f"Blocked progression signals on {bs} product(s).")
    ist = signals.get("intervention_strain_rate")
    if isinstance(ist, (int, float)) and ist >= 0.35:
        out.append(f"Elevated intervention strain rate (~{ist:.2f}) under current policy segments.")
    codes = (outcomes or {}).get("outcome_reason_codes") or []
    if isinstance(codes, list) and any("import" in str(c).lower() for c in codes):
        out.append("Importer / first-pass stress codes present in portfolio outcomes.")
    return out[:8]


def _top_opportunities(
    signals: dict[str, Any],
    outcomes: dict[str, Any] | None,
    operator_queue: dict[str, Any] | None,
    valid_product_ids: AbstractSet[str] | None = None,
) -> list[str]:
    pos = list((outcomes or {}).get("products_with_positive_trajectory") or [])
    entries = [e for e in ((operator_queue or {}).get("entries") or []) if isinstance(e, dict)]
    adv_ids = [
        str(e.get("product_id") or "")
        for e in entries
        if "advance" in str(e.get("readiness_tier") or "").lower()
    ]
    merged: list[str] = []
    for x in pos + adv_ids:
        xs = str(x).strip()
        if not xs or xs in merged:
            continue
        if valid_product_ids is not None and xs not in valid_product_ids:
            continue
        merged.append(xs)
    return [f"Prioritize `{p}` (positive trajectory or advance-ready)" for p in merged[:6]]


def _top_risks(
    signals: dict[str, Any],
    outcomes: dict[str, Any] | None,
    patterns: dict[str, Any] | None,
    intervention_inbox: dict[str, Any] | None,
    valid_product_ids: AbstractSet[str] | None = None,
) -> list[str]:
    risks: list[str] = []
    neg = list((outcomes or {}).get("products_with_negative_trajectory") or [])
    for p in neg[:4]:
        ps = str(p).strip()
        if valid_product_ids is not None and ps and ps not in valid_product_ids:
            continue
        risks.append(f"Stabilize `{p}` — negative overall trajectory.")

    dpat = [p for p in ((patterns or {}).get("detected_patterns") or []) if isinstance(p, dict)]
    dpat.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}.get(str(x.get("severity")), 9), str(x.get("pattern_id"))))
    for p in dpat[:3]:
        pid = p.get("pattern_id")
        sev = p.get("severity")
        title = p.get("title")
        risks.append(f"Pattern `{pid}` ({sev}): {title}")

    items = [x for x in ((intervention_inbox or {}).get("open_items") or []) if isinstance(x, dict)]
    items.sort(key=lambda x: ({"high": 0, "medium": 1, "low": 2}.get(str(x.get("severity")), 9), str(x.get("product_id"))))
    for it in items[:3]:
        if str(it.get("severity") or "") == "high":
            ipid = str(it.get("product_id") or "").strip()
            if valid_product_ids is not None and ipid and ipid not in valid_product_ids:
                continue
            risks.append(
                f"Inbox: `{it.get('product_id')}` — {it.get('intervention_category') or 'intervention'} ({it.get('severity')})"
            )
    return risks[:8]


def _recommended_moves(posture: str, signals: dict[str, Any]) -> list[str]:
    n = int(signals.get("products_n") or 0)
    oi = int(signals.get("open_inbox_count") or 0)
    adv = int(signals.get("advance_ready_count") or 0)
    prop = int(signals.get("creation_proposal_count") or 0)
    new_p = bool(signals.get("new_product_in_queue"))

    if posture == "expand":
        return [
            f"Lean into momentum: {adv} advance-ready product(s) in queue — sequence operator work for throughput.",
            "Protect positive trajectories; keep delta/quiescence cadence tight to avoid regressions.",
            "Align mission drivers across products where overlap exists to compound learning.",
        ]
    if posture == "consolidate":
        return [
            f"Reduce parallel surface area: portfolio has mixed/flat signals across ~{n} product(s) — pick fewer bets.",
            "Standardize tooling and importer posture where patterns repeat.",
            "Reconcile mission mix before adding new experiments.",
        ]
    if posture == "repair":
        return [
            "Clear systemic blockers first (import, policy gaps) before scaling new work.",
            f"Drain intervention debt: {oi} inbox item(s) — resolve or explicitly snooze with owner.",
            "Tighten progression criteria where products are repeatedly blocked.",
        ]
    if posture == "harvest":
        return [
            "Shift capacity from exploration to extraction on mature, advance-ready winners.",
            "Defer non-critical experiments; document what 'done' means for top-ranked products.",
            "Capture learnings into doctrine/runbooks while confidence is high.",
        ]
    if posture == "create":
        moves = [
            "Prioritize the creation pipeline: validate concepts against mission gaps before build-out.",
            f"Treat proposals as a portfolio program: {prop} proposal(s) on file — rank and staff explicitly.",
        ]
        if new_p:
            moves.append("New product materiality detected in delta report — onboard and baseline explicitly.")
        return moves
    if posture == "retire":
        return [
            "Make exit criteria explicit for negative-trajectory products; avoid silent drag.",
            "Sunset or merge overlapping products where missions duplicate.",
            "Redirect operator capacity from tail products to advance-ready or net-new creation.",
        ]
    return ["Re-evaluate portfolio inputs and re-run strategy after the next cycle."]


def _rationale_lines(
    posture: str,
    scores: dict[str, float],
    signals: dict[str, Any],
) -> list[str]:
    n = max(1, int(signals.get("products_n") or 1))
    lines = [
        f"Selected posture `{posture}` among scores: "
        + ", ".join(f"{k}={scores[k]:.2f}" for k in POSTURE_TIE_ORDER),
        f"Outcome mix: positive {signals.get('positive_count')}/{n}, negative {signals.get('negative_count')}/{n}, "
        f"mixed {signals.get('mixed_count')}, flat {signals.get('flat_count')}.",
    ]
    lines.append(
        f"Mission alignment: +{signals.get('mission_align_pos')} / neutral {signals.get('mission_align_neutral')} "
        f"/ −{signals.get('mission_align_neg')}."
    )
    lines.append(
        f"Patterns: {signals.get('high_severity_patterns')} high · {signals.get('medium_severity_patterns')} medium; "
        f"inbox open {signals.get('open_inbox_count')} (active queue {signals.get('active_queue_inbox_count')})."
    )
    if signals.get("new_product_in_queue"):
        lines.append("Delta report signals new_product_in_queue materiality.")
    if int(signals.get("creation_proposal_count") or 0) > 0:
        lines.append(f"Creation proposals on file: {signals.get('creation_proposal_count')}.")
    return lines


def _non_outcome_portfolio_signals(signals: dict[str, Any]) -> bool:
    """True when queue, patterns, inbox, policy, creation, or per-product stress flags exist."""
    if int(signals.get("queue_entry_count") or 0) > 0:
        return True
    if int(signals.get("open_inbox_count") or 0) > 0:
        return True
    hs = int(signals.get("high_severity_patterns") or 0)
    ms = int(signals.get("medium_severity_patterns") or 0)
    if hs + ms > 0:
        return True
    if int(signals.get("creation_proposal_count") or 0) > 0:
        return True
    if int(signals.get("policy_notable_count") or 0) > 0:
        return True
    if bool(signals.get("new_product_in_queue")):
        return True
    if int(signals.get("blocked_stress_count") or 0) > 0:
        return True
    if int(signals.get("intervention_stress_count") or 0) > 0:
        return True
    return False


def _zero_evaluated_bucket(
    signals: dict[str, Any],
    inventory_n: int | None,
    side: bool,
) -> str:
    """Classifier for messaging when products_evaluated is 0 (posture remains create)."""
    if inventory_n is not None and inventory_n == 0:
        return "no_inventory"
    if inventory_n is not None and inventory_n > 0:
        return "sparse_portfolio_evidence" if side else "inventory_without_outcomes"
    # Inventory unknown (caller did not pass a scan count).
    return "unknown_sparse_evidence" if side else "unknown_without_outcomes"


def _fields_for_zero_evaluated_products(
    signals: dict[str, Any],
    operator_queue: dict[str, Any] | None = None,
    outcomes: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Rationale and lists when outcomes report zero evaluated products (posture: create)."""
    inv = signals.get("inventory_valid_product_count")
    inventory_n: int | None = None if inv is None else max(0, int(inv))
    side = _non_outcome_portfolio_signals(signals)
    bucket = _zero_evaluated_bucket(signals, inventory_n, side)
    signals_out = dict(signals)
    signals_out["zero_evaluated_bucket"] = bucket

    qn = int(signals.get("queue_entry_count") or 0)

    if bucket == "no_inventory":
        rationale = [
            "Validated inventory is empty (no passing `product.yaml` manifests under `products/`) — "
            "default posture is `create` until at least one product is onboarded.",
        ]
        if qn > 0:
            rationale.append(
                f"Note: operator queue lists {qn} entries, but the inventory scan found no validated "
                "manifests — reconcile `products/` paths, ids, and validation errors."
            )
        pressures = ["No validated products in inventory."]
        opportunities = [
            "Add or fix product manifests under `products/` and run `argus portfolio refresh`.",
        ]
        risks = [
            "Strategic posture is a placeholder until validated inventory and outcome history exist.",
        ]
        moves = [
            "Add or validate product manifests under `products/`.",
            "Run `argus portfolio refresh`, then `argus portfolio outcomes` and re-run `argus portfolio strategy`.",
        ]
    elif bucket == "inventory_without_outcomes":
        rationale = [
            f"Validated inventory includes {inventory_n} product(s), but portfolio outcomes report **zero "
            "evaluated products** — there is not yet usable outcome history for strategy scoring. "
            "Default posture is `create` until `argus portfolio outcomes` reflects evaluated coverage.",
        ]
        pressures = [
            "Outcome history is missing or not yet tied to evaluated products despite existing inventory.",
        ]
        opportunities = [
            "Run `argus portfolio outcomes` (and ensure mission/run history exists) so strategy can score real trajectories.",
        ]
        risks = [
            "Posture and scores are defaulting without per-product outcome signal — avoid over-interpreting headline posture.",
        ]
        moves = [
            "Refresh portfolio outcomes after the next operator cycle or mission runs.",
            "Re-run `argus portfolio strategy` once `products_evaluated` is non-zero.",
        ]
    elif bucket == "sparse_portfolio_evidence":
        rationale = [
            f"Validated inventory includes {inventory_n} product(s), and other artifacts (queue, patterns, inbox, "
            "or policy signals) show activity, but outcomes still report **zero evaluated products** — "
            "portfolio evidence for strategy scoring is sparse. Default posture is `create`; treat non-outcome "
            "signals as directional until outcome history catches up.",
        ]
        pressures = [
            "Non-outcome signals exist, but evaluated outcome coverage is still empty — strategy is under-informed.",
        ]
        opportunities = [
            "Build outcome coverage while using queue/patterns/inbox as triage hints, not as full strategy substitutes.",
        ]
        risks = [
            "Sparse cross-artifact evidence can look busy while outcomes are still empty — verify `products_evaluated` before acting on posture.",
        ]
        moves = [
            "Run `argus portfolio outcomes` after inventory-sourced runs exist; then re-run strategy.",
            "If the queue looks populated but outcomes stay at zero, check mission linkage and outcome evaluation inputs.",
        ]
    elif bucket == "unknown_sparse_evidence":
        rationale = [
            "Portfolio outcomes report **zero evaluated products**; other artifacts (queue, patterns, inbox, or policy) "
            "show activity — evidence mix is sparse for strategy scoring. Default posture is `create` until outcome "
            "history is populated (inventory count was not passed into this derivation).",
        ]
        pressures = ["Evaluated outcome coverage is empty while other portfolio signals are non-empty."]
        opportunities = [
            "Run `argus portfolio outcomes` after runs exist; pass inventory-aware strategy when integrating programmatically.",
        ]
        risks = [
            "Headline posture defaults without `products_evaluated` — confirm outcomes and inventory before major bets.",
        ]
        moves = [
            "Prefer `build_portfolio_strategy_payload` (inventory-aware) over raw `derive_portfolio_strategy_fields` for repo-backed runs.",
            "Re-run strategy after outcomes show non-zero evaluated products.",
        ]
    else:
        # unknown_without_outcomes
        rationale = [
            "Portfolio outcomes report **zero evaluated products** — default posture is `create`. "
            "Run `argus portfolio outcomes` after manifests and runs exist so strategy can score trajectory data "
            "(inventory count was not passed into this derivation).",
        ]
        pressures = ["No evaluated products in the outcomes artifact — strategy scores cannot represent portfolio trajectory yet."]
        opportunities = [
            "Run `argus portfolio outcomes` once mission/run history covers your products.",
        ]
        risks = [
            "Cannot tell from strategy inputs alone whether inventory is empty or outcomes are stale — verify `products/` and latest outcomes run.",
        ]
        moves = [
            "Run `argus portfolio refresh` and `argus portfolio outcomes`, then re-run `argus portfolio strategy`.",
        ]

    return {
        "strategic_posture": "create",
        "posture_scores": {p: 0.0 for p in POSTURE_TIE_ORDER},
        "rationale": rationale,
        "dominant_mission_mix": _dominant_mission_mix(signals_out, operator_queue, outcomes),
        "portfolio_pressures": pressures,
        "top_opportunities": opportunities,
        "top_risks": risks,
        "recommended_next_portfolio_moves": moves,
        "signals": signals_out,
    }


def derive_portfolio_strategy_fields(
    *,
    outcomes: dict[str, Any] | None = None,
    patterns: dict[str, Any] | None = None,
    operator_queue: dict[str, Any] | None = None,
    intervention_inbox: dict[str, Any] | None = None,
    policy_effectiveness: dict[str, Any] | None = None,
    delta_report: dict[str, Any] | None = None,
    creation_proposals: dict[str, Any] | None = None,
    inventory_valid_product_count: int | None = None,
    valid_product_ids: AbstractSet[str] | None = None,
) -> dict[str, Any]:
    """
    Pure derivation of strategy fields from portfolio artifacts (all optional — missing pieces weaken signals).

    When ``valid_product_ids`` is set (e.g. from :func:`argus.products.inventory.build_inventory`),
    ``top_opportunities`` and product-scoped ``top_risks`` lines exclude ids not present on disk.
    """
    signals = collect_strategy_signals(
        outcomes=outcomes,
        patterns=patterns,
        operator_queue=operator_queue,
        intervention_inbox=intervention_inbox,
        policy_effectiveness=policy_effectiveness,
        delta_report=delta_report,
        creation_proposals=creation_proposals,
        inventory_valid_product_count=inventory_valid_product_count,
    )
    if signals["products_n"] == 0:
        return _fields_for_zero_evaluated_products(signals, operator_queue, outcomes)

    scores = _score_postures(signals)
    posture = _select_posture(scores)
    return {
        "strategic_posture": posture,
        "posture_scores": scores,
        "rationale": _rationale_lines(posture, scores, signals),
        "dominant_mission_mix": _dominant_mission_mix(signals, operator_queue, outcomes),
        "portfolio_pressures": _portfolio_pressures(signals, outcomes),
        "top_opportunities": _top_opportunities(signals, outcomes, operator_queue, valid_product_ids),
        "top_risks": _top_risks(signals, outcomes, patterns, intervention_inbox, valid_product_ids),
        "recommended_next_portfolio_moves": _recommended_moves(posture, signals),
        "signals": signals,
    }


def render_portfolio_strategy_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Portfolio strategy",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Run id:** `{payload.get('run_id')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Strategic posture",
        "",
        f"**`{payload.get('strategic_posture')}`**",
        "",
    ]
    zb = (payload.get("inputs") or {}).get("zero_evaluated_bucket")
    if zb:
        lines.extend(
            [
                f"*Outcomes report zero evaluated products; explanation class: `{zb}` (detail in rationale below).*",
                "",
            ]
        )
    lines.extend(
        [
            "### Rationale",
            "",
        ]
    )
    for r in payload.get("rationale") or []:
        lines.append(f"- {r}")
    dmm = payload.get("dominant_mission_mix") or {}
    scope = str(dmm.get("scope") or "").strip()
    dmm_title = "## Dominant mission mix"
    if scope == "inventory":
        dmm_title = "## Dominant mission mix (current inventory)"
    elif scope == "outcomes_history":
        dmm_title = "## Dominant mission mix (outcomes-evaluated trajectory only)"
    lines.extend(
        [
            "",
            dmm_title,
            "",
            str(dmm.get("summary") or "—"),
            "",
        ]
    )
    omm = dmm.get("outcomes_mission_mix")
    if isinstance(omm, dict) and str(omm.get("summary") or "").strip():
        lines.extend(
            [
                "### Outcomes-evaluated trajectory (historical)",
                "",
                str(omm.get("summary") or "—"),
                "",
            ]
        )
    lines.extend(
        [
            "## Portfolio pressures",
            "",
        ]
    )
    pp = payload.get("portfolio_pressures") or []
    if not pp:
        lines.append("—")
    else:
        for x in pp:
            lines.append(f"- {x}")
    lines.extend(["", "## Top opportunities", ""])
    to = payload.get("top_opportunities") or []
    if not to:
        lines.append("—")
    else:
        for x in to:
            lines.append(f"- {x}")
    lines.extend(["", "## Top risks", ""])
    tr = payload.get("top_risks") or []
    if not tr:
        lines.append("—")
    else:
        for x in tr:
            lines.append(f"- {x}")
    lines.extend(["", "## Recommended next portfolio moves", ""])
    for x in payload.get("recommended_next_portfolio_moves") or []:
        lines.append(f"- {x}")
    si = payload.get("soft_influence") or {}
    if si.get("downstream_hints"):
        lines.extend(["", "## Soft influence (downstream hints)", ""])
        for k, v in sorted((si.get("downstream_hints") or {}).items()):
            lines.append(f"- **{k}:** {v}")
        lines.append(
            f"- **Bounded:** {si.get('bounded')} · **max queue nudge:** {si.get('max_queue_priority_nudge')}"
        )
    lines.extend(["", "## Posture scores (deterministic)", ""])
    ps = payload.get("posture_scores") or {}
    for k in POSTURE_TIE_ORDER:
        if k in ps:
            lines.append(f"- `{k}`: {ps[k]:.3f}")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _load_delta_report_optional(repo_root: Path) -> dict[str, Any] | None:
    return _load_json(portfolio_delta_report_dir(repo_root) / "latest.json")


def _load_creation_proposals_optional(repo_root: Path) -> dict[str, Any] | None:
    raw = _load_json(creation_proposals_dir(repo_root) / "latest.json")
    if raw and str(raw.get("schema") or "") == PRODUCT_CREATION_PROPOSALS_SCHEMA:
        return raw
    return None


def build_portfolio_strategy_payload(
    repo_root: Path,
    *,
    limit_history: int = 50,
    products_dir: Path | None = None,
    outcomes_payload: dict[str, Any] | None = None,
    patterns_payload: dict[str, Any] | None = None,
    operator_queue_payload: dict[str, Any] | None = None,
    intervention_inbox_payload: dict[str, Any] | None = None,
    policy_effectiveness_payload: dict[str, Any] | None = None,
    delta_report_payload: dict[str, Any] | None = None,
    creation_proposals_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = repo_root.resolve()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lim = max(1, int(limit_history))

    outcomes = outcomes_payload or evaluate_portfolio_outcomes(root, limit_history=lim)
    patterns = patterns_payload or evaluate_portfolio_patterns(
        root, limit_history=lim, products_dir=products_dir
    )
    queue = operator_queue_payload or build_operator_queue_payload(
        root, products_dir=products_dir
    )
    inbox = intervention_inbox_payload or build_intervention_inbox_payload(root)
    policy_eff = policy_effectiveness_payload or evaluate_operator_policy_effectiveness(
        root, limit_history=lim
    )
    delta_rep = delta_report_payload if delta_report_payload is not None else _load_delta_report_optional(root)
    creation = (
        creation_proposals_payload
        if creation_proposals_payload is not None
        else _load_creation_proposals_optional(root)
    )

    inv = build_inventory(root, products_dir=products_dir)
    valid_ids = frozenset(inv.valid.keys())
    inv_n = len(inv.valid)

    derived = derive_portfolio_strategy_fields(
        outcomes=outcomes,
        patterns=patterns,
        operator_queue=queue,
        intervention_inbox=inbox,
        policy_effectiveness=policy_eff,
        delta_report=delta_rep,
        creation_proposals=creation,
        inventory_valid_product_count=inv_n,
        valid_product_ids=valid_ids,
    )

    from argus.portfolio.strategy_influence import describe_soft_influence

    soft_influence = describe_soft_influence(str(derived.get("strategic_posture") or "") or None)

    strat_signals = derived.get("signals") or {}
    inputs: dict[str, Any] = {
        "limit_history": lim,
        "inventory_valid_product_count": inv_n,
        "operator_queue_schema": queue.get("schema"),
        "operator_queue_generated_at_utc": queue.get("generated_at_utc"),
        "outcomes_schema": outcomes.get("schema"),
        "outcomes_run_id": outcomes.get("run_id"),
        "patterns_schema": patterns.get("schema"),
        "patterns_run_id": patterns.get("run_id"),
        "intervention_inbox_schema": inbox.get("schema"),
        "policy_effectiveness_schema": policy_eff.get("schema"),
        "policy_effectiveness_run_id": policy_eff.get("run_id"),
        "delta_report_loaded": delta_rep is not None,
        "delta_report_run_id": (delta_rep or {}).get("run_id"),
        "creation_proposals_loaded": creation is not None,
        "creation_proposal_count": int((creation or {}).get("proposal_count") or 0)
        if creation
        else 0,
        "inventory_valid_product_ids": sorted(valid_ids),
    }
    if int(strat_signals.get("products_n") or 0) == 0:
        zb = strat_signals.get("zero_evaluated_bucket")
        if zb is not None:
            inputs["zero_evaluated_bucket"] = zb

    def _schema_latest_ok(subdir: Path, filename: str, schema: str) -> bool:
        raw = _load_json(subdir / filename)
        return bool(raw and str(raw.get("schema") or "") == schema)

    inputs["canonical_artifacts"] = {
        "portfolio_outcomes_latest": _read_canonical_portfolio_outcomes_latest(root) is not None,
        "portfolio_intervention_latest": _schema_latest_ok(
            portfolio_intervention_dir(root), "latest.json", PORTFOLIO_INTERVENTION_SCHEMA
        ),
        "intervention_inbox_latest": _schema_latest_ok(
            intervention_inbox_dir(root), "latest.json", INTERVENTION_INBOX_SCHEMA
        ),
        "operator_queue_latest": _schema_latest_ok(
            operator_queue_output_dir(root), "latest.json", OPERATOR_QUEUE_SCHEMA
        ),
        "portfolio_patterns_latest": _schema_latest_ok(
            portfolio_patterns_dir(root), "latest.json", PORTFOLIO_PATTERNS_SCHEMA
        ),
    }
    inputs["outcomes_evaluated_without_canonical_latest"] = (
        outcomes_payload is None and _read_canonical_portfolio_outcomes_latest(root) is None
    )

    return {
        "schema": PORTFOLIO_STRATEGY_SCHEMA,
        "run_id": run_id,
        "evaluated_at_utc": evaluated_at,
        "strategic_posture": derived["strategic_posture"],
        "posture_scores": derived["posture_scores"],
        "rationale": derived["rationale"],
        "dominant_mission_mix": derived["dominant_mission_mix"],
        "portfolio_pressures": derived["portfolio_pressures"],
        "top_opportunities": derived["top_opportunities"],
        "top_risks": derived["top_risks"],
        "recommended_next_portfolio_moves": derived["recommended_next_portfolio_moves"],
        "soft_influence": soft_influence,
        "inputs": inputs,
    }


def write_portfolio_strategy_artifacts(
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
    d = portfolio_strategy_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_portfolio_strategy_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_portfolio_strategy(
    repo_root: Path,
    *,
    limit_history: int = 50,
    products_dir: Path | None = None,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = build_portfolio_strategy_payload(
        repo_root,
        limit_history=limit_history,
        products_dir=products_dir,
    )
    if write_artifacts:
        write_portfolio_strategy_artifacts(repo_root, payload)
    return payload
