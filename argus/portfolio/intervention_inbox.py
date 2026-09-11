"""
Intervention inbox — operator work items derived from portfolio intervention reports + local actions.

Does not advance orchestration or portfolio progression; writes inbox artifacts and append-only action JSON files under ``runs/portfolio/intervention_inbox/``.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json
from argus.portfolio.artifact_index import (
    list_timestamped_portfolio_json_files,
    stamp_run_id_from_path,
)
from argus.portfolio.intervention import (
    INTERVENTION_CONTINUE_MONITORING,
    INTERVENTION_SAFE_TO_IGNORE,
    PORTFOLIO_INTERVENTION_SCHEMA,
    SEVERITY_LOW,
    portfolio_intervention_dir,
)
from argus.portfolio.intervention_actions import (
    ACK_STATE_IGNORED,
    ACK_STATE_OPEN,
    ACK_STATE_RESOLVED,
    ACK_STATE_SNOOZED,
    fingerprint_for_intervention_row,
    intervention_inbox_dir,
    is_snooze_active,
    item_id_for_intervention_row,
    merge_intervention_actions,
)

INTERVENTION_INBOX_SCHEMA = "argus.intervention_inbox.v1"

DEFAULT_RECENT_INTERVENTION_LIMIT = 15


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _is_meaningful_intervention_row(row: dict[str, Any]) -> bool:
    """Drop passive / noise rows from the inbox queue."""
    cat = str(row.get("intervention_category") or "").strip()
    sev = str(row.get("severity") or "").strip()
    if cat == INTERVENTION_SAFE_TO_IGNORE:
        return False
    if cat == INTERVENTION_CONTINUE_MONITORING and sev == SEVERITY_LOW:
        return False
    return True


def _intervention_json_paths_for_history(repo_root: Path, limit: int) -> list[Path]:
    """Stamped intervention JSONs (newest-first slice) plus ``latest.json`` when distinct."""
    d = portfolio_intervention_dir(repo_root)
    stamped = list_timestamped_portfolio_json_files(d)[: max(0, int(limit))]
    latest = d / "latest.json"
    paths: list[Path] = []
    seen: set[object] = set()
    for p in sorted(stamped, key=lambda x: x.name) + ([latest] if latest.is_file() else []):
        key = p.resolve()
        if key in seen:
            continue
        seen.add(key)
        paths.append(p)
    return sorted(paths, key=lambda p: p.name)


def _detection_fingerprint_series_for_item_ids(
    repo_root: Path,
    item_ids: set[str],
    *,
    recent_limit: int,
) -> dict[str, list[str]]:
    """
    Fingerprints per distinct ``run_id`` in oldest-first path order (see
    ``_intervention_json_paths_for_history``). When ``latest.json`` repeats the same run as a
    stamped file, that run contributes once so recurrence compares material runs, not file copies.
    """
    paths = _intervention_json_paths_for_history(repo_root, recent_limit)
    run_order: list[str] = []
    per_item_run_fp: dict[str, dict[str, str]] = {iid: {} for iid in item_ids}
    for p in paths:
        raw = _load_json(p)
        if not raw or raw.get("schema") != PORTFOLIO_INTERVENTION_SCHEMA:
            continue
        rid = str(raw.get("run_id") or "").strip() or stamp_run_id_from_path(p)
        for row in raw.get("flagged_products") or []:
            if not isinstance(row, dict):
                continue
            if not _is_meaningful_intervention_row(row):
                continue
            iid = item_id_for_intervention_row(row)
            if iid not in item_ids:
                continue
            fp = fingerprint_for_intervention_row(row)
            per_item_run_fp[iid][rid] = fp
            if rid not in run_order:
                run_order.append(rid)
    out: dict[str, list[str]] = {}
    for iid in item_ids:
        fps = [per_item_run_fp[iid][r] for r in run_order if r in per_item_run_fp[iid]]
        out[iid] = fps
    return out


def _history_for_item_ids(
    repo_root: Path,
    item_ids: set[str],
    *,
    recent_limit: int,
) -> dict[str, dict[str, Any]]:
    """Per item_id: first_seen_run_id, last_seen_run_id, seen_in_run_count, recurring."""
    paths = _intervention_json_paths_for_history(repo_root, recent_limit)
    seen_runs: dict[str, list[str]] = {iid: [] for iid in item_ids}
    for p in paths:
        raw = _load_json(p)
        if not raw or raw.get("schema") != PORTFOLIO_INTERVENTION_SCHEMA:
            continue
        rid = str(raw.get("run_id") or "").strip() or stamp_run_id_from_path(p)
        for row in raw.get("flagged_products") or []:
            if not isinstance(row, dict):
                continue
            if not _is_meaningful_intervention_row(row):
                continue
            iid = item_id_for_intervention_row(row)
            if iid in seen_runs:
                seen_runs[iid].append(rid)
    out: dict[str, dict[str, Any]] = {}
    for iid, runs in seen_runs.items():
        if not runs:
            continue
        uniq = sorted(set(runs))
        out[iid] = {
            "first_seen_run_id": uniq[0],
            "last_seen_run_id": uniq[-1],
            "seen_in_run_count": len(uniq),
            "recurring": len(uniq) >= 2,
        }
    return out


def _effective_ack_state(
    *,
    merged: dict[str, Any],
    current_fp: str,
    still_flagged: bool,
) -> tuple[str, bool, str | None]:
    """
    Returns (ack_state, reopened_after_resolve_or_ignore, note).
    If resolved/ignored and same fingerprint still flagged → reopen as open.
    """
    st = str(merged.get("ack_state") or ACK_STATE_OPEN)
    res_fp = merged.get("resolution_fingerprint")
    res_s = str(res_fp).strip() if isinstance(res_fp, str) else ""

    if still_flagged and st in (ACK_STATE_RESOLVED, ACK_STATE_IGNORED) and res_s:
        if current_fp != res_s:
            return ACK_STATE_OPEN, True, "detection changed materially since last resolve/ignore"
        # Same issue persists after operator marked done → surface again
        return ACK_STATE_OPEN, True, "issue still flagged after resolve/ignore"

    if still_flagged and st == ACK_STATE_RESOLVED and not res_s:
        return ACK_STATE_OPEN, True, "issue still flagged (legacy resolve without fingerprint)"

    if still_flagged and st == ACK_STATE_IGNORED and not res_s:
        return ACK_STATE_OPEN, True, "issue still flagged (legacy ignore without fingerprint)"

    return st, False, None


def _in_active_queue(ack_state: str, merged: dict[str, Any]) -> bool:
    if ack_state in (ACK_STATE_RESOLVED, ACK_STATE_IGNORED):
        return False
    if ack_state == ACK_STATE_SNOOZED and is_snooze_active(merged):
        return False
    return True


def build_intervention_inbox_payload(
    repo_root: Path,
    *,
    intervention_report: dict[str, Any] | None = None,
    recent_intervention_limit: int = DEFAULT_RECENT_INTERVENTION_LIMIT,
) -> dict[str, Any]:
    """
    Build inbox JSON from latest intervention report + merged actions + recent-run history.
    """
    root = repo_root.resolve()
    built_at = datetime.now(timezone.utc).isoformat()
    rep = intervention_report if intervention_report is not None else _load_json(portfolio_intervention_dir(root) / "latest.json")
    if not rep or rep.get("schema") != PORTFOLIO_INTERVENTION_SCHEMA:
        merged = merge_intervention_actions(root)
        return {
            "schema": INTERVENTION_INBOX_SCHEMA,
            "built_at_utc": built_at,
            "source_intervention_run_id": None,
            "recent_intervention_runs_considered": int(recent_intervention_limit),
            "note": "No valid portfolio intervention report at runs/portfolio/intervention/latest.json",
            "open_items": [],
            "action_state": merged,
            "active_queue_rollups": {
                "active_item_count": 0,
                "by_intervention_category": {},
                "by_severity": {},
            },
        }

    run_id = str(rep.get("run_id") or "")
    rows_in: list[dict[str, Any]] = []
    for row in rep.get("flagged_products") or []:
        if isinstance(row, dict) and _is_meaningful_intervention_row(row):
            rows_in.append(row)

    item_ids = {item_id_for_intervention_row(r) for r in rows_in}
    hist = _history_for_item_ids(root, item_ids, recent_limit=recent_intervention_limit)
    fp_series = _detection_fingerprint_series_for_item_ids(
        root, item_ids, recent_limit=recent_intervention_limit
    )
    merged_actions = merge_intervention_actions(root)

    open_items: list[dict[str, Any]] = []
    for row in rows_in:
        iid = item_id_for_intervention_row(row)
        fp = fingerprint_for_intervention_row(row)
        series = fp_series.get(iid) or []
        unchanged_last_two = len(series) >= 2 and series[-1] == series[-2]
        h = hist.get(iid, {})
        m = dict(merged_actions.get(iid) or {})
        m.setdefault("item_id", iid)
        ack, reopened, reopen_note = _effective_ack_state(
            merged=m,
            current_fp=fp,
            still_flagged=True,
        )
        if ack == ACK_STATE_SNOOZED and not is_snooze_active(m):
            ack = ACK_STATE_OPEN
        active = _in_active_queue(ack, {**m, "ack_state": ack})

        entry: dict[str, Any] = {
            "item_id": iid,
            "product_id": str(row.get("product_id") or "").strip(),
            "intervention_category": str(row.get("intervention_category") or "").strip(),
            "severity": str(row.get("severity") or "").strip(),
            "chronicity": str(row.get("chronicity") or "").strip(),
            "detection_reason_codes": list(row.get("detection_reason_codes") or []),
            "evidence_summary": str(row.get("evidence_summary") or ""),
            "recommended_operator_action": str(row.get("recommended_operator_action") or ""),
            "first_seen_run_id": h.get("first_seen_run_id") or run_id,
            "last_seen_run_id": h.get("last_seen_run_id") or run_id,
            "seen_in_run_count": int(h.get("seen_in_run_count") or 1),
            "recurring": bool(h.get("recurring")),
            "ack_state": ack,
            "snooze_until_utc": m.get("snooze_until_utc") if ack == ACK_STATE_SNOOZED else None,
            "in_active_queue": active,
            "reopened_after_resolve_or_ignore": reopened,
            "reopen_note": reopen_note,
            "detection_fingerprint": fp,
            "intervention_reports_with_detection_row": len(series),
            "evidence_unchanged_across_last_two_intervention_runs": unchanged_last_two,
        }
        open_items.append(entry)

    # Stable order: severity high first, then product id
    sev_rank = {"high": 0, "medium": 1, "low": 2}

    def _sort_key(e: dict[str, Any]) -> tuple[int, str]:
        s = str(e.get("severity") or "")
        return (sev_rank.get(s, 9), str(e.get("product_id") or ""))

    open_items.sort(key=_sort_key)

    active = [x for x in open_items if x.get("in_active_queue")]
    by_cat: dict[str, int] = {}
    by_sev: dict[str, int] = {}
    for x in active:
        c = str(x.get("intervention_category") or "")
        s = str(x.get("severity") or "")
        by_cat[c] = by_cat.get(c, 0) + 1
        by_sev[s] = by_sev.get(s, 0) + 1

    return {
        "schema": INTERVENTION_INBOX_SCHEMA,
        "built_at_utc": built_at,
        "source_intervention_run_id": run_id or None,
        "recent_intervention_runs_considered": int(recent_intervention_limit),
        "open_items": open_items,
        "action_state": merged_actions,
        "active_queue_rollups": {
            "active_item_count": len(active),
            "by_intervention_category": dict(sorted(by_cat.items())),
            "by_severity": dict(sorted(by_sev.items())),
        },
    }


def render_intervention_inbox_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Intervention inbox",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Built (UTC):** {payload.get('built_at_utc')}",
        f"**Source intervention run:** `{payload.get('source_intervention_run_id')}`",
        "",
    ]
    if payload.get("note"):
        lines.extend([str(payload.get("note")), ""])
    active = [x for x in (payload.get("open_items") or []) if isinstance(x, dict) and x.get("in_active_queue")]
    deferred = [x for x in (payload.get("open_items") or []) if isinstance(x, dict) and not x.get("in_active_queue")]
    lines.extend(
        [
            "## Active queue",
            "",
        ]
    )
    if not active:
        lines.append("—")
    else:
        lines.append("| Item | Product | Category | Severity | Ack | Recurring |")
        lines.append("|------|---------|----------|----------|-----|-----------|")
        for e in active:
            lines.append(
                f"| `{e.get('item_id')}` | `{e.get('product_id')}` | `{e.get('intervention_category')}` | "
                f"`{e.get('severity')}` | `{e.get('ack_state')}` | {e.get('recurring')} |"
            )
    lines.extend(["", "## Deferred (snoozed / resolved / ignored)", ""])
    if not deferred:
        lines.append("—")
    else:
        for e in deferred:
            lines.append(
                f"- `{e.get('item_id')}` — `{e.get('product_id')}` — ack=`{e.get('ack_state')}` — "
                f"snooze_until={e.get('snooze_until_utc')}"
            )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_intervention_inbox_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["inbox_artifact_run_id"] = rid
    d = intervention_inbox_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_intervention_inbox_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def build_operator_intervention_inbox_view(
    repo_root: Path,
    *,
    recent_intervention_limit: int = DEFAULT_RECENT_INTERVENTION_LIMIT,
) -> dict[str, Any]:
    """
    Operator dashboard inbox rows: built from canonical ``runs/portfolio/intervention/latest.json``
    (when valid) plus merged action state — not from ``intervention_inbox/latest.json`` alone.
    """
    root = repo_root.resolve()
    inv_p = portfolio_intervention_dir(root) / "latest.json"
    raw = _load_json(inv_p)
    if not raw or str(raw.get("schema") or "") != PORTFOLIO_INTERVENTION_SCHEMA:
        return {
            "schema": INTERVENTION_INBOX_SCHEMA,
            "built_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_intervention_run_id": None,
            "recent_intervention_runs_considered": int(recent_intervention_limit),
            "open_items": [],
            "action_state": merge_intervention_actions(root),
            "coherence_note": (
                "No valid canonical portfolio intervention report — open_items not derived "
                f"(expected schema {PORTFOLIO_INTERVENTION_SCHEMA})."
            ),
        }
    return build_intervention_inbox_payload(
        root,
        intervention_report=raw,
        recent_intervention_limit=recent_intervention_limit,
    )


def run_intervention_inbox(
    repo_root: Path,
    *,
    recent_intervention_limit: int = DEFAULT_RECENT_INTERVENTION_LIMIT,
    write_artifacts: bool = True,
    intervention_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_intervention_inbox_payload(
        repo_root,
        intervention_report=intervention_report,
        recent_intervention_limit=recent_intervention_limit,
    )
    if write_artifacts:
        write_intervention_inbox_artifacts(repo_root, payload)
    return payload
