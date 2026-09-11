"""Compact refinement summary for the static dashboard JSON payload."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from argus.refinement.persistence import list_session_entries, read_json, session_dir
from argus.refinement.round_files import latest_round_file, round_sets_summary, scan_round_chain


def _preview(text: str, limit: int = 240) -> str:
    t = " ".join(text.strip().split())
    if len(t) <= limit:
        return t
    return t[: limit - 1] + "…"


def build_session_detail(repo_root: Path, session_id: str) -> dict[str, Any]:
    """Per-session drill-down: latest round artifacts, verdicts, synthesis/convergence snippets, round-chain health."""
    sd = session_dir(repo_root, session_id)
    out: dict[str, Any] = {
        "session_id": session_id,
        "round_sets": round_sets_summary(sd),
        "round_chain_errors": [],
        "round_chain_warnings": [],
        "latest_round": None,
        "draft": None,
        "reviews": None,
        "synthesis": None,
        "convergence": None,
    }
    err, warn = scan_round_chain(sd)
    out["round_chain_errors"] = err
    out["round_chain_warnings"] = warn

    sess = read_json(sd / "session.json")
    if sess:
        out["session_status"] = sess.get("status")
        out["current_round"] = sess.get("current_round")
        out["max_rounds"] = sess.get("max_rounds")
        meta = sess.get("meta") if isinstance(sess.get("meta"), dict) else {}
        cprofs = meta.get("council_profiles")
        if isinstance(cprofs, list):
            g = sum(1 for x in cprofs if isinstance(x, dict) and x.get("council_mode") == "grounded")
            o = sum(1 for x in cprofs if isinstance(x, dict) and x.get("council_mode") == "outsider")
            out["council_composition"] = {"grounded_seats": g, "outsider_seats": o, "from_session_meta": True}

    # Prefer the highest round that has a draft; else any latest file.
    draft_p = latest_round_file(sd / "drafts")
    if draft_p:
        dr = read_json(draft_p)
        if dr:
            rn = dr.get("round_number")
            out["latest_round"] = int(rn) if rn is not None else None
            out["draft"] = {
                "round_number": dr.get("round_number"),
                "title": dr.get("title"),
                "content_preview": _preview(str(dr.get("content", "")), 320),
            }

    rp = latest_round_file(sd / "reviews")
    if rp:
        raw = read_json(rp)
        if raw and isinstance(raw.get("reviews"), list):
            verdicts: list[dict[str, Any]] = []
            for rev in raw["reviews"]:
                if not isinstance(rev, dict):
                    continue
                verdicts.append(
                    {
                        "stakeholder": rev.get("stakeholder_type"),
                        "verdict": rev.get("verdict"),
                        "blocking": rev.get("blocking"),
                        "confidence_score": rev.get("confidence_score"),
                        "council_mode": rev.get("council_mode"),
                        "backend_used": rev.get("backend_used"),
                    }
                )
            out["reviews"] = {"round_file": rp.name, "verdicts": verdicts[:16]}

    sp = latest_round_file(sd / "synthesis")
    if sp:
        syn = read_json(sp)
        if syn:
            bi = syn.get("blocking_issues") if isinstance(syn.get("blocking_issues"), list) else []
            out["synthesis"] = {
                "round_file": sp.name,
                "overall_signal": syn.get("overall_signal"),
                "blocking_issues_count": len(bi),
                "themes_preview": [_preview(str(x), 120) for x in (syn.get("themes") or [])[:6]],
            }

    cp = latest_round_file(sd / "convergence")
    if cp:
        conv = read_json(cp)
        if conv:
            nar = conv.get("narrative") if isinstance(conv.get("narrative"), dict) else {}
            ins_lines = nar.get("inspection_lines") if isinstance(nar, dict) else None
            preview_ins: list[str] = []
            if isinstance(ins_lines, list):
                preview_ins = [_preview(str(x), 200) for x in ins_lines[:12]]
            out["convergence"] = {
                "round_file": cp.name,
                "converged": conv.get("converged"),
                "final_status": conv.get("final_status"),
                "pass_ratio": conv.get("pass_ratio"),
                "weighted_confidence": conv.get("weighted_confidence"),
                "reasons_preview": [_preview(str(x), 160) for x in (conv.get("reasons") or [])[:8]],
                "inspection_lines_preview": preview_ins,
            }

    return out


def build_refinement_dashboard_block(repo_root: Path) -> dict[str, Any]:
    root = repo_root.resolve()
    rows = list_session_entries(root)
    terminal = {"approved", "approved_with_risks", "rejected", "human_review_required"}
    active: list[dict[str, Any]] = []
    session_details: dict[str, Any] = {}
    for e in rows:
        st = str(e.get("status", ""))
        if st in terminal:
            continue
        sid = str(e.get("session_id", ""))
        blocking = 0
        sp = session_dir(root, sid) / "synthesis"
        if sp.is_dir():
            latest = sorted(sp.glob("round_*.json"), key=lambda p: p.name, reverse=True)
            if latest:
                raw = read_json(latest[0])
                if raw and isinstance(raw.get("blocking_issues"), list):
                    blocking = len(raw["blocking_issues"])
        active.append(
            {
                "session_id": sid,
                "artifact_type": e.get("artifact_type"),
                "source_id": e.get("source_id"),
                "product_id": e.get("product_id"),
                "current_round": e.get("current_round"),
                "status": st,
                "blocking_issues_count": blocking,
            }
        )
        session_details[sid] = build_session_detail(root, sid)

    summary = {"pass": 0, "concern": 0, "fail": 0}
    for e in rows:
        sid = str(e.get("session_id", ""))
        rp = session_dir(root, sid) / "reviews"
        if not rp.is_dir():
            continue
        latest = sorted(rp.glob("round_*.json"), key=lambda p: p.name, reverse=True)
        if not latest:
            continue
        raw = read_json(latest[0])
        if not raw or not isinstance(raw.get("reviews"), list):
            continue
        for rev in raw["reviews"]:
            if not isinstance(rev, dict):
                continue
            v = str(rev.get("verdict", ""))
            if v == "pass":
                summary["pass"] += 1
            elif v == "concern":
                summary["concern"] += 1
            elif v == "fail":
                summary["fail"] += 1

    return {
        "schema": "argus.dashboard_refinement.v2",
        "active_sessions": active[:50],
        "index_total": len(rows),
        "verdict_totals_latest_round": summary,
        "session_details": session_details,
    }
