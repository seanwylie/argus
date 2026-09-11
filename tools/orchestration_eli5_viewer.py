#!/usr/bin/env python3
"""
Minimal read-only ELI5 viewer for Argus orchestration (stdlib only).

Serves a single HTML page from ``runs/orchestration/latest/operator_summary.json``.
Optionally reads the linked orchestration state file and lightweight ``runs/`` artifacts
for a clearer picture — no writes, no orchestration imports.

Example::

    python tools/orchestration_eli5_viewer.py

Uses the Argus repo that contains ``tools/`` (or set ``ARGUS_REPO_ROOT`` / ``--repo-root``).

Default bind: 127.0.0.1:9147 (override with --port or ARGUS_ELI5_PORT).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Plain-language story stages (order matters for the bar).
# Nine steps: observe → interpret → ideas → experiment loop → wrap-up.
STAGES: list[tuple[str, str]] = [
    ("look", "Observe & refresh inputs"),
    ("understand", "Interpret findings & decisions"),
    ("ideas", "Ideas & refinement"),
    ("propose", "Propose experiments"),
    ("pick", "Prioritize & pick"),
    ("create", "Create experiment"),
    ("start", "Activate & run"),
    ("evaluate", "Measure results"),
    ("close", "Close stale work"),
]

# Expected schema ids in runs/ (no package imports).
_SCHEMA_STRATEGY_SNAPSHOT = "argus.strategy_snapshot.v1"
_SCHEMA_PLANNING_SNAPSHOT = "argus.planning_snapshot.v1"
_SCHEMA_PORTFOLIO_PRIORITIES = "argus.portfolio_priorities.v1"
_SCHEMA_PORTFOLIO_TRENDS = "argus.portfolio_priority_trends.v1"

# Map orchestration next_action → stage index 0..8. "none" handled separately.
ACTION_TO_STAGE: dict[str, int] = {
    "signals_collect": 0,
    "temporal_refresh": 0,
    "audit_run": 0,
    "orchestration_state_refresh": 0,
    "findings_generate": 1,
    "decisions_generate": 1,
    "strategy_refresh_from_decision_evolution": 1,
    "planning_refresh_from_strategy": 1,
    "ideas_generate": 2,
    "refinement_start_idea": 2,
    "refinement_run": 2,
    "refinement_submit_reviews_in": 2,
    "refinement_start_product_spec": 2,
    "implementation_plan_generate": 2,
    "experiments_propose": 3,
    "experiments_prioritize": 4,
    "experiments_create": 5,
    "experiments_activate": 6,
    "experiments_evaluate": 7,
    "experiments_close_stale": 8,
    "escalation_packet_generate": 4,
    "escalation_consider": 4,
    "execution_outcomes_apply": 8,
}

# Short labels for strategy posture / planning mode (file artifacts).
_POSTURE_PLAIN: dict[str, str] = {
    "pivot": "Pivot — shift direction",
    "explore": "Explore — learn more",
    "double_down": "Double down — reinforce current bets",
    "stabilize": "Stabilize — integrate and hold",
}
_PLANNING_PLAIN: dict[str, str] = {
    "redirect": "Redirect",
    "validate": "Validate",
    "expand": "Expand",
    "consolidate": "Consolidate",
}

# One-line headline for the hero (no internal ids).
ACTION_LABELS: dict[str, str] = {
    "none": "Pausing — nothing is queued for the next automatic step",
    "signals_collect": "Gathering fresh signals about how the product is doing",
    "temporal_refresh": "Refreshing time-based health snapshots",
    "audit_run": "Running a structured product check",
    "orchestration_state_refresh": "Refreshing the live status snapshot",
    "findings_generate": "Turning raw signals into clear findings",
    "decisions_generate": "Recording decisions that follow from those findings",
    "strategy_refresh_from_decision_evolution": "Updating the strategic posture from recent decision changes",
    "planning_refresh_from_strategy": "Refreshing the planning view from the current strategy",
    "ideas_generate": "Drafting concrete improvement ideas",
    "refinement_start_idea": "Opening a session to shape an idea with reviews",
    "refinement_run": "Continuing the idea-shaping session",
    "refinement_submit_reviews_in": "Waiting for review input to move forward",
    "refinement_start_product_spec": "Starting product-spec refinement",
    "implementation_plan_generate": "Drafting how the work would be implemented",
    "experiments_propose": "Suggesting experiments worth trying",
    "experiments_prioritize": "Comparing options and ranking the strongest experiment",
    "experiments_create": "Creating a tracked experiment from the ranked pick",
    "experiments_activate": "Marking an experiment as active so measurement can begin",
    "experiments_evaluate": "Measuring how the experiment turned out",
    "experiments_close_stale": "Closing experiments that sat too long without finishing",
    "escalation_packet_generate": "Preparing a clear escalation summary for people",
    "escalation_consider": "Reviewing whether an escalation is needed",
    "execution_outcomes_apply": "Applying agreed outcomes from recent runs",
}

# Friendly rewrites for compact_summary_lines (substring replace, order matters for longer phrases first).
_COMPACT_LINE_FRIENDLY: list[tuple[str, str]] = [
    ("experiments: proposals_present", "Saved experiment proposals on disk"),
    ("experiments: propose_ready", "Ready to propose experiments"),
    ("idea_refinement: refinement_run_eligible_for_idea_session", "An idea session can take another refinement round"),
    ("idea_refinement: refinement_submit_reviews_in_eligible", "Review input can be submitted for the idea session"),
    ("idea_refinement: next=refinement_submit_reviews_in", "Next: add review input for the idea in progress"),
    ("idea_refinement: active_non_terminal_idea_session", "An idea is being shaped (session active)"),
    ("idea_refinement: refinement_start_idea_eligible", "Ready to start shaping an idea with a session"),
    ("fairness: rotated (would_have_selected_without_fairness=", "Fairness rotation (would have picked another product: "),
    ("execution_recorded=true", "Last step finished and was recorded"),
    ("action_status=", "Step status: "),
    ("transition=", "Latest change: "),
    ("selection=", "Why this product: "),
    ("selected_product=", "Focused product: "),
]


def _html_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _human_date_short(iso_ts: str) -> str:
    """Trim ISO timestamps to a short date for muted lines (no time zone jargon)."""
    s = iso_ts.strip()
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        return s[:10]
    return s[:16] if len(s) > 16 else s


def _muted_detail_line(summary: dict[str, Any], hints: dict[str, Any]) -> str:
    """Tiny muted line: snapshot time and latest evaluation date only."""
    parts: list[str] = []
    ev = summary.get("evaluated_at_utc")
    if isinstance(ev, str) and ev.strip():
        parts.append(f"Summary as of {_human_date_short(ev)}")
    la = hints.get("latest_evaluation_at")
    if isinstance(la, str) and la.strip():
        parts.append(f"Latest evaluation on record: {_human_date_short(la)}")
    return " · ".join(parts)


def _trim(s: str, max_len: int = 220) -> str:
    t = " ".join(s.split())
    if len(t) <= max_len:
        return t
    return t[: max_len - 1] + "…"


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _repo_root(path: Path) -> Path:
    return path.resolve()


def infer_repo_root() -> Path:
    """
    Prefer ``ARGUS_REPO_ROOT``, else the checkout that contains ``tools/`` (parent of this file),
    else current working directory.
    """
    env = (os.environ.get("ARGUS_REPO_ROOT") or "").strip()
    if env:
        return Path(env).resolve()
    here = Path(__file__).resolve()
    if here.parent.name == "tools":
        return here.parent.parent
    return Path.cwd().resolve()


def _operator_summary_path(root: Path) -> Path:
    return root / "runs" / "orchestration" / "latest" / "operator_summary.json"


def _fleet_governance_rollup_path(root: Path) -> Path:
    return root / "runs" / "orchestration" / "latest" / "fleet_governance_rollup.json"


def _resolve_state_path(root: Path, summary: dict[str, Any]) -> Path | None:
    links = summary.get("artifact_links")
    if not isinstance(links, dict):
        return None
    rel = links.get("orchestration_state_repo_relative")
    if not isinstance(rel, str) or not rel.strip():
        return None
    p = (root / rel).resolve()
    try:
        p.relative_to(root.resolve())
    except ValueError:
        return None
    return p


def _fleet_product_row(root: Path, product_id: str) -> dict[str, Any] | None:
    raw = _load_json(_fleet_governance_rollup_path(root))
    if not raw:
        return None
    products = raw.get("products")
    if not isinstance(products, list):
        return None
    for row in products:
        if isinstance(row, dict) and str(row.get("product_id") or "") == product_id:
            return row
    return None


def _phase2_from_fleet(root: Path, product_id: str) -> dict[str, Any] | None:
    row = _fleet_product_row(root, product_id)
    if not row:
        return None
    p2 = row.get("phase2_posture")
    return p2 if isinstance(p2, dict) else None


def _merge_summary_from_fleet(summary: dict[str, Any], root: Path, product_id: str) -> dict[str, Any]:
    """
    Richer display only: fill ``experiment_posture`` / ``idea_refinement_posture`` from
    ``fleet_governance_rollup.json`` when missing from operator_summary.
    """
    if not product_id.strip():
        return summary
    need_exp = not isinstance(summary.get("experiment_posture"), dict)
    need_idea = not isinstance(summary.get("idea_refinement_posture"), dict)
    if not need_exp and not need_idea:
        return summary
    row = _fleet_product_row(root, product_id)
    if not row:
        return summary
    out = dict(summary)
    if need_exp and isinstance(row.get("experiment_posture"), dict):
        out["experiment_posture"] = row["experiment_posture"]
    if need_idea and isinstance(row.get("idea_refinement_posture"), dict):
        out["idea_refinement_posture"] = row["idea_refinement_posture"]
    return out


def _experiment_started_on_disk(raw: dict[str, Any]) -> bool:
    """True when the saved experiment has moved past \"created only\" (active / ran / or started)."""
    st = str(raw.get("status") or "").strip().lower()
    if st in ("active", "completed", "failed"):
        return True
    if st == "proposed":
        if str(raw.get("start_at") or "").strip():
            return True
        if str(raw.get("last_execution_at_utc") or "").strip():
            return True
    return False


def _list_product_experiments(root: Path, product_id: str) -> list[dict[str, Any]]:
    d = root / "runs" / "experiments"
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(d.glob("exp_*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("product_id") or "") != product_id:
            continue
        out.append(raw)
    return out


def _count_stale_close_executions(root: Path, product_id: str) -> int:
    """How many successful ``experiments_close_stale`` runs left feedback on disk (read-only)."""
    d = root / "runs" / "execution" / product_id
    if not d.is_dir():
        return 0
    n = 0
    for p in d.glob("orchestration_feedback_*.json"):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("action_id") or "") != "experiments_close_stale":
            continue
        if str(raw.get("orchestration_action_status") or "") != "executed":
            continue
        if raw.get("success") is not True:
            continue
        n += 1
    return n


def _gather_artifact_hints(root: Path, product_id: str, summary: dict[str, Any]) -> dict[str, Any]:
    """Read-only hints from summary + disk (no orchestration code)."""
    hints: dict[str, Any] = {
        "signals_present": (root / "runs" / "signals" / "latest" / f"{product_id}.json").is_file(),
        "findings_present": (root / "runs" / "findings" / "latest" / f"{product_id}.json").is_file(),
        "decisions_present": (root / "runs" / "decisions" / "latest" / f"{product_id}.json").is_file(),
        "ideas_latest_for_product": False,
        "proposals_present": False,
        "proposal_count": None,
        "prioritization_present": (
            root / "runs" / "experiments" / "prioritization" / "latest" / f"{product_id}.json"
        ).is_file(),
        "experiments_for_product": 0,
        "experiments_started": 0,
        "experiments_evaluated": 0,
        "stale_close_executions": 0,
    }
    ideas_p = root / "runs" / "ideas" / "latest.json"
    if ideas_p.is_file():
        try:
            ir = json.loads(ideas_p.read_text(encoding="utf-8"))
            if isinstance(ir, dict) and str(ir.get("product_id") or "") == product_id:
                hints["ideas_latest_for_product"] = True
        except (OSError, json.JSONDecodeError, UnicodeError):
            pass

    exp_post = summary.get("experiment_posture")
    if isinstance(exp_post, dict):
        hints["proposals_present"] = bool(exp_post.get("experiment_proposals_present"))
        pc = exp_post.get("proposal_count")
        if isinstance(pc, int) and pc >= 0:
            hints["proposal_count"] = pc

    exps = _list_product_experiments(root, product_id)
    hints["experiments_for_product"] = len(exps)
    hints["experiments_started"] = sum(1 for e in exps if _experiment_started_on_disk(e))
    hints["experiments_evaluated"] = sum(
        1
        for e in exps
        if isinstance(e.get("last_evaluation_at"), str) and str(e.get("last_evaluation_at")).strip()
    )
    latest_ev: str | None = None
    for e in exps:
        la = e.get("last_evaluation_at")
        if isinstance(la, str) and la.strip():
            if latest_ev is None or la > latest_ev:
                latest_ev = la
    hints["latest_evaluation_at"] = latest_ev
    hints["stale_close_executions"] = _count_stale_close_executions(root, product_id)
    return hints


def _ladder_completed_mask(
    hints: dict[str, Any],
    state: dict[str, Any] | None,
) -> list[bool]:
    """Which stages 0..8 have evidence of progress (for coloring when next is idle)."""
    n = len(STAGES)
    done = [False] * n
    p2 = state.get("phase2_posture") if isinstance(state, dict) else None
    chain = ""
    if isinstance(p2, dict):
        chain = str(p2.get("chain_furthest_ready") or "").strip()

    # Observe / look
    if hints["signals_present"] or chain in {"interpret", "decide", "ideas", "govern"}:
        done[0] = True
    # Understand
    if (hints["findings_present"] and hints["decisions_present"]) or chain in {"decide", "ideas", "govern"}:
        done[1] = True
    # Ideas
    if hints["ideas_latest_for_product"] or chain in {"ideas", "govern"}:
        done[2] = True
    # Propose
    if hints["proposals_present"]:
        done[3] = True
    # Pick (rank / prioritize)
    if hints["prioritization_present"] or chain == "govern":
        done[4] = True
    # Create tracked experiment
    if hints["experiments_for_product"] > 0:
        done[5] = True
    # Start (active / running / has a real start)
    if int(hints.get("experiments_started") or 0) > 0:
        done[6] = True
    # Evaluate
    if hints["experiments_evaluated"] > 0:
        done[7] = True
    # Close stale — done if we ran it, or orchestration says nothing is eligible to close.
    sc = int(hints.get("stale_close_executions") or 0)
    close_eligible: bool | None = None
    facts = state.get("eligibility_facts") if isinstance(state, dict) else None
    if isinstance(facts, dict) and "experiments_close_stale_eligible" in facts:
        close_eligible = bool(facts.get("experiments_close_stale_eligible"))
    if sc > 0:
        done[8] = True
    elif close_eligible is False:
        done[8] = True
    else:
        done[8] = False
    return done


def _current_stage_index(
    summary: dict[str, Any],
    state: dict[str, Any] | None,
    hints: dict[str, Any],
) -> int:
    na = summary.get("snapshot_next_action")
    action = str(na).strip() if isinstance(na, str) else "none"
    if action != "none":
        return ACTION_TO_STAGE.get(action, 0)

    ladder = _ladder_completed_mask(hints, state)
    # Idle: first stage not yet satisfied; if all satisfied, -1 (show whole path as done).
    if all(ladder):
        return -1
    for i, ok in enumerate(ladder):
        if not ok:
            return i
    return 8


def _friendly_compact_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for raw in lines:
        s = str(raw).strip()
        if not s:
            continue
        if re.match(r"^selected_product=[^\s]+$", s):
            continue
        for old, new in _COMPACT_LINE_FRIENDLY:
            if old in s:
                s = s.replace(old, new)
                break
        out.append(s.strip())
    return out


def _chips(summary: dict[str, Any], hints: dict[str, Any], state: dict[str, Any] | None) -> list[str]:
    chips: list[str] = []
    irp = summary.get("idea_refinement_posture")
    if isinstance(irp, dict):
        if irp.get("refinement_start_idea_eligible"):
            chips.append("Ideas ready for refinement")
        if irp.get("idea_refinement_session_present_non_terminal"):
            chips.append("Idea refinement in progress")
    if hints["proposals_present"]:
        n = hints.get("proposal_count")
        if isinstance(n, int) and n > 0:
            chips.append(f"Experiment proposals created ({n})")
        else:
            chips.append("Experiment proposals created")
    if hints["prioritization_present"]:
        chips.append("Experiments ranked")
    if hints["experiments_for_product"] > 0:
        n = int(hints["experiments_for_product"])
        chips.append(f"Experiment created ({n})" if n != 1 else "Experiment created")
    es = int(hints.get("experiments_started") or 0)
    if es > 0:
        chips.append(f"Experiment started ({es})" if es != 1 else "Experiment started")
    if hints["experiments_evaluated"] > 0:
        n = int(hints["experiments_evaluated"])
        chips.append(f"Experiment evaluated ({n})" if n != 1 else "Experiment evaluated")
    sc = int(hints.get("stale_close_executions") or 0)
    if sc > 0:
        chips.append(f"Stale experiment closed ({sc})" if sc != 1 else "Stale experiment closed")

    facts = state.get("eligibility_facts") if isinstance(state, dict) else None
    if isinstance(facts, dict):
        if facts.get("experiments_prioritize_eligible") and not hints["prioritization_present"]:
            chips.append("Ready to rank experiments")
        if facts.get("experiments_create_eligible") and hints["experiments_for_product"] == 0:
            chips.append("Ready to create an experiment from the ranking")
        if (
            facts.get("experiments_activate_eligible")
            and int(hints.get("experiments_started") or 0) == 0
            and hints["experiments_for_product"] > 0
        ):
            chips.append("Ready to start an experiment")
        if (
            facts.get("experiments_evaluate_eligible")
            and hints["experiments_evaluated"] == 0
            and int(hints.get("experiments_started") or 0) > 0
        ):
            chips.append("Ready to evaluate results")
        if facts.get("experiments_close_stale_eligible") and sc == 0:
            chips.append("Ready to close stale experiments")

    # de-dupe preserving order
    seen: set[str] = set()
    uniq: list[str] = []
    for c in chips:
        if c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq[:12]


def _strategy_context(root: Path, product_id: str) -> dict[str, Any] | None:
    """Read ``runs/strategy/latest/<product_id>.json`` when present and schema matches."""
    if not product_id.strip():
        return None
    raw = _load_json(root / "runs" / "strategy" / "latest" / f"{product_id}.json")
    if not raw or str(raw.get("schema") or "") != _SCHEMA_STRATEGY_SNAPSHOT:
        return None
    return {
        "posture": raw.get("posture"),
        "posture_raw": raw.get("posture_raw"),
        "skepticism_applied": bool(raw.get("skepticism_applied")),
        "skepticism_reason": raw.get("skepticism_reason"),
        "recommended_mode": raw.get("recommended_mode"),
    }


def _planning_context(root: Path, product_id: str) -> dict[str, Any] | None:
    """Read ``runs/planning/latest/<product_id>.json`` when present and schema matches."""
    if not product_id.strip():
        return None
    raw = _load_json(root / "runs" / "planning" / "latest" / f"{product_id}.json")
    if not raw or str(raw.get("schema") or "") != _SCHEMA_PLANNING_SNAPSHOT:
        return None
    ws = raw.get("priority_workstreams")
    ws_list = [str(x) for x in ws[:4]] if isinstance(ws, list) else []
    rac = raw.get("recommended_actions")
    n_ra = len(rac) if isinstance(rac, list) else 0
    return {
        "planning_mode": raw.get("planning_mode"),
        "posture": raw.get("posture"),
        "priority_workstreams": ws_list,
        "recommended_actions_count": n_ra,
    }


def _portfolio_context_for_product(root: Path, product_id: str) -> dict[str, Any]:
    """Read portfolio priorities; rank and reasons for this product + rank-1 context."""
    out: dict[str, Any] = {
        "present": False,
        "recommended_product_id": None,
        "recommended_next_action": None,
        "rank1_product_id": None,
        "rank1_reasons": [],
        "this_rank": None,
        "this_priority_score": None,
        "this_reasons": [],
    }
    if not product_id.strip():
        return out
    raw = _load_json(root / "runs" / "orchestration" / "latest" / "portfolio_priorities.json")
    if not raw or str(raw.get("schema") or "") != _SCHEMA_PORTFOLIO_PRIORITIES:
        return out
    out["present"] = True
    out["recommended_product_id"] = raw.get("recommended_product_id")
    out["recommended_next_action"] = raw.get("recommended_next_action")
    rows = raw.get("products")
    if not isinstance(rows, list):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        if int(r.get("rank") or 0) == 1:
            out["rank1_product_id"] = r.get("product_id")
            pr = r.get("priority_reasons")
            out["rank1_reasons"] = [str(x) for x in pr[:3]] if isinstance(pr, list) else []
            break
    for r in rows:
        if isinstance(r, dict) and str(r.get("product_id") or "") == product_id:
            out["this_rank"] = r.get("rank")
            out["this_priority_score"] = r.get("priority_score")
            pr = r.get("priority_reasons")
            out["this_reasons"] = [str(x) for x in pr[:4]] if isinstance(pr, list) else []
            break
    return out


def _portfolio_trends_context_for_product(root: Path, product_id: str) -> dict[str, Any]:
    """Read ``portfolio_priority_trends.json``; stability/churn plus optional row for this product."""
    out: dict[str, Any] = {
        "present": False,
        "portfolio_stability": None,
        "churn_summary": None,
        "operator_recommendations": [],
        "this_trend": None,
    }
    if not product_id.strip():
        return out
    raw = _load_json(root / "runs" / "orchestration" / "latest" / "portfolio_priority_trends.json")
    if not raw or str(raw.get("schema") or "") != _SCHEMA_PORTFOLIO_TRENDS:
        return out
    out["present"] = True
    out["portfolio_stability"] = raw.get("portfolio_stability")
    out["churn_summary"] = raw.get("churn_summary")
    ore = raw.get("operator_recommendations")
    out["operator_recommendations"] = [str(x) for x in ore] if isinstance(ore, list) else []
    rows = raw.get("products") or []
    if isinstance(rows, list):
        for r in rows:
            if isinstance(r, dict) and str(r.get("product_id") or "") == product_id:
                out["this_trend"] = r
                break
    return out


def _html_portfolio_trends_note(product: str, trends_ctx: dict[str, Any]) -> str:
    """One compact card: portfolio stability + this product’s trend flags (not a dashboard)."""
    if not product or not trends_ctx.get("present"):
        return ""
    stab = trends_ctx.get("portfolio_stability")
    stab_plain = {
        "stable": "steady — same top focus across recent snapshots",
        "shifting": "moving — top priority changed at least once",
        "volatile": "churning — top priority changes often",
    }.get(str(stab or "").lower(), str(stab) if stab else "unknown")
    parts: list[str] = [
        "<h2>Portfolio trend</h2>",
        f"<p class='tiny'>Overall: <strong>{_html_escape(stab_plain)}</strong></p>",
    ]
    churn = trends_ctx.get("churn_summary")
    if isinstance(churn, str) and churn.strip():
        parts.append(f"<p class='tiny muted'>{_html_escape(_trim(churn, 240))}</p>")
    tr = trends_ctx.get("this_trend")
    if isinstance(tr, dict):
        bits: list[str] = []
        if tr.get("rising"):
            bits.append("rising in urgency vs recent average")
        elif tr.get("falling"):
            bits.append("slipping vs recent average")
        elif tr.get("stable"):
            bits.append("similar urgency vs recent average")
        tf = tr.get("times_ranked_first")
        if isinstance(tf, int) and tf >= 2:
            bits.append(f"held rank #1 in {tf} recent snapshots")
        if bits:
            parts.append(
                "<p class='tiny'><strong>" + _html_escape(product) + "</strong> — "
                + _html_escape("; ".join(bits))
                + "</p>"
            )
    recs = trends_ctx.get("operator_recommendations") or []
    if recs:
        lis = "".join(f"<li>{_html_escape(_trim(str(x), 220))}</li>" for x in recs[:5])
        parts.append("<p class='tiny'><strong>Guidance</strong></p><ul class='bullets tight'>" + lis + "</ul>")
    inner = "".join(parts)
    return (
        f"""<div class="card focus-card">{inner}"""
        f"""<p class="tiny muted">From <code>runs/orchestration/latest/portfolio_priority_trends.json</code> when present.</p></div>"""
    )


def _selection_highlights(friendly_lines: list[str]) -> list[str]:
    """Pull fairness / selection lines from compact summary (already plain-language)."""
    out: list[str] = []
    for s in friendly_lines:
        sl = s.lower()
        if any(
            k in sl
            for k in (
                "fairness",
                "why this product",
                "focused product",
                "rotation",
                "would have picked",
            )
        ):
            out.append(s)
    return out[:4]


def _posture_label(posture: Any) -> str:
    if not isinstance(posture, str) or not posture.strip():
        return "—"
    p = posture.strip().lower()
    return _POSTURE_PLAIN.get(p, posture.strip())


def _planning_label(mode: Any) -> str:
    if not isinstance(mode, str) or not mode.strip():
        return "—"
    m = mode.strip().lower()
    return _PLANNING_PLAIN.get(m, mode.strip())


def _html_why_now_panel(
    product: str,
    portfolio_ctx: dict[str, Any],
    friendly_lines: list[str],
) -> str:
    """Compact 'Why this product? · Why now?' from portfolio + selection lines."""
    if not product:
        return ""
    highlights = _selection_highlights(friendly_lines)
    paras: list[str] = []
    if portfolio_ctx.get("present"):
        rec = portfolio_ctx.get("recommended_product_id")
        na = portfolio_ctx.get("recommended_next_action")
        tr = portfolio_ctx.get("this_rank")
        rec_s = str(rec).strip() if rec is not None else ""
        if rec_s == product:
            paras.append(
                "<p><strong>This product is the portfolio’s top focus right now.</strong> "
                "The system ranks attention across products; you’re viewing the one it would nudge first.</p>"
            )
            rs = portfolio_ctx.get("this_reasons") or []
            if rs:
                lis = "".join(f"<li>{_html_escape(_trim(str(x), 280))}</li>" for x in rs[:2])
                paras.append(f"<p class='tiny'>Signals behind that rank:</p><ul class='bullets tight'>{lis}</ul>")
        elif rec_s:
            paras.append(
                f"<p>Portfolio attention is currently centered on <strong>{_html_escape(rec_s)}</strong> "
                f"(next step there: <code>{_html_escape(str(na or '—'))}</code>). "
                f"You’re viewing <strong>{_html_escape(product)}</strong> for detail.</p>"
            )
        if isinstance(tr, int) and tr > 0 and not (rec_s == product and tr == 1):
            paras.append(
                f"<p class='rank-badge'>In the latest portfolio ranking: <strong>#{tr}</strong></p>"
            )
    elif highlights:
        paras.append(
            "<p class='tiny'>No <code>portfolio_priorities.json</code> found — fairness/selection notes below are from the operator summary only.</p>"
        )

    if highlights:
        lis = "".join(f"<li>{_html_escape(x)}</li>" for x in highlights)
        paras.append(f"<ul class='bullets tight'>{lis}</ul>")

    if not paras:
        return ""

    inner = "".join(paras)
    return f"""<div class="card focus-card">
      <h2>Why this product · Why now</h2>
      {inner}
      <p class="tiny muted">From operator summary lines and, when present, <code>runs/orchestration/latest/portfolio_priorities.json</code>.</p>
    </div>"""


def _html_strategy_planning_panel(
    strategy: dict[str, Any] | None,
    planning: dict[str, Any] | None,
) -> str:
    if strategy is None and planning is None:
        return ""
    rows: list[str] = []
    if strategy is not None:
        posture = _posture_label(strategy.get("posture"))
        sk = strategy.get("skepticism_applied")
        sk_txt = "Yes" if sk else "No"
        reason = strategy.get("skepticism_reason")
        if sk and isinstance(reason, str) and reason.strip():
            sk_txt += f" — {_html_escape(_trim(reason, 120))}"
        rows.append(
            f"<p><span class='badge strat'>Strategy</span> Posture: <strong>{_html_escape(posture)}</strong> · "
            f"Skepticism applied: {sk_txt}</p>"
        )
    if planning is not None:
        mode = _planning_label(planning.get("planning_mode"))
        n = planning.get("recommended_actions_count")
        ws = planning.get("priority_workstreams") or []
        ws_bit = ""
        if ws:
            ws_bit = " · Themes: " + _html_escape(", ".join(ws[:3]))
        rows.append(
            f"<p><span class='badge plan'>Planning</span> Mode: <strong>{_html_escape(mode)}</strong> · "
            f"Suggested next steps on file: {int(n) if isinstance(n, int) else 0}{ws_bit}</p>"
        )
    return f"""<div class="card">
      <h2>Strategy &amp; planning (from latest files)</h2>
      {"".join(rows)}
      <p class="tiny muted">Read from <code>runs/strategy/latest/</code> and <code>runs/planning/latest/</code> when they exist.</p>
    </div>"""


def _html_recent_activity_panel(
    hints: dict[str, Any],
    portfolio_ctx: dict[str, Any],
    strategy: dict[str, Any] | None,
    planning: dict[str, Any] | None,
    product: str,
) -> str:
    """Concise 'what changed / what to notice' without raw JSON."""
    lines: list[str] = []
    la = hints.get("latest_evaluation_at")
    if isinstance(la, str) and la.strip():
        lines.append(f"Latest experiment evaluation on record: <strong>{_html_escape(_human_date_short(la))}</strong>")
    pc = hints.get("proposal_count")
    prop = hints.get("proposals_present")
    if prop:
        if isinstance(pc, int) and pc > 0:
            lines.append(f"Experiment proposals saved: <strong>{pc}</strong>")
        else:
            lines.append("Experiment proposals saved on disk")
    if hints.get("prioritization_present"):
        lines.append("Experiments have been ranked (prioritization file present)")
    ex_n = int(hints.get("experiments_for_product") or 0)
    if ex_n:
        lines.append(f"Tracked experiments for this product: <strong>{ex_n}</strong>")
    ev_n = int(hints.get("experiments_evaluated") or 0)
    if ev_n:
        lines.append(f"Experiments with at least one evaluation: <strong>{ev_n}</strong>")
    if portfolio_ctx.get("present") and product:
        tr = portfolio_ctx.get("this_rank")
        rec = portfolio_ctx.get("recommended_product_id")
        if rec == product and isinstance(tr, int) and tr == 1:
            lines.append("This product matches the portfolio’s #1 slot — good place to focus first.")
        elif isinstance(tr, int) and tr > 1 and rec and rec != product:
            lines.append(
                f"Not the portfolio #1 pick (#{tr} here); check the &quot;Why now&quot; box for the top product."
            )
    if strategy is not None:
        lines.append(f"Strategy snapshot says posture is <strong>{_html_escape(_posture_label(strategy.get('posture')))}</strong>")
    if planning is not None:
        lines.append(f"Planning snapshot says mode is <strong>{_html_escape(_planning_label(planning.get('planning_mode')))}</strong>")

    if not lines:
        return "<p class='sub' style='margin:0'>No extra “recent change” signals beyond the chips above.</p>"
    lis = "".join(f"<li>{x}</li>" for x in lines)
    return f"<ul class='bullets activity'>{lis}</ul>"


def _render_page(
    root: Path,
    summary: dict[str, Any],
    state: dict[str, Any] | None,
    *,
    has_operator_summary: bool,
    phase2_overlay: dict[str, Any] | None,
) -> str:
    pid = summary.get("selected_product_id")
    product = str(pid).strip() if pid is not None else ""
    if not has_operator_summary:
        product_display = "Orchestration"
    elif product:
        product_display = product.replace("_", " ")
    else:
        product_display = "Selected product"

    state_eff: dict[str, Any] | None = dict(state) if isinstance(state, dict) else None
    if phase2_overlay and (state_eff is None or not state_eff.get("phase2_posture")):
        if state_eff is None:
            state_eff = {}
        state_eff["phase2_posture"] = phase2_overlay

    hints = (
        _gather_artifact_hints(root, product, summary)
        if product
        else {
            "signals_present": False,
            "findings_present": False,
            "decisions_present": False,
            "ideas_latest_for_product": False,
            "proposals_present": False,
            "proposal_count": None,
            "prioritization_present": False,
            "experiments_for_product": 0,
            "experiments_started": 0,
            "experiments_evaluated": 0,
            "latest_evaluation_at": None,
            "stale_close_executions": 0,
        }
    )

    portfolio_ctx = _portfolio_context_for_product(root, product) if product else {}
    trends_ctx = _portfolio_trends_context_for_product(root, product) if product else {}
    strat_ctx = _strategy_context(root, product) if product else None
    plan_ctx = _planning_context(root, product) if product else None

    friendly_preview: list[str] = []
    lines = summary.get("compact_summary_lines")
    if isinstance(lines, list):
        friendly_preview = _friendly_compact_lines([str(x) for x in lines])

    why_now_html = ""
    if product and has_operator_summary:
        why_now_html = _html_why_now_panel(product, portfolio_ctx, friendly_preview)
    trends_html = _html_portfolio_trends_note(product, trends_ctx) if product else ""
    strat_plan_html = _html_strategy_planning_panel(strat_ctx, plan_ctx) if product else ""

    na = summary.get("snapshot_next_action")
    action = str(na).strip() if isinstance(na, str) else "none"
    cur_idx = _current_stage_index(summary, state_eff, hints)
    if action == "none" and cur_idx < 0:
        headline = (
            "Up to date — proposals through evaluation, plus stale cleanup when needed, "
            "line up with what is on disk"
        )
    else:
        headline = ACTION_LABELS.get(action, "Moving this product through the loop")

    # Pipeline cells: done / current / upcoming (cur_idx from next action or first open stage when idle)
    cells: list[str] = []
    for i, (_key, label) in enumerate(STAGES):
        if cur_idx < 0:
            cls = "cell done"
        elif i < cur_idx:
            cls = "cell done"
        elif i == cur_idx:
            cls = "cell current"
        else:
            cls = "cell upcoming"
        cells.append(f"<div class='{cls}'><span class='dot'></span>{_html_escape(label)}</div>")

    arrow_html = '<span class="arrow">→</span>'
    pipeline_row = "".join(
        cells[i] + (arrow_html if i < len(cells) - 1 else "") for i in range(len(cells))
    )

    status_reason = ""
    if state_eff:
        sr = state_eff.get("orchestration_status_reason")
        if isinstance(sr, str) and sr.strip():
            status_reason = _trim(_stakeholder_reason(sr), 260)
    if not status_reason:
        tr = summary.get("transition_reason")
        if isinstance(tr, str) and tr.strip():
            status_reason = _trim(_stakeholder_reason(tr), 260)

    bullet_html = ""
    if friendly_preview:
        lis = "".join(f"<li>{_html_escape(x)}</li>" for x in friendly_preview)
        bullet_html = f"<ul class='bullets'>{lis}</ul>"

    chip_list = _chips(summary, hints, state_eff) if product else []
    chips_html = ""
    if chip_list:
        chips_html = '<div class="chips">' + "".join(
            f'<span class="chip">{_html_escape(c)}</span>' for c in chip_list
        ) + "</div>"

    reason_block = ""
    if status_reason:
        reason_block = f"<p class='reason'>{_html_escape(status_reason)}</p>"

    freshness_html = _freshness_breakdown_html(root, state_eff) if product else ""

    sum_p = _operator_summary_path(root)
    rel = "runs/orchestration/latest/operator_summary.json"
    missing = ""
    if not has_operator_summary:
        missing = f"""<div class="card callout">
      <h2 class="callout-title">No summary file yet</h2>
      <p>This page reads one file: <code>{_html_escape(rel)}</code> inside your Argus repo.</p>
      <p>Argus writes it when you run <strong>argus orchestration batch-advance</strong> (multi-product orchestration). Refresh this page after that command finishes.</p>
      <p class="mono-path">{_html_escape(str(sum_p))}</p>
    </div>"""

    legend = (
        "<p class='legend'>Green = that part of the journey is already in a good place · "
        "Blue = where attention is now · Gray = still ahead</p>"
    )

    muted = _muted_detail_line(summary, hints)
    muted_html = f'<p class="detail">{_html_escape(muted)}</p>' if muted else ""

    recent_activity_html = ""
    if product:
        recent_activity_html = _html_recent_activity_panel(
            hints, portfolio_ctx, strat_ctx, plan_ctx, product
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>Argus — { _html_escape(product_display) }</title>
  <style>
    :root {{
      --bg: #0f1419;
      --card: #1a2332;
      --text: #e8eef5;
      --muted: #8b9cb3;
      --done: #3d9a6d;
      --current: #5b8cff;
      --up: #3a4556;
      --arrow: #4a5568;
      --chip: #2a3f5c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      font-family: "Segoe UI", system-ui, sans-serif;
      background: var(--bg);
      color: var(--text);
      margin: 0;
      min-height: 100vh;
      line-height: 1.45;
    }}
    .wrap {{
      max-width: 72rem;
      margin: 0 auto;
      padding: 1.75rem 1.25rem 3rem;
    }}
    h1 {{
      font-size: 1.35rem;
      font-weight: 600;
      margin: 0 0 0.25rem;
      letter-spacing: 0.02em;
    }}
    .sub {{
      color: var(--muted);
      font-size: 0.95rem;
      margin-bottom: 1.5rem;
    }}
    .card {{
      background: var(--card);
      border-radius: 12px;
      padding: 1.25rem 1.35rem;
      margin-bottom: 1.25rem;
      border: 1px solid #2a3544;
    }}
    .headline {{
      font-size: 1.05rem;
      margin: 0 0 0.75rem;
      color: #cfe0ff;
    }}
    .legend {{
      font-size: 0.78rem;
      color: var(--muted);
      margin: 0 0 0.85rem;
    }}
    .pipeline {{
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 0.35rem 0.2rem;
      margin-bottom: 0.25rem;
    }}
    .cell {{
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      padding: 0.45rem 0.65rem;
      border-radius: 999px;
      font-size: 0.78rem;
      white-space: nowrap;
    }}
    .cell.done {{ background: rgba(61, 154, 109, 0.2); color: #9ed9b8; border: 1px solid var(--done); }}
    .cell.current {{ background: rgba(91, 140, 255, 0.25); color: #b8d0ff; border: 1px solid var(--current); font-weight: 600; }}
    .cell.upcoming {{ background: #243041; color: var(--muted); border: 1px solid var(--up); }}
    .dot {{
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: currentColor;
      opacity: 0.85;
    }}
    .arrow {{ color: var(--arrow); font-size: 0.85rem; padding: 0 0.1rem; user-select: none; }}
    h2 {{
      font-size: 0.75rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin: 0 0 0.5rem;
      font-weight: 600;
    }}
    .chips {{
      display: flex;
      flex-wrap: wrap;
      gap: 0.4rem;
      margin-bottom: 0.85rem;
    }}
    .chip {{
      font-size: 0.8rem;
      background: var(--chip);
      color: #d0dce8;
      padding: 0.28rem 0.6rem;
      border-radius: 6px;
      border: 1px solid #3d5566;
    }}
    .bullets {{
      margin: 0;
      padding-left: 1.15rem;
      color: #c5d4e5;
      font-size: 0.92rem;
    }}
    .bullets li {{ margin-bottom: 0.35rem; }}
    .reason {{
      margin: 0;
      font-size: 0.95rem;
      color: #b8c9db;
    }}
    .callout {{ border-color: #3d4f66; background: #152028; }}
    .callout-title {{ margin-top: 0; color: #ffb86b; font-size: 0.95rem; }}
    code {{ font-size: 0.88em; background: #0f1824; padding: 0.12rem 0.35rem; border-radius: 4px; }}
    .mono-path {{ font-family: ui-monospace, monospace; font-size: 0.8rem; color: var(--muted); word-break: break-all; margin-bottom: 0; }}
    .detail {{
      font-size: 0.72rem;
      color: #5c6b7e;
      margin-top: 0.75rem;
      word-break: break-word;
    }}
    .focus-card {{
      border-color: #3d5a8a;
      background: linear-gradient(165deg, #1a2838 0%, #1a2332 100%);
    }}
    .focus-card h2 {{ color: #a8c4ff; }}
    .rank-badge {{
      font-size: 0.88rem;
      color: #c5d8f0;
      margin: 0.35rem 0;
    }}
    .tiny {{ font-size: 0.78rem; margin: 0.25rem 0 0; }}
    .bullets.tight {{ font-size: 0.85rem; }}
    .bullets.activity {{ margin-top: 0.5rem; }}
    .badge {{
      display: inline-block;
      font-size: 0.68rem;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 0.12rem 0.4rem;
      border-radius: 4px;
      margin-right: 0.35rem;
      vertical-align: middle;
    }}
    .badge.strat {{ background: #2d3f5c; color: #b8d0ff; border: 1px solid #4a6a9a; }}
    .badge.plan {{ background: #2d4a4f; color: #b8f0e0; border: 1px solid #4a9a8a; }}
    h3.section {{
      font-size: 0.72rem;
      text-transform: uppercase;
      letter-spacing: 0.07em;
      color: var(--muted);
      margin: 1rem 0 0.4rem;
      font-weight: 600;
    }}
    footer {{
      margin-top: 2rem;
      font-size: 0.75rem;
      color: var(--muted);
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>{_html_escape(product_display)}</h1>
    <p class="sub">{"From signals to ideas, experiments, and measured results — in plain language" if has_operator_summary else "Hook up your Argus repo, then run a batch advance — details below"}</p>
    {missing}
    {why_now_html}
    {trends_html}
    {strat_plan_html}
    <div class="card pipeline-card">
      <p class="headline">{_html_escape(headline)}</p>
      {legend}
      <div class="pipeline">{pipeline_row}</div>
    </div>
    {freshness_html}
    <div class="card">
      <h2>What’s on disk · what changed recently</h2>
      {chips_html if chips_html else "<p class='sub' style='margin:0'>When proposals, rankings, experiments, or evaluations are saved, quick badges appear here.</p>"}
      <h3 class="section">What to notice first</h3>
      {recent_activity_html if product else "<p class='sub' style='margin:0'>Select a product in operator summary to see activity hints.</p>"}
      <h3 class="section">Recent notes (from operator summary)</h3>
      {bullet_html if bullet_html else ("<p class='sub' style='margin:0'>Short notes will appear here after a batch advance.</p>" if not has_operator_summary else "<p class='sub' style='margin:0'>No extra notes for this snapshot.</p>")}
      <h3 class="section">Status detail</h3>
      {reason_block if reason_block else ("<p class='sub' style='margin:0'>A plain-language explanation appears when orchestration state is linked.</p>" if not has_operator_summary else "<p class='sub' style='margin:0'>No extra explanation line for this snapshot.</p>")}
      {muted_html}
    </div>
    <footer>Read-only · refresh after orchestration updates</footer>
  </div>
</body>
</html>
"""


def _repo_root_on_syspath(root: Path) -> None:
    """Allow importing ``argus.*`` helpers when the viewer is run as a script."""
    s = str(root.resolve())
    if s not in sys.path:
        sys.path.insert(0, s)


def _freshness_breakdown_html(root: Path, state: dict[str, Any] | None) -> str:
    """
    When status is ``stale_refresh_needed``, explain signals vs temporal vs audit from ``eligibility_facts``.
    Read-only; does not change orchestration evaluation.
    """
    if not state:
        return ""
    if str(state.get("orchestration_status") or "").strip() != "stale_refresh_needed":
        return ""
    _repo_root_on_syspath(root)
    try:
        from argus.orchestrator.freshness_plain_language import freshness_explanation_lines
    except ImportError:
        return ""
    facts = state.get("eligibility_facts")
    lines = freshness_explanation_lines(
        facts if isinstance(facts, dict) else None,
        orchestration_status=str(state.get("orchestration_status") or "").strip() or None,
    )
    if not lines:
        return ""
    lis = "".join(f"<li>{_html_escape(x)}</li>" for x in lines)
    return f"""<div class="card freshness">
      <h2>Freshness detail (why status can stay “stale”)</h2>
      <p class="sub">Argus tracks separate dimensions. Collecting signals updates one clock; temporal snapshots and audit bundles have their own.</p>
      <ul class="bullets">{lis}</ul>
    </div>"""


def _stakeholder_reason(text: str) -> str:
    """Light touch: strip codes and paths; keep meaning; still one line."""
    t = " ".join(text.split())
    t = re.sub(r"\b(?:RC_[A-Z0-9_]+)\b", "", t)
    t = re.sub(r"\bruns/[^\s]+", "", t)
    t = re.sub(r"\s+", " ", t).strip(" ,;.")
    return t


class Eli5Handler(BaseHTTPRequestHandler):
    repo_root: Path

    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/", "/index.html"):
            self.send_error(404, "Not found")
            return

        root = self.repo_root
        sum_p = _operator_summary_path(root)
        has_summary = sum_p.is_file()
        summary = _load_json(sum_p) or {}
        pid = str(summary.get("selected_product_id") or "").strip()
        summary = _merge_summary_from_fleet(summary, root, pid)
        state: dict[str, Any] | None = None
        st_path = _resolve_state_path(root, summary)
        if st_path and st_path.is_file():
            state = _load_json(st_path)

        phase2_overlay: dict[str, Any] | None = None
        if pid and (not state or not (isinstance(state, dict) and state.get("phase2_posture"))):
            phase2_overlay = _phase2_from_fleet(root, pid)

        html = _render_page(
            root,
            summary,
            state,
            has_operator_summary=has_summary,
            phase2_overlay=phase2_overlay,
        )
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    p = argparse.ArgumentParser(description="ELI5 orchestration viewer (read-only)")
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Argus repo root (default: $ARGUS_REPO_ROOT, else parent of tools/, else cwd)",
    )
    p.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address (default: 127.0.0.1)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ARGUS_ELI5_PORT", "9147")),
        help="Port (default: 9147, or ARGUS_ELI5_PORT)",
    )
    args = p.parse_args()
    root = _repo_root(args.repo_root) if args.repo_root is not None else infer_repo_root()

    Eli5Handler.repo_root = root

    httpd = ThreadingHTTPServer((args.host, args.port), Eli5Handler)
    sum_p = _operator_summary_path(root)
    sum_status = "found" if sum_p.is_file() else "missing — run: argus orchestration batch-advance"
    print(f"Argus ELI5 viewer — http://{args.host}:{args.port}/", file=sys.stderr)
    print(f"Repo root: {root}", file=sys.stderr)
    print(f"operator_summary.json: {sum_status}", file=sys.stderr)
    print("Ctrl+C to stop.", file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
