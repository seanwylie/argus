"""
Escalation inbox — operator-facing items that require human judgment, approval, or unblock work.

Deterministic merge of autonomous sessions, blocked lifecycle promotions, intervention signals,
scheduler/cycle stop reasons, and persisted operator actions (ack / resolve / snooze / escalate).
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from argus.builder.escalation_bridge import list_operator_visible_builder_packets
from argus.core.serialize import dumps_json
from argus.portfolio.autonomous_runner import (
    PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA,
    portfolio_autonomous_runner_dir,
)
from argus.portfolio.cycle import PORTFOLIO_CYCLE_SCHEMA, portfolio_cycle_dir
from argus.portfolio.escalation_operator_enrichment import (
    apply_escalation_operator_enrichment,
    load_operator_queue_context,
)
from argus.portfolio.intervention import INTERVENTION_EVIDENCE_REFRESH
from argus.portfolio.intervention_inbox import build_intervention_inbox_payload
from argus.portfolio.scheduler import (
    CYCLE_OVERALL_STOP,
    PORTFOLIO_SCHEDULER_SESSION_SCHEMA,
    portfolio_scheduler_dir,
)

ESCALATION_INBOX_SCHEMA = "argus.escalation_inbox.v1"
ESCALATION_INBOX_ACTION_SCHEMA = "argus.escalation_inbox_action.v1"

# Categories (distinct from intervention noise; aligned with autonomy boundary semantics)
CATEGORY_INFORMATIONAL = "informational"
CATEGORY_REVIEW_NEEDED = "review_needed"
CATEGORY_APPROVAL_NEEDED = "approval_needed"
CATEGORY_EXTERNAL_DEPENDENCY = "external_dependency"
CATEGORY_UNSAFE_TO_CONTINUE = "unsafe_to_continue"

ACTION_ACKNOWLEDGE = "acknowledge"
ACTION_RESOLVE = "resolve"
ACTION_SNOOZE = "snooze"
ACTION_ESCALATE_PRIORITY = "escalate_priority"

ACK_STATE_OPEN = "open"
ACK_STATE_ACKNOWLEDGED = "acknowledged"
ACK_STATE_SNOOZED = "snoozed"
ACK_STATE_RESOLVED = "resolved"
ACK_STATE_ESCALATED_PRIORITY = "escalated_priority"

RESOLUTION_OPEN = "open"
RESOLUTION_RESOLVED = "resolved"
RESOLUTION_SNOOZED = "snoozed"

SEVERITY_LOW = "low"
SEVERITY_MEDIUM = "medium"
SEVERITY_HIGH = "high"
SEVERITY_CRITICAL = "critical"

DEFAULT_RECENT_SESSION_LIMIT = 12


def escalation_inbox_dir(repo_root: Path) -> Path:
    return Path(repo_root).resolve() / "runs" / "portfolio" / "escalation_inbox"


def escalation_inbox_actions_dir(repo_root: Path) -> Path:
    return escalation_inbox_dir(repo_root) / "actions"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s: str) -> datetime | None:
    try:
        raw = s.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _slug_item_id(item_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", item_id.strip())
    return s[:120] if len(s) > 120 else s


def _stable_hash(*parts: str) -> str:
    h = hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:16]
    return h


def _fingerprint_item(
    *,
    source: str,
    kind: str,
    product_id: str | None,
    summary: str,
) -> str:
    pid = (product_id or "").strip()
    return _stable_hash(source, kind, pid, summary.strip()[:500])


def list_escalation_action_files(repo_root: Path) -> list[Path]:
    d = escalation_inbox_actions_dir(repo_root)
    if not d.is_dir():
        return []
    files = [p for p in d.iterdir() if p.is_file() and p.suffix == ".json"]
    return sorted(files, key=lambda p: p.name)


def load_escalation_action_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return raw if isinstance(raw, dict) else None


def merge_escalation_actions(repo_root: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    loaded: list[tuple[str, Path, dict[str, Any]]] = []
    for p in list_escalation_action_files(repo_root):
        rec = load_escalation_action_file(p)
        if not rec or rec.get("schema") != ESCALATION_INBOX_ACTION_SCHEMA:
            continue
        iid = str(rec.get("item_id") or "").strip()
        if not iid:
            continue
        at = str(rec.get("at_utc") or "").strip()
        loaded.append((at, p, rec))
    loaded.sort(key=lambda x: (x[0], x[1].name))
    for _at_key, _p, rec in loaded:
        iid = str(rec.get("item_id") or "").strip()
        verb = str(rec.get("verb") or "").strip()
        at = str(rec.get("at_utc") or "").strip()
        cur = out.get(iid) or {"item_id": iid, "ack_state": ACK_STATE_OPEN}
        cur["last_action_verb"] = verb
        cur["last_action_at_utc"] = at
        if verb == ACTION_ACKNOWLEDGE:
            cur["ack_state"] = ACK_STATE_ACKNOWLEDGED
            cur.pop("snooze_until_utc", None)
        elif verb == ACTION_SNOOZE:
            cur["ack_state"] = ACK_STATE_SNOOZED
            su = rec.get("snooze_until_utc")
            if isinstance(su, str) and su.strip():
                cur["snooze_until_utc"] = su.strip()
        elif verb == ACTION_RESOLVE:
            cur["ack_state"] = ACK_STATE_RESOLVED
            fp = rec.get("resolution_fingerprint")
            if isinstance(fp, str) and fp.strip():
                cur["resolution_fingerprint"] = fp.strip()
            cur.pop("snooze_until_utc", None)
        elif verb == ACTION_ESCALATE_PRIORITY:
            cur["ack_state"] = ACK_STATE_ESCALATED_PRIORITY
            cur.pop("snooze_until_utc", None)
        note = rec.get("note")
        if isinstance(note, str) and note.strip():
            cur["last_note"] = note.strip()
        out[iid] = cur
    return out


def is_snooze_active(state: dict[str, Any], *, now: datetime | None = None) -> bool:
    if str(state.get("ack_state") or "") != ACK_STATE_SNOOZED:
        return False
    su = state.get("snooze_until_utc")
    if not isinstance(su, str) or not su.strip():
        return False
    dt = _parse_iso(su)
    if dt is None:
        return False
    n = now or _utc_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return n < dt


def snooze_until_iso(*, days: float, now: datetime | None = None) -> str:
    base = now or _utc_now()
    until = base + timedelta(days=float(days))
    return until.isoformat().replace("+00:00", "Z")


def append_escalation_action(
    repo_root: Path,
    *,
    item_id: str,
    verb: str,
    at_utc: datetime | None = None,
    note: str | None = None,
    snooze_until_utc: str | None = None,
    resolution_fingerprint: str | None = None,
) -> Path:
    root = repo_root.resolve()
    d = escalation_inbox_actions_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    ts = (at_utc or _utc_now()).strftime("%Y%m%dT%H%M%SZ")
    name = f"{ts}__{verb}__{_slug_item_id(item_id)}.json"
    path = d / name
    payload: dict[str, Any] = {
        "schema": ESCALATION_INBOX_ACTION_SCHEMA,
        "item_id": item_id,
        "verb": verb,
        "at_utc": (at_utc or _utc_now()).isoformat().replace("+00:00", "Z"),
    }
    if note:
        payload["note"] = note
    if snooze_until_utc:
        payload["snooze_until_utc"] = snooze_until_utc
    if resolution_fingerprint:
        payload["resolution_fingerprint"] = resolution_fingerprint
    path.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return path


def _blocked_promotion_escalation_tier(
    row: dict[str, Any],
) -> tuple[bool, str, str, bool]:
    """
    Returns (include_in_escalation, category, severity, requires_action).
    Routine operational blocks are downgraded to informational-only rows (still listed when include_all).
    """
    kind = str(row.get("kind") or "")
    reason = str(row.get("reason") or "").lower()
    if "plan artifact already exists" in reason or "already exists for this product" in reason:
        return False, CATEGORY_INFORMATIONAL, SEVERITY_LOW, False
    if "another deprecation proposal" in reason or "queued for promotion" in reason:
        return False, CATEGORY_INFORMATIONAL, SEVERITY_LOW, False
    if "existing product directory" in reason or "overwrite" in reason or "refusing" in reason:
        return True, CATEGORY_REVIEW_NEEDED, SEVERITY_HIGH, True
    if "not in valid inventory" in reason or "inventory" in reason:
        return True, CATEGORY_REVIEW_NEEDED, SEVERITY_MEDIUM, True
    if "not found" in reason or "preview failed" in reason:
        return True, CATEGORY_EXTERNAL_DEPENDENCY, SEVERITY_MEDIUM, True
    if "invalid deprecation_posture" in reason or "invalid" in reason:
        return True, CATEGORY_REVIEW_NEEDED, SEVERITY_MEDIUM, True
    if kind == "creation_proposal_to_scaffold":
        return True, CATEGORY_REVIEW_NEEDED, SEVERITY_MEDIUM, True
    if kind == "deprecation_proposal_to_plan":
        return True, CATEGORY_REVIEW_NEEDED, SEVERITY_MEDIUM, True
    if kind == "scaffolded_product_to_bootstrap":
        return True, CATEGORY_REVIEW_NEEDED, SEVERITY_MEDIUM, True
    return True, CATEGORY_REVIEW_NEEDED, SEVERITY_LOW, True


def _autonomous_stop_mapping(
    stop_reason: str,
    codes: list[str],
) -> tuple[str, str, bool, str]:
    """category, severity, requires_action, requested_action"""
    sr = stop_reason.strip()
    if sr in (
        "portfolio_refresh_failed",
        "portfolio_cycle_failed",
        "portfolio_lifecycle_failed",
        "operator_summary_failed",
        "operator_narrative_failed",
    ):
        return (
            CATEGORY_UNSAFE_TO_CONTINUE,
            SEVERITY_CRITICAL,
            True,
            "Investigate pipeline failure before more autonomous cycles; fix underlying error or restore artifacts.",
        )
    if sr == "empty_portfolio":
        return (
            CATEGORY_INFORMATIONAL,
            SEVERITY_LOW,
            False,
            "Portfolio inventory is empty — add a validated product under products/ before autonomous cycles can proceed.",
        )
    if sr == "intervention_heavy_streak":
        return (
            CATEGORY_REVIEW_NEEDED,
            SEVERITY_HIGH,
            True,
            "Review flagged products and intervention inbox; reduce chronic intervention load before automation continues.",
        )
    if sr == "cycle_overall_recommendation":
        return (
            CATEGORY_APPROVAL_NEEDED,
            SEVERITY_MEDIUM,
            True,
            "Portfolio cycle recommends stopping automation — confirm operator intent and queue health.",
        )
    if sr == "quiescence_recommendation":
        for c in codes:
            if "quiescence.human_review" in c:
                return (
                    CATEGORY_REVIEW_NEEDED,
                    SEVERITY_MEDIUM,
                    True,
                    "Quiescence recommends human review — inspect portfolio state before continuing.",
                )
            if "quiescence.wait" in c:
                return (
                    CATEGORY_EXTERNAL_DEPENDENCY,
                    SEVERITY_MEDIUM,
                    True,
                    "Automation stopped on quiescence wait — unblock waiting inputs or approvals outside Argus.",
                )
            if "quiescence.inspect" in c:
                return (
                    CATEGORY_REVIEW_NEEDED,
                    SEVERITY_MEDIUM,
                    True,
                    "Inspect specific products or imports before resuming cycles.",
                )
            if "import_refresh" in c:
                return (
                    CATEGORY_EXTERNAL_DEPENDENCY,
                    SEVERITY_MEDIUM,
                    True,
                    "Refresh or repair imports before blind automation continues.",
                )
        return CATEGORY_INFORMATIONAL, SEVERITY_LOW, False, "Quiescence stop recorded; may be routine."
    if sr == "no_material_change_streak":
        return (
            CATEGORY_INFORMATIONAL,
            SEVERITY_LOW,
            False,
            "No material portfolio change for several cycles — informational unless mission-critical.",
        )
    if sr == "explicit_stop_sentinel":
        return (
            CATEGORY_INFORMATIONAL,
            SEVERITY_LOW,
            False,
            "Operator STOP file halted the session intentionally.",
        )
    if sr == "max_cycles_reached":
        return CATEGORY_INFORMATIONAL, SEVERITY_LOW, False, "Session reached max cycle cap without guardrail stop."
    return CATEGORY_REVIEW_NEEDED, SEVERITY_MEDIUM, True, f"Autonomous session stopped: {sr}"


def _list_recent_autonomous_jsons(repo_root: Path, limit: int) -> list[Path]:
    d = portfolio_autonomous_runner_dir(repo_root)
    if not d.is_dir():
        return []
    files = [p for p in d.glob("*.json") if p.name != "latest.json"]
    files.sort(key=lambda p: p.name, reverse=True)
    return files[: max(0, limit)]


def _first_last_seen_for_fingerprints(
    repo_root: Path,
    fps: set[str],
    *,
    recent_limit: int,
) -> dict[str, dict[str, Any]]:
    """Scan recent autonomous JSONs for recurring fingerprints (blocked promos + stop reasons)."""
    out: dict[str, list[str]] = {fp: [] for fp in fps}
    paths = _list_recent_autonomous_jsons(repo_root, recent_limit)
    latest = portfolio_autonomous_runner_dir(repo_root) / "latest.json"
    ordered = sorted({*paths, latest}, key=lambda p: p.name)
    for p in ordered:
        raw = _load_json(p)
        if not raw or raw.get("schema") != PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA:
            continue
        rid = str(raw.get("session_id") or "").strip() or p.stem
        for b in raw.get("blocked_promotions") or []:
            if not isinstance(b, dict):
                continue
            inc, _cat, _sev, _ra = _blocked_promotion_escalation_tier(b)
            if not inc:
                continue
            summary = str(b.get("reason") or "")
            fp = _fingerprint_item(
                source="blocked_promotion",
                kind=str(b.get("kind") or ""),
                product_id=str(b.get("product_id") or "") or None,
                summary=summary,
            )
            if fp in out:
                out[fp].append(rid)
        sr = str(raw.get("stop_reason") or "")
        codes = [str(c) for c in (raw.get("stop_reason_codes") or [])]
        cat, _sev, _req, _msg = _autonomous_stop_mapping(sr, codes)
        if cat != CATEGORY_INFORMATIONAL or _req:
            fp = _fingerprint_item(
                source="autonomous_stop",
                kind=sr,
                product_id=None,
                summary=sr + "|" + ",".join(sorted(codes)),
            )
            if fp in out:
                out[fp].append(rid)
    hist: dict[str, dict[str, Any]] = {}
    for fp, runs in out.items():
        if not runs:
            continue
        uniq = sorted(set(runs))
        hist[fp] = {
            "first_seen_run_id": uniq[0],
            "last_seen_run_id": uniq[-1],
            "seen_in_run_count": len(uniq),
            "recurring": len(uniq) >= 2,
        }
    return hist


def _items_from_autonomous(
    repo_root: Path,
    raw: dict[str, Any] | None,
    *,
    recent_limit: int,
) -> list[dict[str, Any]]:
    if not raw or raw.get("schema") != PORTFOLIO_AUTONOMOUS_RUNNER_SCHEMA:
        return []
    run_id = str(raw.get("session_id") or raw.get("finished_at_utc") or "unknown")
    items: list[dict[str, Any]] = []
    blocked = raw.get("blocked_promotions") or []
    fp_set: set[str] = set()

    for b in blocked:
        if not isinstance(b, dict):
            continue
        inc, cat, sev, req = _blocked_promotion_escalation_tier(b)
        summary = str(b.get("reason") or "")
        fp = _fingerprint_item(
            source="blocked_promotion",
            kind=str(b.get("kind") or ""),
            product_id=str(b.get("product_id") or "") or None,
            summary=summary,
        )
        fp_set.add(fp)
        if not inc:
            iid = f"esc-bp-info-{_stable_hash(fp)}"
            items.append(
                {
                    "item_id": iid,
                    "category": CATEGORY_INFORMATIONAL,
                    "product_id": str(b.get("product_id") or "").strip() or None,
                    "source": "blocked_promotion",
                    "source_run_id": run_id,
                    "severity": SEVERITY_LOW,
                    "requires_operator_action": False,
                    "requested_action": "No action required — routine lifecycle guardrail.",
                    "evidence_summary": summary or str(b),
                    "content_fingerprint": fp,
                    "first_seen_run_id": run_id,
                    "last_seen_run_id": run_id,
                    "source_refs": ["runs/portfolio/autonomous_runner/latest.json"],
                }
            )
            continue
        iid = f"esc-bp-{_stable_hash(fp, 'esc')}"
        items.append(
            {
                "item_id": iid,
                "category": cat,
                "product_id": str(b.get("product_id") or "").strip() or None,
                "source": "blocked_promotion",
                "source_run_id": run_id,
                "severity": sev,
                "requires_operator_action": req,
                "requested_action": "Resolve the blocked lifecycle promotion condition (see evidence).",
                "evidence_summary": summary or str(b),
                "content_fingerprint": fp,
                "first_seen_run_id": run_id,
                "last_seen_run_id": run_id,
                "source_refs": ["runs/portfolio/autonomous_runner/latest.json"],
            }
        )

    sr = str(raw.get("stop_reason") or "")
    codes = [str(c) for c in (raw.get("stop_reason_codes") or [])]
    cat, sev, req, action = _autonomous_stop_mapping(sr, codes)
    fp_stop = _fingerprint_item(
        source="autonomous_stop",
        kind=sr,
        product_id=None,
        summary=sr + "|" + ",".join(sorted(codes)),
    )
    fp_set.add(fp_stop)
    iid_stop = f"esc-auto-{ _stable_hash(sr, ','.join(codes)) }"
    items.append(
        {
            "item_id": iid_stop,
            "category": cat,
            "product_id": None,
            "source": "autonomous_runner",
            "source_run_id": run_id,
            "severity": sev,
            "requires_operator_action": req,
            "requested_action": action,
            "evidence_summary": f"stop_reason={sr}; codes={codes}",
            "content_fingerprint": fp_stop,
            "first_seen_run_id": run_id,
            "last_seen_run_id": run_id,
            "source_refs": ["runs/portfolio/autonomous_runner/latest.json"],
        }
    )

    hist = _first_last_seen_for_fingerprints(repo_root, fp_set, recent_limit=recent_limit)
    for it in items:
        fp = str(it.get("content_fingerprint") or "")
        h = hist.get(fp)
        if h:
            it["first_seen_run_id"] = h.get("first_seen_run_id") or it["first_seen_run_id"]
            it["last_seen_run_id"] = h.get("last_seen_run_id") or it["last_seen_run_id"]
            it["recurring"] = bool(h.get("recurring"))
            it["seen_in_run_count"] = int(h.get("seen_in_run_count") or 1)
        else:
            it["recurring"] = False
            it["seen_in_run_count"] = 1
    return items


def _items_from_scheduler(repo_root: Path) -> list[dict[str, Any]]:
    raw = _load_json(portfolio_scheduler_dir(repo_root) / "latest.json")
    if not raw or raw.get("schema") != PORTFOLIO_SCHEDULER_SESSION_SCHEMA:
        return []
    run_id = str(raw.get("session_id") or raw.get("finished_at_utc") or "scheduler")
    sr = str(raw.get("stop_reason") or "")
    codes = [str(c) for c in (raw.get("stop_reason_codes") or [])]
    cat, sev, req, action = _autonomous_stop_mapping(sr, codes)
    fp = _fingerprint_item(source="scheduler_stop", kind=sr, product_id=None, summary=sr + "|" + ",".join(codes))
    iid = f"esc-sch-{ _stable_hash(sr, ','.join(codes)) }"
    return [
        {
            "item_id": iid,
            "category": cat,
            "product_id": None,
            "source": "scheduler",
            "source_run_id": run_id,
            "severity": sev,
            "requires_operator_action": req,
            "requested_action": action,
            "evidence_summary": f"scheduler stop_reason={sr}; codes={codes}",
            "content_fingerprint": fp,
            "first_seen_run_id": run_id,
            "last_seen_run_id": run_id,
            "recurring": False,
            "seen_in_run_count": 1,
            "source_refs": ["runs/portfolio/scheduler/latest.json"],
        }
    ]


def _items_from_builder_escalation_packets(repo_root: Path) -> list[dict[str, Any]]:
    """Mirror critical/high Builder packets from ``runs/escalations/latest`` (medium stays artifact-only)."""
    rows = list_operator_visible_builder_packets(repo_root)
    out: list[dict[str, Any]] = []
    for row in rows:
        pid = str(row.get("product_id") or "").strip()
        pkt_id = str(row.get("packet_id") or "").strip()
        if not pkt_id:
            continue
        rl = str(row.get("risk_level") or "").strip().lower()
        sev = SEVERITY_CRITICAL if rl == "critical" else SEVERITY_HIGH
        cat = CATEGORY_UNSAFE_TO_CONTINUE if rl == "critical" else CATEGORY_REVIEW_NEEDED
        title = str(row.get("title") or "Builder anomaly")
        why = str(row.get("why_stopped") or "").strip()
        summ = str(row.get("summary") or "").strip()
        ev = why.splitlines()[0].strip() if why else (summ[:400] if summ else title)
        fp = _fingerprint_item(
            source="builder_escalation",
            kind=pkt_id,
            product_id=pid or None,
            summary=pkt_id,
        )
        iid = f"esc-bld-{pkt_id.replace('.', '_')}"
        rid = str(row.get("created_at") or pkt_id)
        ref = str(row.get("path_repo") or "runs/escalations/latest/")
        out.append(
            {
                "item_id": iid,
                "category": cat,
                "product_id": pid or None,
                "source": "builder_escalation",
                "source_run_id": rid,
                "severity": sev,
                "requires_operator_action": True,
                "requested_action": (
                    "Review Builder scope, execution outcome, and trust signals; inspect reconcile/invoke JSON "
                    f"before merge ({pkt_id})."
                ),
                "evidence_summary": ev[:2000],
                "content_fingerprint": fp,
                "first_seen_run_id": rid,
                "last_seen_run_id": rid,
                "recurring": False,
                "seen_in_run_count": 1,
                "source_refs": [ref],
                "related_builder_packet_id": pkt_id,
            }
        )
    return out


def _items_from_intervention_escalations(repo_root: Path) -> list[dict[str, Any]]:
    inbox = build_intervention_inbox_payload(repo_root)
    items: list[dict[str, Any]] = []
    for row in inbox.get("open_items") or []:
        if not isinstance(row, dict):
            continue
        if not row.get("in_active_queue"):
            continue
        sev = str(row.get("severity") or "").strip().lower()
        chronic = str(row.get("chronicity") or "").strip().lower()
        recurring = bool(row.get("recurring"))
        cat_s = str(row.get("intervention_category") or "")
        if sev != SEVERITY_HIGH and not recurring and chronic != "chronic":
            if "human" not in cat_s.lower() and "stuck" not in cat_s.lower():
                continue
        # Medium evidence_refresh with no detection change across the last two intervention artifacts
        # stays in the intervention inbox but does not duplicate pressure in the escalation inbox.
        if (
            sev == SEVERITY_MEDIUM
            and cat_s == INTERVENTION_EVIDENCE_REFRESH
            and bool(row.get("evidence_unchanged_across_last_two_intervention_runs"))
        ):
            continue
        iid = f"esc-inv-{row.get('item_id')}"
        fp = _fingerprint_item(
            source="intervention",
            kind=cat_s,
            product_id=str(row.get("product_id") or "") or None,
            summary=str(row.get("detection_fingerprint") or row.get("evidence_summary") or ""),
        )
        items.append(
            {
                "item_id": iid,
                "category": CATEGORY_REVIEW_NEEDED,
                "product_id": str(row.get("product_id") or "").strip() or None,
                "source": "intervention_inbox",
                "source_run_id": str(inbox.get("source_intervention_run_id") or "intervention"),
                "severity": SEVERITY_HIGH if sev == SEVERITY_HIGH else SEVERITY_MEDIUM,
                "requires_operator_action": True,
                "requested_action": str(row.get("recommended_operator_action") or "Review intervention row and take operator action."),
                "evidence_summary": str(row.get("evidence_summary") or "")[:2000],
                "content_fingerprint": fp,
                "first_seen_run_id": str(row.get("first_seen_run_id") or ""),
                "last_seen_run_id": str(row.get("last_seen_run_id") or ""),
                "recurring": recurring,
                "seen_in_run_count": int(row.get("seen_in_run_count") or 1),
                "source_refs": [
                    "runs/portfolio/intervention_inbox/latest.json",
                    "runs/portfolio/intervention/latest.json",
                ],
                "related_intervention_item_id": str(row.get("item_id") or ""),
            }
        )
    return items


def _items_from_portfolio_cycle(repo_root: Path) -> list[dict[str, Any]]:
    raw = _load_json(portfolio_cycle_dir(repo_root) / "latest.json")
    if not raw or raw.get("schema") != PORTFOLIO_CYCLE_SCHEMA:
        return []
    summ = raw.get("summary") or {}
    overall = str(summ.get("overall_operator_recommendation") or "").strip()
    if overall not in CYCLE_OVERALL_STOP:
        return []
    rid = str(raw.get("run_id") or "cycle")
    fp = _fingerprint_item(
        source="portfolio_cycle",
        kind=overall,
        product_id=None,
        summary=f"overall={overall}",
    )
    iid = f"esc-cyc-{ _stable_hash(overall, rid) }"
    sev = SEVERITY_MEDIUM
    cat = CATEGORY_APPROVAL_NEEDED
    if overall == "repair_imports":
        cat = CATEGORY_EXTERNAL_DEPENDENCY
    return [
        {
            "item_id": iid,
            "category": cat,
            "product_id": None,
            "source": "portfolio_cycle",
            "source_run_id": rid,
            "severity": sev,
            "requires_operator_action": True,
            "requested_action": (
                "Portfolio cycle recommends pausing automation — address imports, inspection, or human review "
                f"before continuing (overall={overall})."
            ),
            "evidence_summary": f"overall_operator_recommendation={overall}",
            "content_fingerprint": fp,
            "first_seen_run_id": rid,
            "last_seen_run_id": rid,
            "recurring": False,
            "seen_in_run_count": 1,
            "source_refs": ["runs/portfolio/cycle/latest.json"],
        }
    ]


def _dedupe_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rank = {
        SEVERITY_CRITICAL: 0,
        SEVERITY_HIGH: 1,
        SEVERITY_MEDIUM: 2,
        SEVERITY_LOW: 3,
    }
    by_fp: dict[str, dict[str, Any]] = {}
    for it in items:
        iid = str(it.get("item_id") or "")
        if not iid:
            continue
        fp = str(it.get("content_fingerprint") or "") or iid
        cur = by_fp.get(fp)
        if cur is None:
            by_fp[fp] = it
            continue
        r_new = rank.get(str(it.get("severity")), 9)
        r_old = rank.get(str(cur.get("severity")), 9)
        # Prefer higher severity; if tie, keep more recent source_run_id
        if r_new < r_old:
            by_fp[fp] = it
        elif r_new == r_old:
            by_fp[fp] = it if str(it.get("source_run_id") or "") >= str(cur.get("source_run_id") or "") else cur
    return sorted(by_fp.values(), key=lambda x: (str(x.get("severity")), str(x.get("item_id"))))


def _effective_resolution(
    *,
    merged: dict[str, Any],
    content_fp: str,
    still_present: bool,
) -> tuple[str, str, bool, str | None]:
    """ack_state, resolution_state, reopened, note"""
    st = str(merged.get("ack_state") or ACK_STATE_OPEN)
    res_fp = str(merged.get("resolution_fingerprint") or "").strip()

    if still_present and st == ACK_STATE_RESOLVED and res_fp and content_fp != res_fp:
        return ACK_STATE_OPEN, RESOLUTION_OPEN, True, "underlying signal changed since resolve"
    if still_present and st == ACK_STATE_RESOLVED and not res_fp:
        return ACK_STATE_OPEN, RESOLUTION_OPEN, True, "issue reappeared (legacy resolve without fingerprint)"
    if still_present and st == ACK_STATE_RESOLVED and res_fp == content_fp:
        return ACK_STATE_OPEN, RESOLUTION_OPEN, True, "issue still present after resolve"

    if still_present and st == ACK_STATE_SNOOZED and not is_snooze_active(merged):
        return ACK_STATE_OPEN, RESOLUTION_OPEN, False, "snooze expired"

    if st == ACK_STATE_SNOOZED and is_snooze_active(merged):
        return st, RESOLUTION_SNOOZED, False, None
    if st == ACK_STATE_RESOLVED:
        return st, RESOLUTION_RESOLVED, False, None
    if st == ACK_STATE_ACKNOWLEDGED:
        return st, RESOLUTION_OPEN, False, None
    if st == ACK_STATE_ESCALATED_PRIORITY:
        return st, RESOLUTION_OPEN, False, None
    return st, RESOLUTION_OPEN, False, None


def _in_active_queue(ack: str, merged: dict[str, Any], *, requires_action: bool, category: str) -> bool:
    if ack in (ACK_STATE_RESOLVED,):
        return False
    if ack == ACK_STATE_SNOOZED and is_snooze_active(merged):
        return False
    if not requires_action and category == CATEGORY_INFORMATIONAL and ack == ACK_STATE_OPEN:
        return False
    return True


def build_escalation_inbox_payload(
    repo_root: Path,
    *,
    recent_session_limit: int = DEFAULT_RECENT_SESSION_LIMIT,
) -> dict[str, Any]:
    root = repo_root.resolve()
    built_at = _utc_now().isoformat()
    merged_actions = merge_escalation_actions(root)
    queue_ctx = load_operator_queue_context(root)

    prior_inbox = _load_json(escalation_inbox_dir(root) / "latest.json")
    manual_preserved: list[dict[str, Any]] = []
    if prior_inbox and prior_inbox.get("schema") == ESCALATION_INBOX_SCHEMA:
        for row in prior_inbox.get("manual_items") or []:
            if isinstance(row, dict) and row.get("item_id"):
                m = dict(row)
                if not str(m.get("content_fingerprint") or "").strip():
                    m["content_fingerprint"] = _fingerprint_item(
                        source="manual",
                        kind=str(m.get("item_id")),
                        product_id=str(m.get("product_id") or "").strip() or None,
                        summary=str(m.get("evidence_summary") or m.get("requested_action") or ""),
                    )
                manual_preserved.append(m)

    autonomous_raw = _load_json(portfolio_autonomous_runner_dir(root) / "latest.json")
    items: list[dict[str, Any]] = []
    items.extend(_items_from_autonomous(root, autonomous_raw, recent_limit=recent_session_limit))
    items.extend(_items_from_scheduler(root))
    items.extend(_items_from_portfolio_cycle(root))
    items.extend(_items_from_intervention_escalations(root))
    items.extend(_items_from_builder_escalation_packets(root))
    items.extend(manual_preserved)
    items = _dedupe_items(items)

    open_items: list[dict[str, Any]] = []
    for it in items:
        iid = str(it.get("item_id") or "")
        fp = str(it.get("content_fingerprint") or "")
        m = dict(merged_actions.get(iid) or {})
        m.setdefault("item_id", iid)
        ack, res_state, reopened, reopen_note = _effective_resolution(
            merged=m,
            content_fp=fp,
            still_present=True,
        )
        if ack == ACK_STATE_SNOOZED and not is_snooze_active(m):
            ack = ACK_STATE_OPEN
            res_state = RESOLUTION_OPEN
        active = _in_active_queue(
            ack,
            {**m, "ack_state": ack},
            requires_action=bool(it.get("requires_operator_action")),
            category=str(it.get("category") or ""),
        )
        entry = {
            **it,
            "ack_state": ack,
            "resolution_state": res_state,
            "reopened_after_resolve": reopened,
            "reopen_note": reopen_note,
            "in_active_queue": active,
        }
        open_items.append(
            apply_escalation_operator_enrichment(entry, queue_ctx=queue_ctx, repo_root=root)
        )

    sev_order = {SEVERITY_CRITICAL: 0, SEVERITY_HIGH: 1, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 3}

    def _sort_key(e: dict[str, Any]) -> tuple[int, str]:
        s = str(e.get("severity") or "")
        return (sev_order.get(s, 9), str(e.get("item_id") or ""))

    open_items.sort(key=_sort_key)

    return {
        "schema": ESCALATION_INBOX_SCHEMA,
        "built_at_utc": built_at,
        "recent_autonomous_sessions_considered": int(recent_session_limit),
        "open_items": open_items,
        "action_state": merged_actions,
        "manual_items": manual_preserved,
        "inputs": {
            "sources": [
                "autonomous_runner",
                "scheduler",
                "portfolio_cycle",
                "intervention_inbox",
                "builder_escalation",
                "manual_items",
            ],
            "operator_queue_context_loaded": bool(queue_ctx.get("loaded")),
        },
    }


def render_escalation_inbox_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# Escalation inbox (autonomy boundary)",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Built (UTC):** {payload.get('built_at_utc')}",
        "",
        "## Active queue (needs attention)",
        "",
    ]
    active = [x for x in (payload.get("open_items") or []) if x.get("in_active_queue")]
    if not active:
        lines.append("—")
    else:
        lines.append("| Item | Category | Severity | Source | Product | Ack |")
        lines.append("|------|----------|----------|--------|---------|-----|")
        for e in active:
            lines.append(
                f"| `{e.get('item_id')}` | `{e.get('category')}` | `{e.get('severity')}` | "
                f"`{e.get('source')}` | `{e.get('product_id') or '—'}` | `{e.get('ack_state')}` |"
            )
        lines.extend(["", "### Actionable detail (active)", ""])
        for e in active:
            pp = e.get("primary_product") or e.get("product_id")
            lines.extend(
                [
                    f"#### `{e.get('item_id')}`",
                    "",
                    f"- **State:** {e.get('human_readable_state') or '—'}",
                    f"- **Primary product:** `{pp or '—'}`",
                    f"- **Cause:** {e.get('root_cause_summary') or '—'}",
                    f"- **Recommended action:** {e.get('recommended_operator_action') or '—'}",
                    "",
                ]
            )
    lines.extend(["", "## Deferred / informational", ""])
    defer = [x for x in (payload.get("open_items") or []) if not x.get("in_active_queue")]
    if not defer:
        lines.append("—")
    else:
        for e in defer:
            lines.append(
                f"- `{e.get('item_id')}` — {e.get('category')} — {e.get('evidence_summary', '')[:160]}"
            )
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def write_escalation_inbox_artifacts(
    repo_root: Path,
    payload: dict[str, Any],
    *,
    run_id: str | None = None,
) -> tuple[Path, Path, Path, Path]:
    root = repo_root.resolve()
    rid = run_id or _utc_now().strftime("%Y%m%dT%H%M%SZ")
    pl = dict(payload)
    pl["inbox_artifact_run_id"] = rid
    d = escalation_inbox_dir(root)
    d.mkdir(parents=True, exist_ok=True)
    stamped_json = d / f"{rid}.json"
    stamped_md = d / f"{rid}.md"
    latest_json = d / "latest.json"
    latest_md = d / "latest.md"
    stamped_json.write_text(dumps_json(pl) + "\n", encoding="utf-8")
    stamped_md.write_text(render_escalation_inbox_markdown(pl), encoding="utf-8")
    shutil.copyfile(stamped_json, latest_json)
    shutil.copyfile(stamped_md, latest_md)
    return stamped_json, stamped_md, latest_json, latest_md


def run_escalation_inbox(
    repo_root: Path,
    *,
    recent_session_limit: int = DEFAULT_RECENT_SESSION_LIMIT,
    write_artifacts: bool = True,
) -> dict[str, Any]:
    payload = build_escalation_inbox_payload(repo_root, recent_session_limit=recent_session_limit)
    if write_artifacts:
        write_escalation_inbox_artifacts(repo_root, payload)
    return payload


def record_escalation_acknowledge(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    return append_escalation_action(repo_root, item_id=item_id, verb=ACTION_ACKNOWLEDGE, note=note)


def record_escalation_resolve(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    """Resolve using current payload fingerprint for the item_id."""
    pl = build_escalation_inbox_payload(repo_root)
    fp = ""
    for row in pl.get("open_items") or []:
        if isinstance(row, dict) and str(row.get("item_id")) == item_id:
            fp = str(row.get("content_fingerprint") or "")
            break
    if not fp:
        raise ValueError(f"unknown escalation item_id: {item_id!r}")
    return append_escalation_action(
        repo_root,
        item_id=item_id,
        verb=ACTION_RESOLVE,
        note=note,
        resolution_fingerprint=fp,
    )


def record_escalation_snooze(
    repo_root: Path,
    item_id: str,
    *,
    days: float,
    note: str | None = None,
) -> Path:
    until = snooze_until_iso(days=days)
    return append_escalation_action(
        repo_root,
        item_id=item_id,
        verb=ACTION_SNOOZE,
        note=note,
        snooze_until_utc=until,
    )


def record_escalation_priority(repo_root: Path, item_id: str, *, note: str | None = None) -> Path:
    return append_escalation_action(repo_root, item_id=item_id, verb=ACTION_ESCALATE_PRIORITY, note=note)


__all__ = [
    "ACTION_ACKNOWLEDGE",
    "ACTION_ESCALATE_PRIORITY",
    "ACTION_RESOLVE",
    "ACTION_SNOOZE",
    "ACK_STATE_ACKNOWLEDGED",
    "ACK_STATE_ESCALATED_PRIORITY",
    "ACK_STATE_OPEN",
    "ACK_STATE_RESOLVED",
    "ACK_STATE_SNOOZED",
    "CATEGORY_APPROVAL_NEEDED",
    "CATEGORY_EXTERNAL_DEPENDENCY",
    "CATEGORY_INFORMATIONAL",
    "CATEGORY_REVIEW_NEEDED",
    "CATEGORY_UNSAFE_TO_CONTINUE",
    "ESCALATION_INBOX_ACTION_SCHEMA",
    "ESCALATION_INBOX_SCHEMA",
    "append_escalation_action",
    "build_escalation_inbox_payload",
    "escalation_inbox_actions_dir",
    "escalation_inbox_dir",
    "merge_escalation_actions",
    "record_escalation_acknowledge",
    "record_escalation_priority",
    "record_escalation_resolve",
    "record_escalation_snooze",
    "render_escalation_inbox_markdown",
    "run_escalation_inbox",
    "write_escalation_inbox_artifacts",
]
