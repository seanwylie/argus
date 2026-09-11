"""
Debug runner quality report — compact metrics over stamped autonomous sessions.

Writes ``runs/debug/runner_quality/latest.{json,md}``. Complements
:mod:`argus.portfolio.autonomy_memory` with iteration/cycle and streak views.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.autonomous_runner import PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA

RUNNER_QUALITY_REPORT_SCHEMA = "argus.debug_runner_quality.v1"

# Keep aligned with autonomy_memory._SAFE_CAUTION_STOPS (caution-class, not failure).
_SAFE_CAUTION_STOPS = frozenset(
    {
        "quiescence_recommendation",
        "no_material_change_streak",
        "max_cycles_reached",
    }
)


def runner_quality_report_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "debug" / "runner_quality"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _list_autonomous_session_paths(repo_root: Path, limit: int) -> list[Path]:
    d = Path(repo_root).resolve() / "runs" / "portfolio" / "autonomous_runner"
    if not d.is_dir():
        return []
    paths = [
        p
        for p in d.iterdir()
        if p.is_file() and p.suffix == ".json" and p.name not in ("latest.json",)
    ]
    paths.sort(key=lambda p: p.name, reverse=True)
    lim = max(1, int(limit))
    return paths[:lim]


def _session_ok(raw: dict[str, Any]) -> bool:
    return str(raw.get("schema") or "") == PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA


def _primary_code(codes: list[str]) -> str:
    if not codes:
        return ""
    return str(sorted(codes)[0])


def _last_cycle_snapshot(pl: dict[str, Any]) -> dict[str, Any]:
    outcomes = pl.get("per_cycle_outcomes") or []
    if not outcomes:
        return {}
    last = outcomes[-1] if isinstance(outcomes[-1], dict) else {}
    pc = last.get("portfolio_cycle") if isinstance(last.get("portfolio_cycle"), dict) else {}
    return {
        "overall_operator_recommendation": str(pc.get("overall_operator_recommendation") or "").strip(),
        "quiescence_recommendation": str(pc.get("quiescence_recommendation") or "").strip(),
        "intervention_flagged_count": int(pc.get("intervention_flagged_count") or 0),
        "progression_summary_counts": dict(pc.get("progression_summary_counts") or {})
        if isinstance(pc.get("progression_summary_counts"), dict)
        else {},
    }


def _lifecycle_primary(pl: dict[str, Any]) -> str:
    lsi = pl.get("lifecycle_session_influence") if isinstance(pl.get("lifecycle_session_influence"), dict) else {}
    return str(lsi.get("primary_signal") or "neutral").strip() or "neutral"


def _lifecycle_product_ids(pl: dict[str, Any]) -> list[str]:
    lsi = pl.get("lifecycle_session_influence") if isinstance(pl.get("lifecycle_session_influence"), dict) else {}
    snap = lsi.get("inputs_snapshot") if isinstance(lsi.get("inputs_snapshot"), dict) else {}
    keys = (
        "products_under_repair_pressure",
        "products_under_retirement_pressure",
        "products_entering",
        "products_exiting",
    )
    out: list[str] = []
    for k in keys:
        for x in snap.get(k) or []:
            xs = str(x).strip()
            if xs:
                out.append(xs)
    return out


def _productive_hint(pl: dict[str, Any]) -> tuple[bool, str]:
    cr = int(pl.get("cycles_run") or 0)
    if cr >= 2:
        return True, "multiple_iterations"
    for oc in pl.get("per_cycle_outcomes") or []:
        if not isinstance(oc, dict):
            continue
        pc = oc.get("portfolio_cycle") if isinstance(oc.get("portfolio_cycle"), dict) else {}
        ps = pc.get("progression_summary_counts") if isinstance(pc.get("progression_summary_counts"), dict) else {}
        if int(ps.get("advanced") or 0) > 0:
            return True, "progression_advanced"
    pe = pl.get("promotion_execution") if isinstance(pl.get("promotion_execution"), dict) else {}
    for st in pe.get("steps") or []:
        if isinstance(st, dict) and str(st.get("result_status") or "") == "success" and bool(st.get("attempted")):
            return True, "promotion_step_success"
    return False, ""


def _condition_key(pl: dict[str, Any]) -> tuple[str, str]:
    sr = str(pl.get("stop_reason") or "").strip()
    codes = [str(c) for c in (pl.get("stop_reason_codes") or []) if str(c).strip()]
    return sr, _primary_code(codes)


def _longest_same_stop_streak(chrono: list[dict[str, Any]]) -> dict[str, Any]:
    if not chrono:
        return {"max_consecutive_same_stop_reason": 0, "stop_reason": None}
    best_len = 0
    best_sr: str | None = None
    cur_sr: str | None = None
    cur_len = 0
    for pl in chrono:
        sr = str(pl.get("stop_reason") or "").strip() or "unknown"
        if sr == cur_sr:
            cur_len += 1
        else:
            cur_sr = sr
            cur_len = 1
        if cur_len > best_len:
            best_len = cur_len
            best_sr = sr
    return {"max_consecutive_same_stop_reason": best_len, "stop_reason": best_sr}


def build_runner_quality_report_payload(
    repo_root: Path,
    *,
    limit_history: int = 50,
) -> dict[str, Any]:
    """
    Deterministic metrics over recent stamped autonomous sessions (newest-first scan window).
    Chronological order for streaks uses ``finished_at_utc`` when present, else ``session_id``.
    """
    root = repo_root.resolve()
    lim = max(1, int(limit_history))
    paths = _list_autonomous_session_paths(root, lim)
    sessions: list[dict[str, Any]] = []
    for p in paths:
        raw = _load_json(p)
        if raw and _session_ok(raw):
            sessions.append(raw)

    def _sort_key(pl: dict[str, Any]) -> tuple[str, str]:
        fin = str(pl.get("finished_at_utc") or "")
        sid = str(pl.get("session_id") or "")
        return (fin, sid)

    sessions.sort(key=_sort_key, reverse=True)
    chrono = sorted(sessions, key=_sort_key)

    stop_c = Counter[str]()
    cycle_c = Counter[int]()
    codes_c = Counter[str]()
    lifecycle_c = Counter[str]()
    product_hits = Counter[str]()

    fingerprints: list[dict[str, Any]] = []
    productive_n = 0
    productive_reasons = Counter[str]()
    churn_hints: list[dict[str, Any]] = []

    for pl in sessions:
        sr = str(pl.get("stop_reason") or "").strip()
        if sr:
            stop_c[sr] += 1
        cr = int(pl.get("cycles_run") or 0)
        cycle_c[cr] += 1
        for c in pl.get("stop_reason_codes") or []:
            cs = str(c).strip()
            if cs:
                codes_c[cs] += 1
        lifecycle_c[_lifecycle_primary(pl)] += 1
        for pid in _lifecycle_product_ids(pl):
            product_hits[pid] += 1

        ok, reason = _productive_hint(pl)
        if ok:
            productive_n += 1
            productive_reasons[reason or "unknown"] += 1

        sid = str(pl.get("session_id") or "").strip()
        fp = {
            "session_id": sid,
            "finished_at_utc": str(pl.get("finished_at_utc") or ""),
            "stop_reason": sr,
            "primary_stop_code": _primary_code([str(c) for c in (pl.get("stop_reason_codes") or [])]),
            "cycles_run": cr,
            "last_cycle": _last_cycle_snapshot(pl),
            "lifecycle_primary_signal": _lifecycle_primary(pl),
        }
        fingerprints.append(fp)

    n = len(sessions)
    avg_cycles = float(sum(int(pl.get("cycles_run") or 0) for pl in sessions)) / float(n) if n else 0.0
    single_cycle_fraction = float(cycle_c.get(1, 0)) / float(n) if n else 0.0

    streak_info = _longest_same_stop_streak(chrono)

    consecutive_same_condition_pairs = 0
    for i in range(1, len(chrono)):
        if _condition_key(chrono[i]) == _condition_key(chrono[i - 1]):
            consecutive_same_condition_pairs += 1

    for i in range(1, len(chrono)):
        prev, cur = chrono[i - 1], chrono[i]
        ok_p, _ = _productive_hint(prev)
        ok_c, _ = _productive_hint(cur)
        if ok_p or ok_c:
            continue
        sr_p = str(prev.get("stop_reason") or "").strip()
        sr_c = str(cur.get("stop_reason") or "").strip()
        if sr_p == sr_c and sr_p in _SAFE_CAUTION_STOPS:
            churn_hints.append(
                {
                    "pattern": "repeated_safe_caution_same_stop",
                    "session_ids": [str(prev.get("session_id")), str(cur.get("session_id"))],
                    "stop_reason": sr_c,
                }
            )

    repeated_products = [
        {"product_id": k, "sessions_mentioning": int(product_hits[k])}
        for k in sorted(product_hits.keys())
        if product_hits[k] >= 2
    ]

    top_stop_codes = [{"code": k, "count": int(codes_c[k])} for k in sorted(codes_c.keys(), key=lambda x: -codes_c[x])[:15]]

    notes: list[str] = []
    if n == 0:
        notes.append("No stamped autonomous session JSON found under runs/portfolio/autonomous_runner/.")
    else:
        notes.append(
            f"Analyzed {n} session(s) in window (limit_history={lim}), ordered by finished_at for streak math."
        )
    if n >= 3 and single_cycle_fraction >= 0.85:
        notes.append(
            "Most sessions exit after one pipeline iteration — expected when cycle-overall / quiescence "
            "guardrails stop early; compare `max_consecutive_same_stop_reason` to see if the stop reason is stable."
        )
    if streak_info.get("max_consecutive_same_stop_reason", 0) >= 4 and len(set(stop_c.keys())) <= 2:
        notes.append(
            "Long same-stop streak with few distinct stop reasons — likely stable environmental caution, not thrashing."
        )
    if consecutive_same_condition_pairs >= 3:
        notes.append(
            f"{consecutive_same_condition_pairs} consecutive session pairs share the same (stop_reason, primary code) — "
            "evidence may be unchanged across runs; pair with intervention/escalation recurrence fields."
        )
    if productive_n == 0 and n >= 3:
        notes.append(
            "No sessions classified as productive in-window (multi-iteration, advancement, or promotion success) — "
            "portfolio may be inspection-bound or blocked; not a failure signal by itself."
        )

    suggested_actions: list[str] = []
    if single_cycle_fraction >= 0.9 and stop_c.get("cycle_overall_recommendation", 0) >= n * 0.5:
        suggested_actions.append(
            "Review cycle overall recommendation codes in `top_stop_reason_codes` — "
            "inspect-specific stops are bounded; confirm products called out in portfolio cycle."
        )
    if repeated_products:
        suggested_actions.append(
            "Products with repeated lifecycle pressure mentions may need explicit lifecycle review "
            "(see `repeated_product_attention`)."
        )

    non_productive_estimate = {
        "sessions_with_productive_hint": productive_n,
        "sessions_without_productive_hint": max(0, n - productive_n),
        "productive_hint_reasons": {k: int(productive_reasons[k]) for k in sorted(productive_reasons.keys())},
    }

    return {
        "schema": RUNNER_QUALITY_REPORT_SCHEMA,
        "evaluated_at_utc": _iso_now(),
        "inputs": {
            "limit_history": lim,
            "session_paths_scanned": [str(p.relative_to(root)) if p.is_relative_to(root) else str(p) for p in paths],
        },
        "summary": {
            "sessions_in_window": n,
            "avg_cycles_run": round(avg_cycles, 4),
            "single_cycle_session_fraction": round(single_cycle_fraction, 4),
            "cycles_run_histogram": {str(k): int(cycle_c[k]) for k in sorted(cycle_c.keys())},
            "stop_reason_counts": {k: int(stop_c[k]) for k in sorted(stop_c.keys())},
            "top_stop_reasons": [{"stop_reason": k, "count": int(stop_c[k])} for k in sorted(stop_c.keys(), key=lambda x: -stop_c[x])[:12]],
            "top_stop_reason_codes": top_stop_codes,
            "lifecycle_primary_signal_counts": {k: int(lifecycle_c[k]) for k in sorted(lifecycle_c.keys())},
            "max_consecutive_same_stop_reason": streak_info,
            "consecutive_same_condition_pairs": int(consecutive_same_condition_pairs),
            "repeated_safe_caution_churn_hints": churn_hints,
        },
        "non_productive_estimate": non_productive_estimate,
        "repeated_product_attention": repeated_products,
        "session_fingerprints_newest_first": fingerprints,
        "notes": notes,
        "suggested_next_actions": suggested_actions,
    }


def render_runner_quality_report_markdown(payload: dict[str, Any]) -> str:
    s = payload.get("summary") or {}
    lines = [
        "# Runner quality (debug)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Summary",
        "",
        f"- Sessions in window: **{s.get('sessions_in_window')}**",
        f"- Avg cycles per session: **{s.get('avg_cycles_run')}**",
        f"- Single-cycle fraction: **{s.get('single_cycle_session_fraction')}**",
        f"- Consecutive pairs same (stop, primary code): **{s.get('consecutive_same_condition_pairs')}**",
        "",
    ]
    streak = s.get("max_consecutive_same_stop_reason") or {}
    lines.append(
        f"- Longest streak same `stop_reason`: **{streak.get('max_consecutive_same_stop_reason')}** "
        f"(`{streak.get('stop_reason')}`)"
    )
    lines.append("")
    lines.append("### Stop reasons")
    lines.append("")
    for row in s.get("top_stop_reasons") or []:
        lines.append(f"- `{row.get('stop_reason')}` — {row.get('count')}")
    lines.extend(["", "### Notes", ""])
    for n in payload.get("notes") or []:
        lines.append(f"- {n}")
    lines.extend(["", "### Suggested next actions", ""])
    for a in payload.get("suggested_next_actions") or []:
        lines.append(f"- {a}")
    if not payload.get("suggested_next_actions"):
        lines.append("- —")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_runner_quality_report_artifacts(repo_root: Path, payload: dict[str, Any]) -> tuple[Path, Path]:
    root = repo_root.resolve()
    d = runner_quality_report_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    latest_json.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    latest_md.write_text(render_runner_quality_report_markdown(payload), encoding="utf-8")
    return latest_json, latest_md


def run_runner_quality_report(repo_root: Path, *, write_artifacts: bool = True, limit_history: int = 50) -> dict[str, Any]:
    payload = build_runner_quality_report_payload(repo_root, limit_history=limit_history)
    if write_artifacts:
        write_runner_quality_report_artifacts(repo_root, payload)
    return payload


__all__ = [
    "RUNNER_QUALITY_REPORT_SCHEMA",
    "build_runner_quality_report_payload",
    "render_runner_quality_report_markdown",
    "runner_quality_report_dir",
    "run_runner_quality_report",
    "write_runner_quality_report_artifacts",
]
