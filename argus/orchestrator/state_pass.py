"""Write orchestration state artifact under ``runs/orchestration/``."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.decision.persistence import latest_product_path as _decisions_latest_path
from argus.escalation.task_state import tasks_latest_path
from argus.findings.persistence import latest_path as _findings_latest_path
from argus.orchestrator.artifact_paths import (
    experiments_proposals_latest_product_path,
    ideas_bundle_latest_path,
    orchestration_advancement_path,
    orchestration_batch_advancement_path,
    orchestration_fleet_governance_rollup_path,
    orchestration_index_path,
    orchestration_latest_path,
    orchestration_operator_summary_path,
    orchestration_progression_generation_path,
    orchestration_progression_latest_path,
)
from argus.orchestrator.artifact_snapshot import parse_iso_timestamp
from argus.orchestrator.eligibility import (
    RC_ORCH_FEEDBACK_REPEATED_FAILURES,
    RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED,
    evaluate_product_orchestration,
)
from argus.orchestrator.operator_snapshot import write_operator_snapshot_artifacts
from argus.orchestrator.portfolio_priorities import (
    build_portfolio_priorities,
    write_portfolio_priorities_payload,
)
from argus.orchestrator.state_models import (
    ACTION_DECISIONS_GENERATE,
    ACTION_ESCALATION_PACKET_GENERATE,
    ACTION_EXPERIMENTS_PROPOSE,
    ACTION_FINDINGS_GENERATE,
    ACTION_IDEAS_GENERATE,
    ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
    ACTION_SIGNALS_COLLECT,
    ORCHESTRATION_FLEET_GOVERNANCE_ROLLUP_SCHEMA,
    ORCHESTRATION_INDEX_SCHEMA,
    ORCHESTRATION_OPERATOR_SUMMARY_SCHEMA,
    ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA,
)
from argus.orchestrator.task_artifact import orchestration_task_path, sync_orchestration_task
from argus.signals.persistence import latest_path as _signals_latest_path


def _repo_rel(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _eligibility_facts_dict(payload: dict[str, Any]) -> dict[str, Any]:
    facts = payload.get("eligibility_facts")
    return facts if isinstance(facts, dict) else {}


def _refinement_idea_session_compact(payload: dict[str, Any]) -> dict[str, Any] | None:
    art = payload.get("artifacts")
    if not isinstance(art, dict):
        return None
    ref = art.get("refinement")
    if not isinstance(ref, dict):
        return None
    ib = ref.get("idea")
    if not isinstance(ib, dict) or not ib.get("session_id"):
        return None
    return {
        "phase": ib.get("phase"),
        "session_id": ib.get("session_id"),
        "status": ib.get("status"),
        "current_round": ib.get("current_round"),
    }


def _rollup_idea_refinement_posture(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Compact idea-refinement corridor view for fleet rollup / operator visibility.

    Uses ``eligibility_facts`` and ``artifacts.refinement.idea`` from evaluated orchestration state.
    """
    facts = _eligibility_facts_dict(payload)
    na = str(payload.get("next_action") or "none")
    idea_compact = _refinement_idea_session_compact(payload)
    return {
        "refinement_start_idea_eligible": bool(facts.get("refinement_start_idea_eligible")),
        "idea_refinement_session_present_non_terminal": bool(
            facts.get("idea_refinement_session_present_non_terminal")
        ),
        "refinement_submit_reviews_in_eligible": bool(facts.get("refinement_submit_reviews_in_eligible")),
        "next_action_is_refinement_submit_reviews_in": na == ACTION_REFINEMENT_SUBMIT_REVIEWS_IN,
        "refinement_run_eligible_for_idea_session": bool(facts.get("refinement_run_eligible_for_idea_session")),
        "idea_session": idea_compact,
    }


def _rollup_experiment_posture(
    repo_root: Path,
    payload: dict[str, Any],
    eligible_action_ids: set[str],
    product_id: str,
) -> dict[str, Any]:
    """
    Compact experiment-proposal corridor for fleet rollup / operator visibility.

    Uses ``eligibility_facts.experiments_propose_eligible`` when set; otherwise falls back to
    whether ``experiments_propose`` appears in evaluated ``eligible_action_ids``. Artifact presence
    and optional ``proposal_count`` come from ``runs/experiments/proposals/latest/<product_id>.json``.
    """
    root = Path(repo_root).resolve()
    facts = _eligibility_facts_dict(payload)

    v = facts.get("experiments_propose_eligible")
    if v is not None:
        exp_eligible = bool(v)
    else:
        exp_eligible = ACTION_EXPERIMENTS_PROPOSE in eligible_action_ids

    prop_p = experiments_proposals_latest_product_path(root, product_id)
    present = prop_p.is_file()
    rel = _repo_rel(root, prop_p) if present else None

    proposal_count: int | None = None
    if present:
        try:
            raw = json.loads(prop_p.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                pr = raw.get("proposals")
                if isinstance(pr, list):
                    proposal_count = len(pr)
        except (OSError, json.JSONDecodeError, UnicodeError):
            proposal_count = None

    out: dict[str, Any] = {
        "experiments_propose_eligible": exp_eligible,
        "experiment_proposals_present": present,
        "artifact_links": {
            "experiments_latest_repo_relative": rel,
        },
    }
    if proposal_count is not None:
        out["proposal_count"] = proposal_count
    return out


def build_operator_summary_payload(repo_root: Path, batch_body: dict[str, Any]) -> dict[str, Any]:
    """
    Derive a compact summary from ``batch_advancement.json`` body (no recomputation of eligibility).
    """
    root = Path(repo_root).resolve()
    idx_p = orchestration_index_path(root)
    batch_p = orchestration_batch_advancement_path(root)
    links: dict[str, Any] = {
        "orchestration_index_repo_relative": _repo_rel(root, idx_p) if idx_p.is_file() else None,
        "batch_advancement_repo_relative": _repo_rel(root, batch_p),
    }

    sel = batch_body.get("selected_product_id")
    if sel is not None and str(sel).strip():
        pid = str(sel).strip()
        st_p = orchestration_latest_path(root, pid)
        links["orchestration_state_repo_relative"] = _repo_rel(root, st_p) if st_p.is_file() else None
        adv_rel = batch_body.get("advancement_artifact_path_repo_relative")
        if isinstance(adv_rel, str) and adv_rel.strip():
            links["advancement_artifact_repo_relative"] = adv_rel.strip()
        else:
            links["advancement_artifact_repo_relative"] = None
    else:
        links["orchestration_state_repo_relative"] = None
        links["advancement_artifact_repo_relative"] = None

    adv = batch_body.get("advancement_payload")
    if not isinstance(adv, dict):
        adv = {}

    fairness = batch_body.get("batch_advancement_fairness")
    fairness_applied = False
    fairness_reason: str | None = None
    would_have: str | None = None
    if isinstance(fairness, dict):
        fairness_applied = bool(
            fairness.get("skipped_repeat_top_for_fairness")
            or fairness.get("skipped_top_due_to_recent_dominance")
        )
        fr = str(fairness.get("selection_reason") or "").strip()
        fairness_reason = fr if fr else None
        w = fairness.get("would_have_selected_without_fairness")
        if w is not None and str(w).strip():
            would_have = str(w).strip()

    selected_action = adv.get("selected_action")
    snap_next = adv.get("snapshot_next_action")
    tr = str(adv.get("transition_reason") or "").strip()

    execution: dict[str, Any] = {}
    if isinstance(adv, dict):
        if "action_status" in adv:
            execution["action_status"] = adv.get("action_status")
        ex_at = adv.get("executed_at_utc")
        if ex_at:
            execution["executed_at_utc"] = ex_at
        ex_err = adv.get("execution_error")
        if ex_err:
            execution["execution_error"] = str(ex_err)

    lines: list[str] = []
    idea_refinement_posture_selected: dict[str, Any] | None = None
    experiment_posture_selected: dict[str, Any] | None = None
    br = batch_body.get("reason")
    if br == "no_ranked_products":
        lines.append("batch: no ranked products — advance skipped")
    elif sel is not None and str(sel).strip():
        pid = str(sel).strip()
        lines.append(f"selected_product={pid}")
        if fairness_applied and would_have:
            lines.append(f"fairness: rotated (would_have_selected_without_fairness={would_have!r})")
        if fairness_reason:
            lines.append(f"selection={fairness_reason}")
        if tr:
            lines.append(f"transition={tr[:400]}")
        st = execution.get("action_status")
        if st is not None:
            lines.append(f"action_status={st}")
        if execution.get("executed_at_utc"):
            lines.append("execution_recorded=true")
        st_p = orchestration_latest_path(root, pid)
        if st_p.is_file():
            try:
                ost = json.loads(st_p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, UnicodeError):
                ost = None
            if isinstance(ost, dict):
                irp = _rollup_idea_refinement_posture(ost)
                idea_refinement_posture_selected = irp
                if irp.get("refinement_start_idea_eligible"):
                    lines.append("idea_refinement: refinement_start_idea_eligible")
                if irp.get("idea_refinement_session_present_non_terminal"):
                    lines.append("idea_refinement: active_non_terminal_idea_session")
                if irp.get("next_action_is_refinement_submit_reviews_in"):
                    lines.append("idea_refinement: next=refinement_submit_reviews_in")
                if irp.get("refinement_submit_reviews_in_eligible"):
                    lines.append("idea_refinement: refinement_submit_reviews_in_eligible")
                if irp.get("refinement_run_eligible_for_idea_session"):
                    lines.append("idea_refinement: refinement_run_eligible_for_idea_session")

                elig_os = ost.get("eligible_actions") or []
                eids_os: list[str] = []
                if isinstance(elig_os, list):
                    for row in elig_os:
                        if isinstance(row, dict) and row.get("action_id"):
                            eids_os.append(str(row["action_id"]))
                    eids_os = sorted(set(eids_os))
                exp_p = _rollup_experiment_posture(root, ost, set(eids_os), pid)
                experiment_posture_selected = exp_p
                if exp_p.get("experiments_propose_eligible"):
                    lines.append("experiments: propose_ready")
                if exp_p.get("experiment_proposals_present"):
                    lines.append("experiments: proposals_present")

    return {
        "schema": ORCHESTRATION_OPERATOR_SUMMARY_SCHEMA,
        "evaluated_at_utc": str(batch_body.get("evaluated_at_utc") or ""),
        "batch_reason": br if br in ("no_ranked_products",) else None,
        "selected_product_id": str(sel).strip() if sel is not None and str(sel).strip() else None,
        "selected_action_id": selected_action if selected_action is not None else None,
        "snapshot_next_action": str(snap_next) if isinstance(snap_next, str) else None,
        "fairness_applied": fairness_applied,
        "fairness_selection_reason": fairness_reason,
        "fairness_would_have_selected_without_fairness": would_have,
        "transition_reason": tr if tr else None,
        "execution": execution,
        "compact_summary_lines": lines,
        "idea_refinement_posture": idea_refinement_posture_selected,
        "experiment_posture": experiment_posture_selected,
        "artifact_links": links,
    }


def write_operator_summary_latest_batch(repo_root: Path, batch_body: dict[str, Any]) -> Path:
    """Persist :func:`build_operator_summary_payload` next to other ``latest/`` orchestration artifacts."""
    root = Path(repo_root).resolve()
    payload = build_operator_summary_payload(root, batch_body)
    p = orchestration_operator_summary_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return p


def attach_batch_advancement_links_to_index_payload(
    repo_root: Path,
    index_payload: dict[str, object],
) -> dict[str, object]:
    """
    Additive index fields: repo-relative path to ``batch_advancement.json`` when it exists,
    and the selected advancement path stored in that artifact (if any).
    """
    root = repo_root.resolve()
    batch_p = orchestration_batch_advancement_path(root)
    out: dict[str, object] = dict(index_payload)
    if not batch_p.is_file():
        out["batch_advancement_artifact_path_repo_relative"] = None
        out["batch_advancement_selected_advancement_path_repo_relative"] = None
        return out
    try:
        rel_batch = str(batch_p.relative_to(root))
    except ValueError:
        rel_batch = str(batch_p)
    out["batch_advancement_artifact_path_repo_relative"] = rel_batch
    sel_adv: str | None = None
    try:
        raw = json.loads(batch_p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            v = raw.get("advancement_artifact_path_repo_relative")
            if v is not None:
                s = str(v).strip()
                sel_adv = s if s else None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        pass
    out["batch_advancement_selected_advancement_path_repo_relative"] = sel_adv
    return out


def _compact_orchestration_reason_for_rollup(reason: Any, *, max_len: int = 280) -> str:
    t = str(reason or "").strip()
    if len(t) <= max_len:
        return t
    return t[: max_len - 3] + "..."


def _fleet_governance_top_links(root: Path) -> dict[str, str | None]:
    """Repo-relative pointers to batch-level artifacts (optional files may be absent)."""
    r = root.resolve()
    idx = orchestration_index_path(r)
    batch = orchestration_batch_advancement_path(r)
    op_sum = orchestration_operator_summary_path(r)
    rollup = orchestration_fleet_governance_rollup_path(r)
    out: dict[str, str | None] = {
        "orchestration_index_repo_relative": _repo_rel(r, idx) if idx.is_file() else None,
        "batch_advancement_repo_relative": _repo_rel(r, batch) if batch.is_file() else None,
        "operator_summary_repo_relative": _repo_rel(r, op_sum) if op_sum.is_file() else None,
        # Stable path for this artifact (written in the same batch pass as ``index.json``).
        "fleet_governance_rollup_repo_relative": _repo_rel(r, rollup),
    }
    return out


def _per_product_optional_artifact_links(root: Path, product_id: str) -> dict[str, str | None]:
    """Repo-relative paths only when the file exists (deterministic; no guessing)."""
    r = root.resolve()
    task_p = orchestration_task_path(r, product_id)
    adv_p = orchestration_advancement_path(r, product_id)
    esc_p = tasks_latest_path(r, product_id)
    prog_p = orchestration_progression_latest_path(r, product_id)
    return {
        "orchestration_task_repo_relative": _repo_rel(r, task_p) if task_p.is_file() else None,
        "advancement_repo_relative": _repo_rel(r, adv_p) if adv_p.is_file() else None,
        "escalation_task_state_repo_relative": _repo_rel(r, esc_p) if esc_p.is_file() else None,
        "progression_run_repo_relative": _repo_rel(r, prog_p) if prog_p.is_file() else None,
    }


def _phase2_escalation_packet_latest_repo_relative(root: Path, product_id: str) -> str | None:
    """
    Latest ``runs/escalations/latest/esc_*.json`` for ``product_id`` (max ``created_at``, then path).

    Falls back to lexicographic max path among matching files when ``created_at`` is missing/unreadable.
    """
    from argus.escalation.packet import latest_dir

    r = root.resolve()
    lat = latest_dir(r)
    if not lat.is_dir():
        return None
    with_ts: list[tuple[datetime, Path]] = []
    paths_only: list[Path] = []
    for p in sorted(lat.glob("esc_*.json")):
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeError):
            continue
        if not isinstance(raw, dict):
            continue
        if str(raw.get("product_id") or "") != product_id:
            continue
        t = parse_iso_timestamp(str(raw.get("created_at") or "").strip() or None)
        if t is not None:
            with_ts.append((t, p))
        else:
            paths_only.append(p)
    if with_ts:
        _, best = max(with_ts, key=lambda x: (x[0], str(x[1])))
        return _repo_rel(r, best)
    if paths_only:
        best = max(paths_only, key=lambda x: str(x))
        return _repo_rel(r, best)
    return None


def _phase2_ideas_latest_repo_relative(root: Path, product_id: str) -> str | None:
    """``runs/ideas/latest.json`` when present and its ``product_id`` matches (shared path; inspect payload)."""
    r = root.resolve()
    lp = ideas_bundle_latest_path(r)
    if not lp.is_file():
        return None
    try:
        raw = json.loads(lp.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None
    if not isinstance(raw, dict) or str(raw.get("product_id") or "") != product_id:
        return None
    return _repo_rel(r, lp)


def _phase2_optional_chain_artifact_links(root: Path, product_id: str) -> dict[str, str | None]:
    """Repo-relative Phase-2 chain artifacts when present on disk (observe / interpret / decide / ideas / govern)."""
    r = root.resolve()
    sig = _signals_latest_path(r, product_id)
    fin = _findings_latest_path(r, product_id)
    dec = _decisions_latest_path(r, product_id)
    return {
        "signals_latest_repo_relative": _repo_rel(r, sig) if sig.is_file() else None,
        "findings_latest_repo_relative": _repo_rel(r, fin) if fin.is_file() else None,
        "decisions_latest_repo_relative": _repo_rel(r, dec) if dec.is_file() else None,
        "ideas_latest_repo_relative": _phase2_ideas_latest_repo_relative(r, product_id),
        "escalation_packet_latest_repo_relative": _phase2_escalation_packet_latest_repo_relative(
            r, product_id
        ),
    }


def _rollup_phase2_eligibility_bools(
    payload: dict[str, Any],
    eligible_action_ids: set[str],
) -> tuple[bool, bool, bool, bool]:
    facts = _eligibility_facts_dict(payload)

    def _merged(key: str, fallback: bool) -> bool:
        v = facts.get(key)
        if v is not None:
            return bool(v)
        return fallback

    fb_fg = ACTION_FINDINGS_GENERATE in eligible_action_ids
    fb_dg = ACTION_DECISIONS_GENERATE in eligible_action_ids
    fb_ig = ACTION_IDEAS_GENERATE in eligible_action_ids
    fb_ep = ACTION_ESCALATION_PACKET_GENERATE in eligible_action_ids
    return (
        _merged("findings_generate_eligible", fb_fg),
        _merged("decisions_generate_eligible", fb_dg),
        _merged("ideas_generate_eligible", fb_ig),
        _merged("escalation_packet_generate_eligible", fb_ep),
    )


def _rollup_phase2_chain_furthest_ready(
    payload: dict[str, Any],
    eligible_action_ids: set[str],
    *,
    findings_eligible: bool,
    decisions_eligible: bool,
    ideas_eligible: bool,
    escalation_packet_eligible: bool,
) -> str:
    """
    Single headline stage for the observe → interpret → decide → ideas → govern chain (highest first).

    Uses ``eligibility_facts`` when present; falls back to ``eligible_action_ids`` only when a fact
    key is absent.
    """
    facts = _eligibility_facts_dict(payload)

    if escalation_packet_eligible:
        return "govern"
    if ideas_eligible:
        return "ideas"
    if decisions_eligible:
        return "decide"
    if findings_eligible:
        return "interpret"

    obs_refresh = bool(facts.get("signals_refresh_needed") or facts.get("signals_collection_time_stale"))
    if obs_refresh:
        return "observe_refresh"
    if ACTION_SIGNALS_COLLECT in eligible_action_ids:
        return "observe_refresh"
    return "none"


def build_fleet_governance_rollup_payload(
    repo_root: Path,
    *,
    evaluated_at_utc: str,
    product_ids: list[str],
    by_pid: dict[str, dict[str, Any]],
    cross_product_prioritization: dict[str, Any],
) -> dict[str, Any]:
    """
    Compact fleet rollup from already-evaluated orchestration payloads (no re-evaluation).

    ``product_ids`` must be sorted for deterministic ``products[]`` order (callers use
    ``sorted(product_ids)``).
    """
    root = Path(repo_root).resolve()
    entries_in = cross_product_prioritization.get("entries") or []
    entry_by_pid: dict[str, dict[str, Any]] = {}
    if isinstance(entries_in, list):
        for e in entries_in:
            if isinstance(e, dict) and e.get("product_id"):
                entry_by_pid[str(e["product_id"])] = e

    products_out: list[dict[str, Any]] = []
    for pid in product_ids:
        payload = by_pid[pid]
        ent = entry_by_pid.get(pid, {})
        pr = ent.get("priority_rank")
        pt = ent.get("priority_tuple")
        pl = ent.get("priority_labels")
        orch_rel = str(orchestration_latest_path(root, pid).relative_to(root))

        elig = payload.get("eligible_actions") or []
        has_elig = bool(isinstance(elig, list) and len(elig) > 0)
        na = payload.get("next_action")
        next_action = str(na) if na is not None else "none"

        orch_posture = payload.get("orchestration_posture")
        posture_subset: dict[str, Any] = {}
        if isinstance(orch_posture, dict):
            for k in (
                "execution_feedback_crosswalk",
                "retry_reopened",
                "repeat_execution_feedback_thresholds",
            ):
                if k in orch_posture:
                    posture_subset[k] = orch_posture[k]

        art = payload.get("artifacts")
        refinement_review_state: str | None = None
        if isinstance(art, dict):
            ref = art.get("refinement")
            if isinstance(ref, dict):
                rs = ref.get("review_state")
                if rs is not None:
                    refinement_review_state = str(rs)

        triggers = payload.get("escalation_triggers") or []
        esc_codes: list[str] = []
        if isinstance(triggers, list):
            for t in triggers:
                if isinstance(t, dict) and t.get("code"):
                    esc_codes.append(str(t["code"]))
            esc_codes = sorted(set(esc_codes))

        eids: list[str] = []
        if isinstance(elig, list):
            for row in elig:
                if isinstance(row, dict) and row.get("action_id"):
                    eids.append(str(row["action_id"]))
            eids = sorted(set(eids))
        eid_set = set(eids)

        fg_e, dg_e, ig_e, ep_e = _rollup_phase2_eligibility_bools(payload, eid_set)
        chain_ready = _rollup_phase2_chain_furthest_ready(
            payload,
            eid_set,
            findings_eligible=fg_e,
            decisions_eligible=dg_e,
            ideas_eligible=ig_e,
            escalation_packet_eligible=ep_e,
        )
        phase2_posture: dict[str, Any] = {
            "findings_generate_eligible": fg_e,
            "decisions_generate_eligible": dg_e,
            "ideas_generate_eligible": ig_e,
            "escalation_packet_generate_eligible": ep_e,
            "chain_furthest_ready": chain_ready,
            "artifact_links": _phase2_optional_chain_artifact_links(root, pid),
        }

        idea_refinement_posture = _rollup_idea_refinement_posture(payload)
        experiment_posture = _rollup_experiment_posture(root, payload, eid_set, pid)

        rsn_codes = payload.get("orchestration_status_reason_codes") or []
        reason_codes_out: list[str] = []
        if isinstance(rsn_codes, list):
            reason_codes_out = sorted({str(x) for x in rsn_codes if str(x).strip()})

        per_links = _per_product_optional_artifact_links(root, pid)

        products_out.append(
            {
                "product_id": pid,
                "priority_rank": int(pr) if isinstance(pr, int) else pr,
                "priority_tuple": pt if isinstance(pt, list) else [],
                "priority_labels": pl if isinstance(pl, dict) else {},
                "orchestration_status": str(payload.get("orchestration_status") or ""),
                "orchestration_status_reason": _compact_orchestration_reason_for_rollup(
                    payload.get("orchestration_status_reason")
                ),
                "orchestration_status_reason_codes": reason_codes_out,
                "overall_status": str(payload.get("overall_status") or ""),
                "next_action": next_action,
                "has_eligible_actions": has_elig,
                "eligible_action_ids": eids,
                "escalation_eligible": bool(payload.get("escalation_eligible")),
                "escalation_trigger_codes": esc_codes,
                "refinement_review_state": refinement_review_state,
                "orchestration_posture": posture_subset,
                "phase2_posture": phase2_posture,
                "idea_refinement_posture": idea_refinement_posture,
                "experiment_posture": experiment_posture,
                "artifact_links": {
                    "orchestration_state_repo_relative": orch_rel,
                    **per_links,
                },
            }
        )

    return {
        "schema": ORCHESTRATION_FLEET_GOVERNANCE_ROLLUP_SCHEMA,
        "evaluated_at_utc": evaluated_at_utc,
        "repo_root": str(root),
        "artifact_links": _fleet_governance_top_links(root),
        "products": products_out,
    }


def write_fleet_governance_rollup_latest(repo_root: Path, payload: dict[str, Any]) -> Path:
    root = Path(repo_root).resolve()
    p = orchestration_fleet_governance_rollup_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return p


def _orchestration_progression_run_id(completed_at_utc: str, product_id: str) -> str:
    s = completed_at_utc.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        dt = datetime.now(timezone.utc)
    compact = dt.strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in product_id)
    return f"prog_{compact}_{safe}"


def write_orchestration_progression_run(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    """
    Persist ``argus.orchestration_progression_run_artifact.v1`` to latest + generations.

    Inserts ``run_id`` (derived from ``completed_at_utc`` and ``product_id``) into the written JSON.
    """
    root = repo_root.resolve()
    pid = str(payload.get("product_id") or "")
    completed = str(payload.get("completed_at_utc") or "")
    run_id = _orchestration_progression_run_id(completed, pid)
    body = {**payload, "run_id": run_id, "schema": ORCHESTRATION_PROGRESSION_RUN_ARTIFACT_SCHEMA}
    text = dumps_json(body) + "\n"
    latest = orchestration_progression_latest_path(root, pid)
    gen = orchestration_progression_generation_path(root, run_id)
    for p in (gen, latest):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return latest, gen


def write_orchestration_state(repo_root: Path, product_id: str) -> Path:
    payload = evaluate_product_orchestration(repo_root, product_id)
    return write_orchestration_state_payload(repo_root, product_id, payload)


def write_orchestration_state_payload(repo_root: Path, product_id: str, payload: dict) -> Path:
    path = orchestration_latest_path(repo_root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    sync_orchestration_task(repo_root, product_id, payload)
    write_operator_snapshot_artifacts(repo_root, product_id, payload)
    return path


def orchestration_priority_tuple(state: dict[str, Any]) -> tuple[int, int, int, int, int]:
    """
    Lexicographic priority signals (0/1 each), leftmost wins when comparing products.

    Order: repeated_failure_threshold, repeated_queued_unhandled_threshold, any escalation
    trigger, eligible_actions, retry-reopened (failed deprioritize suppressed).
    """
    triggers = state.get("escalation_triggers") or []
    if not isinstance(triggers, list):
        triggers = []
    codes = {str(t.get("code") or "") for t in triggers if isinstance(t, dict)}
    facts = _eligibility_facts_dict(state)
    sup = facts.get("orchestration_feedback_failed_deprioritize_suppressed_action_ids") or []
    sup_b = bool(isinstance(sup, list) and len(sup) > 0)

    r_fail = 1 if RC_ORCH_FEEDBACK_REPEATED_FAILURES in codes else 0
    r_uh = 1 if RC_ORCH_FEEDBACK_REPEATED_QUEUED_UNHANDLED in codes else 0
    esc = 1 if len(triggers) > 0 else 0
    elig = 1 if (state.get("eligible_actions") or []) else 0
    rr = 1 if sup_b else 0
    return (r_fail, r_uh, esc, elig, rr)


def _cross_product_sort_key(product_id: str, state: dict[str, Any]) -> tuple:
    t = orchestration_priority_tuple(state)
    return (-t[0], -t[1], -t[2], -t[3], -t[4], product_id)


def build_cross_product_prioritization(
    product_id_to_state: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """
    Deterministic ordering: higher-priority products first (attention rank 0 = first).

    Uses :func:`orchestration_priority_tuple` per product; tie-break ``product_id`` ascending.
    """
    ranked = sorted(
        product_id_to_state.keys(),
        key=lambda pid: _cross_product_sort_key(pid, product_id_to_state[pid]),
    )
    entries: list[dict[str, Any]] = []
    for rank, pid in enumerate(ranked):
        state = product_id_to_state[pid]
        tup = orchestration_priority_tuple(state)
        entries.append(
            {
                "product_id": pid,
                "priority_rank": rank,
                "priority_tuple": list(tup),
                "priority_labels": {
                    "repeated_failure_threshold": bool(tup[0]),
                    "repeated_queued_unhandled_threshold": bool(tup[1]),
                    "has_escalation_triggers": bool(tup[2]),
                    "has_eligible_actions": bool(tup[3]),
                    "retry_reopened_suppression": bool(tup[4]),
                },
            }
        )
    return {
        "ranked_product_ids": list(ranked),
        "entries": entries,
    }


def emit_orchestration_batch(
    repo_root: Path,
    product_ids: list[str],
    *,
    write: bool,
) -> tuple[dict[str, object], Path | None]:
    """
    Evaluate each product and optionally write per-product JSON plus ``index.json``.

    Returns ``(index_payload, index_path)`` where ``index_path`` is None if ``write`` is False.
    """
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).isoformat()
    by_pid: dict[str, dict[str, Any]] = {}
    for pid in sorted(product_ids):
        by_pid[pid] = evaluate_product_orchestration(root, pid)

    prior = build_cross_product_prioritization(by_pid)
    entry_by_pid = {e["product_id"]: e for e in prior["entries"] if isinstance(e, dict)}

    rows: list[dict[str, str | object]] = []
    for pid in sorted(product_ids):
        payload = by_pid[pid]
        if write:
            write_orchestration_state_payload(root, pid, payload)
        rel = orchestration_latest_path(root, pid).relative_to(root)
        ent = entry_by_pid.get(pid, {})
        pr = ent.get("priority_rank") if isinstance(ent, dict) else 0
        pt = ent.get("priority_tuple") if isinstance(ent, dict) else []
        rows.append(
            {
                "product_id": pid,
                "orchestration_status": payload.get("orchestration_status", ""),
                "orchestration_status_reason": payload.get("orchestration_status_reason", ""),
                "artifact_path": str(rel),
                "priority_rank": pr,
                "priority_tuple": pt,
            }
        )
    index_payload: dict[str, object] = {
        "schema": ORCHESTRATION_INDEX_SCHEMA,
        "evaluated_at_utc": ts,
        "repo_root": str(root),
        "products": rows,
        "cross_product_prioritization": prior,
    }
    index_payload = attach_batch_advancement_links_to_index_payload(root, index_payload)
    if not write:
        return index_payload, None
    idx = orchestration_index_path(root)
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(dumps_json(index_payload) + "\n", encoding="utf-8")
    rollup_payload = build_fleet_governance_rollup_payload(
        root,
        evaluated_at_utc=ts,
        product_ids=sorted(product_ids),
        by_pid=by_pid,
        cross_product_prioritization=prior,
    )
    write_fleet_governance_rollup_latest(root, rollup_payload)
    pp_payload = build_portfolio_priorities(
        root, sorted(product_ids), orchestration_states=by_pid
    )
    write_portfolio_priorities_payload(root, pp_payload)
    return index_payload, idx


def write_orchestration_states_batch(repo_root: Path, product_ids: list[str]) -> tuple[Path, list[dict[str, str | object]]]:
    """
    Evaluate and write per-product JSON plus a single **index** listing paths and headline status.

    Returns ``(index_path, product_rows)``.
    """
    index_payload, idx_path = emit_orchestration_batch(repo_root, product_ids, write=True)
    assert idx_path is not None
    products = index_payload["products"]
    assert isinstance(products, list)
    return idx_path, products


def load_orchestration_state(repo_root: Path, product_id: str) -> dict | None:
    p = orchestration_latest_path(repo_root, product_id)
    if not p.is_file():
        return None
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None
