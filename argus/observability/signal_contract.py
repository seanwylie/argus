"""
Deterministic **signal contract** evaluation: golden (product-type) vs mission (optimization) signals.

Read-only: loads ``runs/signals/latest`` and ``runs/temporal/latest``; does not run collectors.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.serialize import dumps_json, to_jsonable
from argus.mission.mission import load_mission_registry
from argus.mission.product_mission import parse_product_mission_from_yaml, read_product_yaml
from argus.signals.persistence import latest_path, load_latest_bundle
from argus.temporal.persistence import load_latest_temporal_bundle, temporal_latest_path

SIGNAL_CONTRACT_EVALUATION_SCHEMA = "argus.signal_contract_evaluation.v1"

# --- Product-type buckets (explicit; no fuzzy inference) ---

PRODUCT_TYPE_BUCKET_SAAS_API = "saas_api"
PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT = "website_content"
PRODUCT_TYPE_BUCKET_UTILITY_CLI = "utility_cli"
PRODUCT_TYPE_BUCKET_MOBILE_COMPANION = "mobile_companion"

# Map product.yaml ``type`` (lowercased) → bucket id.
PRODUCT_TYPE_TO_BUCKET: dict[str, str] = {
    "micro_saas": PRODUCT_TYPE_BUCKET_SAAS_API,
    "saas": PRODUCT_TYPE_BUCKET_SAAS_API,
    "api": PRODUCT_TYPE_BUCKET_SAAS_API,
    "api_service": PRODUCT_TYPE_BUCKET_SAAS_API,
    "backend_service": PRODUCT_TYPE_BUCKET_SAAS_API,
    "website": PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT,
    "content": PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT,
    "content_site": PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT,
    "blog": PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT,
    "marketing_site": PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT,
    "cli": PRODUCT_TYPE_BUCKET_UTILITY_CLI,
    "utility": PRODUCT_TYPE_BUCKET_UTILITY_CLI,
    "tool": PRODUCT_TYPE_BUCKET_UTILITY_CLI,
    "desktop_tool": PRODUCT_TYPE_BUCKET_UTILITY_CLI,
    "mobile": PRODUCT_TYPE_BUCKET_MOBILE_COMPANION,
    "mobile_companion": PRODUCT_TYPE_BUCKET_MOBILE_COMPANION,
    "react_native": PRODUCT_TYPE_BUCKET_MOBILE_COMPANION,
    "flutter_app": PRODUCT_TYPE_BUCKET_MOBILE_COMPANION,
}

_GOLDEN_BY_BUCKET: dict[str, tuple[str, ...]] = {
    PRODUCT_TYPE_BUCKET_SAAS_API: ("latency", "availability", "error_rate", "throughput"),
    PRODUCT_TYPE_BUCKET_WEBSITE_CONTENT: ("availability", "page_performance", "traffic", "error_rate"),
    PRODUCT_TYPE_BUCKET_UTILITY_CLI: ("execution_success", "duration", "failure_rate"),
    PRODUCT_TYPE_BUCKET_MOBILE_COMPANION: ("crash_rate", "startup_latency", "api_availability"),
}

_MISSION_SIGNALS: dict[str, tuple[str, ...]] = {
    "revenue": ("conversion_rate", "arppu", "retention", "funnel_completion"),
    "engagement": ("dau_wau", "repeat_usage", "session_length"),
    "education": ("completion_rate", "return_rate", "learning_progress"),
}

_STALE_STATUSES = frozenset({"stale", "expired"})


def resolve_product_type_bucket(product_type: str | None) -> tuple[str, bool]:
    """
    Return ``(bucket_id, used_default)``.

    Unknown / empty ``product_type`` maps to ``saas_api`` (conservative default) with ``used_default=True``.
    """
    if not product_type or not str(product_type).strip():
        return PRODUCT_TYPE_BUCKET_SAAS_API, True
    key = str(product_type).strip().lower()
    if key in PRODUCT_TYPE_TO_BUCKET:
        return PRODUCT_TYPE_TO_BUCKET[key], False
    return PRODUCT_TYPE_BUCKET_SAAS_API, True


def golden_signals_for_product_type(bucket: str) -> tuple[str, ...]:
    """Required golden signal ids for a product-type bucket."""
    return _GOLDEN_BY_BUCKET.get(bucket, _GOLDEN_BY_BUCKET[PRODUCT_TYPE_BUCKET_SAAS_API])


def mission_signals_for_mission(mission_id: str) -> tuple[str, ...]:
    """Recommended mission optimization signal ids (registry profile id)."""
    mid = str(mission_id or "").strip().lower()
    return _MISSION_SIGNALS.get(mid, ())


def _effective_mission_id(repo_root: Path, product_id: str) -> str:
    raw = read_product_yaml(repo_root, product_id)
    spec, _ = parse_product_mission_from_yaml(raw or {})
    if spec is not None:
        return str(spec.objective).strip().lower()
    if raw and isinstance(raw.get("mission_id"), str) and raw["mission_id"].strip():
        return raw["mission_id"].strip().lower()
    reg = load_mission_registry(repo_root)
    return str(reg.get("default_mission_id") or "revenue").strip().lower()


def _record_match_blob(rec: dict[str, Any]) -> str:
    tags = rec.get("tags") if isinstance(rec.get("tags"), list) else []
    tag_s = " ".join(str(t) for t in tags)
    pl = rec.get("payload")
    try:
        pl_s = json.dumps(pl, sort_keys=True) if isinstance(pl, (dict, list)) else str(pl)
    except (TypeError, ValueError):
        pl_s = str(pl)
    can = rec.get("canonical") if isinstance(rec.get("canonical"), dict) else {}
    parts = [
        str(rec.get("signal_type") or ""),
        tag_s,
        pl_s,
        str(can.get("category") or ""),
        str(can.get("signal_id") or ""),
        str(rec.get("source") or ""),
    ]
    return " ".join(parts).lower()


def _rules_for_golden(golden_id: str) -> list[tuple[frozenset[str], tuple[str, ...]]]:
    """
    Each rule: (allowed signal_types, needle substrings).

    A record matches the rule if ``signal_type`` is in allowed (non-empty allowed set)
    and **any** needle appears in the blob (if needles empty, type match alone suffices).
    """
    g = golden_id.strip().lower()
    any_metrics_health = frozenset({"metrics", "health"})
    any_exec = frozenset({"execution"})
    any_analytics = frozenset({"analytics"})
    any_health_exec = frozenset({"health", "execution"})
    any_all = frozenset({"filesystem", "analytics", "cost", "logs", "metrics", "health", "execution", "custom", "temporal"})

    rules: dict[str, list[tuple[frozenset[str], tuple[str, ...]]]] = {
        "latency": [
            (any_metrics_health, ("latency", "p95", "p99", "duration", "response_time", "rt", "ms")),
            (any_exec, ("duration", "latency", "wall")),
        ],
        "availability": [
            (frozenset({"health"}), ("availability", "uptime", "slo", "success_rate", "healthy")),
            (any_metrics_health, ("uptime", "availability", "slo")),
        ],
        "error_rate": [
            (any_health_exec, ("error", "failure", "5xx", "exception")),
            (frozenset({"metrics"}), ("error", "failure_rate")),
        ],
        "throughput": [
            (frozenset({"metrics", "analytics", "health"}), ("throughput", "rps", "qps", "requests", "tps")),
        ],
        "page_performance": [
            (any_analytics, ("lcp", "fcp", "page", "performance", "vitals", "tti")),
            (frozenset({"metrics"}), ("page", "lcp", "web_vitals")),
        ],
        "traffic": [
            (any_analytics, ("traffic", "sessions", "pageview", "visitors", "users")),
            (frozenset({"metrics"}), ("traffic", "sessions", "visitors")),
        ],
        "execution_success": [
            (any_exec, ("success", "exit", "ok", "passed")),
        ],
        "duration": [
            (any_exec, ("duration", "elapsed", "runtime", "ms")),
            (any_metrics_health, ("duration",)),
        ],
        "failure_rate": [
            (any_exec, ("fail", "error", "nonzero")),
        ],
        "crash_rate": [
            (any_health_exec, ("crash", "anr", "fatal")),
            (frozenset({"custom"}), ("crash",)),
        ],
        "startup_latency": [
            (any_health_exec, ("startup", "cold_start", "launch_time")),
            (frozenset({"metrics"}), ("startup", "ttfb")),
        ],
        "api_availability": [
            (frozenset({"health"}), ("api", "availability", "endpoint")),
            (any_metrics_health, ("api", "healthcheck")),
        ],
    }
    return rules.get(g, [(any_all, ())])


def _rules_for_mission_signal(ms_id: str) -> list[tuple[frozenset[str], tuple[str, ...]]]:
    mid = ms_id.strip().lower()
    biz = frozenset({"analytics", "metrics", "custom"})
    any_b = biz | frozenset({"health", "execution"})
    rules_map: dict[str, list[tuple[frozenset[str], tuple[str, ...]]]] = {
        "conversion_rate": [(biz, ("conversion", "cvr", "checkout", "purchase"))],
        "arppu": [(biz, ("arppu", "arpu", "revenue_per"))],
        "retention": [(biz, ("retention", "churn", "cohort"))],
        "funnel_completion": [(biz, ("funnel", "completion", "dropoff"))],
        "dau_wau": [(biz, ("dau", "wau", "mau", "stickiness"))],
        "repeat_usage": [(any_b, ("repeat", "returning", "resurrect"))],
        "session_length": [(biz, ("session", "length", "duration"))],
        "completion_rate": [(biz, ("completion", "finished", "course"))],
        "return_rate": [(biz, ("return", "revisit"))],
        "learning_progress": [(biz, ("progress", "mastery", "lesson"))],
    }
    return rules_map.get(mid, [(frozenset({"analytics", "metrics", "custom", "health"}), (mid.replace("_", " "),))])


def _matches_rule(rec: dict[str, Any], rule: tuple[frozenset[str], tuple[str, ...]]) -> bool:
    types, needles = rule
    st = str(rec.get("signal_type") or "").strip().lower()
    if types and st not in types:
        return False
    blob = _record_match_blob(rec)
    if not needles:
        return True
    return any(n in blob for n in needles)


def _golden_status_for_records(
    golden_id: str,
    records: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Return ``(present|missing|stale, matched_signal_ids)``."""
    rules = _rules_for_golden(golden_id)
    matched: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if any(_matches_rule(rec, r) for r in rules):
            matched.append(rec)
    if not matched:
        return "missing", []
    ids: list[str] = []
    any_stale = False
    any_fresh = False
    for rec in matched:
        can = rec.get("canonical") if isinstance(rec.get("canonical"), dict) else {}
        sid = str(can.get("signal_id") or rec.get("id") or "")
        if sid:
            ids.append(sid)
        fs = str(can.get("freshness_status") or "unknown").strip().lower()
        if fs in _STALE_STATUSES:
            any_stale = True
        elif fs in ("fresh", "ok", "current"):
            any_fresh = True
    if any_stale and not any_fresh:
        return "stale", ids
    if any_stale and any_fresh:
        return "present", ids
    return "present", ids


def _mission_status_for_records(
    ms_id: str,
    records: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    rules = _rules_for_mission_signal(ms_id)
    matched: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        if any(_matches_rule(rec, r) for r in rules):
            matched.append(rec)
    if not matched:
        return "missing", []
    ids: list[str] = []
    for rec in matched:
        can = rec.get("canonical") if isinstance(rec.get("canonical"), dict) else {}
        sid = str(can.get("signal_id") or rec.get("id") or "")
        if sid:
            ids.append(sid)
    return "present", ids


def _records_from_bundle(repo_root: Path, product_id: str) -> tuple[list[dict[str, Any]], str | None]:
    b = load_latest_bundle(repo_root, product_id)
    if b is None:
        return [], None
    out: list[dict[str, Any]] = []
    for r in b.records:
        try:
            raw = to_jsonable(r)
            d = json.loads(dumps_json(raw))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if isinstance(d, dict):
            out.append(d)
    return out, b.collected_at_utc


def _temporal_worst(repo_root: Path, product_id: str) -> str | None:
    raw = load_latest_temporal_bundle(repo_root, product_id)
    if not raw:
        return None
    w = raw.get("worst_freshness_status")
    return str(w).strip() if isinstance(w, str) and w.strip() else None


def _signals_latest_surface_state(*, present: bool, record_count: int) -> str:
    """
    Observable partition only—does **not** diagnose collection bugs vs heuristic mismatch.

    - ``missing_file`` — no ``runs/signals/latest/<id>.json``
    - ``empty_file`` — file exists, zero persisted records
    - ``non_empty_file`` — file exists with ≥1 record (golden may still be ``missing`` if rules do not match)
    """
    if not present:
        return "missing_file"
    if record_count <= 0:
        return "empty_file"
    return "non_empty_file"


def _observe_signal_contract_inputs(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Filesystem truth for signal-contract inputs: distinguishes **no bundle file** from
    **bundle present but heuristic mismatch** (see ``record_count`` vs golden ``missing``).
    """
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    sig_path = latest_path(root, pid)
    sig_rel = sig_path.relative_to(root).as_posix()
    signals_block: dict[str, Any] = {
        "path_relative": sig_rel,
        "present": False,
        "record_count": 0,
        "collected_at_utc": None,
        "surface_state": "missing_file",
    }
    b = load_latest_bundle(root, pid)
    if b is not None:
        signals_block["present"] = True
        signals_block["record_count"] = len(b.records)
        signals_block["collected_at_utc"] = b.collected_at_utc or None
    signals_block["surface_state"] = _signals_latest_surface_state(
        present=bool(signals_block["present"]),
        record_count=int(signals_block["record_count"]),
    )

    tmp_path = temporal_latest_path(root, pid)
    tmp_rel = tmp_path.relative_to(root).as_posix()
    raw_t = load_latest_temporal_bundle(root, pid)
    temporal_block: dict[str, Any] = {
        "path_relative": tmp_rel,
        "present": raw_t is not None,
        "worst_freshness_status": None,
    }
    if isinstance(raw_t, dict):
        w = raw_t.get("worst_freshness_status")
        if isinstance(w, str) and w.strip():
            temporal_block["worst_freshness_status"] = w.strip()
    return {"signals_latest_bundle": signals_block, "temporal_latest_bundle": temporal_block}


def _classify_operability(
    golden_rows: list[dict[str, Any]],
) -> str:
    if any(x.get("status") == "missing" for x in golden_rows):
        return "blocked"
    if any(x.get("status") == "stale" for x in golden_rows):
        return "limited"
    return "sufficient"


def _classify_optimization(
    mission_rows: list[dict[str, Any]],
) -> str:
    if not mission_rows:
        return "sufficient"
    miss = sum(1 for x in mission_rows if x.get("status") == "missing")
    n = len(mission_rows)
    if miss == 0:
        return "sufficient"
    if miss >= max(1, (n + 1) // 2):
        return "thin"
    return "partial"


def _builder_candidates(
    golden_rows: list[dict[str, Any]],
    mission_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in golden_rows:
        st = str(g.get("status") or "")
        gid = str(g.get("signal_id") or "")
        if st == "missing":
            out.append(
                {
                    "kind": "golden_signal_gap",
                    "priority": "high",
                    "signal_id": gid,
                    "note": f"Required golden signal `{gid}` has no matching collected records.",
                }
            )
        elif st == "stale":
            out.append(
                {
                    "kind": "golden_signal_stale",
                    "priority": "high",
                    "signal_id": gid,
                    "note": f"Golden signal `{gid}` matched only stale/expired canonical rows — refresh collection.",
                }
            )
    for m in mission_rows:
        if str(m.get("status") or "") == "missing":
            mid = str(m.get("signal_id") or "")
            out.append(
                {
                    "kind": "mission_signal_gap",
                    "priority": "medium",
                    "signal_id": mid,
                    "note": f"Recommended mission signal `{mid}` has no matching collected records.",
                }
            )
    return out


def evaluate_signal_contract(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Build ``argus.signal_contract_evaluation.v1`` for one product.

    Deterministic: same repo state → same payload (aside from ``evaluated_at_utc``);
    ``inputs_observed`` matches the tree (paths, presence, counts).
    """
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    evaluated_at = datetime.now(timezone.utc).isoformat()
    raw = read_product_yaml(root, pid) or {}
    ptype = raw.get("type")
    ptype_s = str(ptype).strip().lower() if ptype is not None else ""
    bucket, used_default = resolve_product_type_bucket(ptype_s if ptype else None)

    mission_id = _effective_mission_id(root, pid)
    required_golden = list(golden_signals_for_product_type(bucket))
    recommended_mission = list(mission_signals_for_mission(mission_id))

    records, signals_collected_at = _records_from_bundle(root, pid)
    temporal_worst = _temporal_worst(root, pid)

    golden_rows: list[dict[str, Any]] = []
    for gid in required_golden:
        st, matched_ids = _golden_status_for_records(gid, records)
        golden_rows.append(
            {
                "signal_id": gid,
                "status": st,
                "matched_signal_ids": sorted(set(matched_ids)),
            }
        )

    mission_rows: list[dict[str, Any]] = []
    for mid in recommended_mission:
        st, matched_ids = _mission_status_for_records(mid, records)
        mission_rows.append(
            {
                "signal_id": mid,
                "status": st,
                "matched_signal_ids": sorted(set(matched_ids)),
            }
        )

    op = _classify_operability(golden_rows)
    opt = _classify_optimization(mission_rows)
    candidates = _builder_candidates(golden_rows, mission_rows)

    present_g = [g["signal_id"] for g in golden_rows if g.get("status") == "present"]
    missing_g = [g["signal_id"] for g in golden_rows if g.get("status") == "missing"]
    stale_g = [g["signal_id"] for g in golden_rows if g.get("status") == "stale"]
    present_m = [g["signal_id"] for g in mission_rows if g.get("status") == "present"]
    missing_m = [g["signal_id"] for g in mission_rows if g.get("status") == "missing"]

    return {
        "schema": SIGNAL_CONTRACT_EVALUATION_SCHEMA,
        "evaluated_at_utc": evaluated_at,
        "product_id": pid,
        "product_type_raw": ptype_s or None,
        "product_type_bucket": bucket,
        "product_type_bucket_defaulted": used_default,
        "mission_id": mission_id,
        "signals_latest_collected_at_utc": signals_collected_at,
        "temporal_worst_freshness_status": temporal_worst,
        "required_golden_signals": required_golden,
        "golden_signal_status": golden_rows,
        "present_golden_signals": present_g,
        "missing_golden_signals": missing_g,
        "stale_golden_signals": stale_g,
        "recommended_mission_signals": recommended_mission,
        "mission_signal_status": mission_rows,
        "present_mission_signals": present_m,
        "missing_mission_signals": missing_m,
        "operability_status": op,
        "optimization_status": opt,
        "builder_task_candidates": candidates,
        "inputs_observed": _observe_signal_contract_inputs(root, pid),
        "inputs": {
            "signals_bundle": "runs/signals/latest/<product_id>.json",
            "temporal_bundle": "runs/temporal/latest/<product_id>.json",
            "note": "Matching uses signal_type, tags, payload, and canonical.category/signal_id substrings — v1 heuristic.",
        },
    }


def compute_signal_contract_hint(operability_status: str, optimization_status: str) -> str:
    """Compact machine hint for queue / dashboards (deterministic)."""
    o = str(operability_status or "").strip().lower()
    opt = str(optimization_status or "").strip().lower()
    if o == "blocked":
        return "missing_golden"
    if o == "limited":
        return "golden_stale"
    if o == "sufficient" and opt == "thin":
        return "golden_ok_mission_thin"
    if o == "sufficient" and opt == "partial":
        return "golden_ok_mission_partial"
    return "sufficient"


def compact_signal_contract_row_fields(repo_root: Path, product_id: str) -> dict[str, Any]:
    """
    Per-queue-row fields derived from :func:`evaluate_signal_contract` (same truth as product readiness).

    Adds: operability/optimization status, hint, and golden gap lists for escalation alignment.
    """
    sc = evaluate_signal_contract(repo_root, product_id)
    op = str(sc.get("operability_status") or "")
    opt = str(sc.get("optimization_status") or "")
    obs = sc.get("inputs_observed")
    surf: str | None = None
    if isinstance(obs, dict):
        sb = obs.get("signals_latest_bundle")
        if isinstance(sb, dict):
            s = sb.get("surface_state")
            surf = str(s).strip() if isinstance(s, str) and s.strip() else None
    return {
        "signal_contract_operability_status": op,
        "signal_contract_optimization_status": opt,
        "signal_contract_hint": compute_signal_contract_hint(op, opt),
        "signal_contract_missing_golden_signals": list(sc.get("missing_golden_signals") or []),
        "signal_contract_stale_golden_signals": list(sc.get("stale_golden_signals") or []),
        "signal_contract_signals_surface_state": surf,
    }


def build_signal_contract_summary_from_evaluation(sc: dict[str, Any]) -> dict[str, Any]:
    """Shared shape for product_readiness and documentation (includes builder candidates)."""
    obs = sc.get("inputs_observed")
    surf: str | None = None
    if isinstance(obs, dict):
        sb = obs.get("signals_latest_bundle")
        if isinstance(sb, dict):
            s = sb.get("surface_state")
            surf = str(s).strip() if isinstance(s, str) and s.strip() else None
    return {
        "schema_ref": SIGNAL_CONTRACT_EVALUATION_SCHEMA,
        "label": "Signal contract (required operability + recommended mission optimization)",
        "product_type_bucket": sc.get("product_type_bucket"),
        "mission_id": sc.get("mission_id"),
        "operability_status": sc.get("operability_status"),
        "optimization_status": sc.get("optimization_status"),
        "signals_latest_surface_state": surf,
        "golden_missing": sc.get("missing_golden_signals") or [],
        "golden_stale": sc.get("stale_golden_signals") or [],
        "mission_missing": sc.get("missing_mission_signals") or [],
        "builder_task_candidates": sc.get("builder_task_candidates") or [],
        "builder_task_candidates_count": len(sc.get("builder_task_candidates") or []),
    }


def signal_contract_context_for_escalation(
    repo_root: Path | None,
    product_id: str | None,
    queue_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    Prefer operator queue row fields (no extra work); fall back to evaluation when needed.

    Returns operability, missing/stale golden lists, and ``source`` for inspectability.
    """
    qe = queue_entry if isinstance(queue_entry, dict) else None
    if qe and str(qe.get("signal_contract_operability_status") or "").strip():
        ss = qe.get("signal_contract_signals_surface_state")
        surf = str(ss).strip() if isinstance(ss, str) and ss.strip() else None
        return {
            "operability_status": str(qe.get("signal_contract_operability_status") or "").strip(),
            "optimization_status": str(qe.get("signal_contract_optimization_status") or "").strip(),
            "missing_golden": list(qe.get("signal_contract_missing_golden_signals") or []),
            "stale_golden": list(qe.get("signal_contract_stale_golden_signals") or []),
            "hint": str(qe.get("signal_contract_hint") or "").strip() or None,
            "signals_surface_state": surf,
            "source": "operator_queue_row",
        }
    pid = str(product_id or "").strip()
    if repo_root is None or not pid:
        return {
            "operability_status": None,
            "optimization_status": None,
            "missing_golden": [],
            "stale_golden": [],
            "hint": None,
            "signals_surface_state": None,
            "source": "unavailable",
        }
    sc = evaluate_signal_contract(repo_root, pid)
    op = str(sc.get("operability_status") or "")
    opt = str(sc.get("optimization_status") or "")
    obs = sc.get("inputs_observed")
    surf_ev: str | None = None
    if isinstance(obs, dict):
        sb = obs.get("signals_latest_bundle")
        if isinstance(sb, dict):
            s = sb.get("surface_state")
            surf_ev = str(s).strip() if isinstance(s, str) and s.strip() else None
    return {
        "operability_status": op,
        "optimization_status": opt,
        "missing_golden": list(sc.get("missing_golden_signals") or []),
        "stale_golden": list(sc.get("stale_golden_signals") or []),
        "hint": compute_signal_contract_hint(op, opt),
        "signals_surface_state": surf_ev,
        "source": "evaluate_signal_contract",
    }


def _signal_coherence_markdown_lines(payload: dict[str, Any]) -> list[str]:
    """
    Operator-facing reconciliation: multiple fields are **simultaneously valid** in different scopes.

    Does not invent collection failures or root causes—only relates definitions already in this module.
    """
    op = str(payload.get("operability_status") or "")
    opt = str(payload.get("optimization_status") or "")
    hint = compute_signal_contract_hint(op, opt)
    obs = payload.get("inputs_observed")
    surf: str | None = None
    tmp_present: bool | None = None
    tmp_worst: str | None = None
    if isinstance(obs, dict):
        sb = obs.get("signals_latest_bundle")
        if isinstance(sb, dict):
            s = sb.get("surface_state")
            surf = str(s).strip() if isinstance(s, str) and s.strip() else None
        tb = obs.get("temporal_latest_bundle")
        if isinstance(tb, dict):
            tmp_present = bool(tb.get("present"))
            w = tb.get("worst_freshness_status")
            if isinstance(w, str) and w.strip():
                tmp_worst = w.strip()
    tw = payload.get("temporal_worst_freshness_status")
    tw_top = str(tw).strip() if isinstance(tw, str) and tw.strip() else None
    lines = [
        "## Signal coherence (how to read this report)",
        "",
        "These fields answer **different questions**. None replaces another:",
        "",
        f"- **Compact hint** (here: `{hint}`): from `compute_signal_contract_hint(operability, optimization)` only — **heuristic summary** of operability/mission rows, **not** filesystem or collector outcome.",
    ]
    if surf:
        lines.append(
            f"- **signals_latest surface_state** (`{surf}`): **on-disk** `runs/signals/latest/<product>.json` — "
            "`missing_file` vs `empty_file` vs `non_empty_file` (see `_signals_latest_surface_state`)."
        )
    else:
        lines.append(
            "- **signals_latest surface_state:** _(unavailable — `inputs_observed` incomplete)_"
        )
    if tmp_present is not None:
        extra = f", worst in bundle `{tmp_worst}`" if tmp_worst else ""
        lines.append(
            f"- **Temporal latest sidecar:** present=`{tmp_present}`{extra} — **freshness aggregation** for that path; "
            "orthogonal to golden heuristic match."
        )
    if tw_top:
        lines.append(
            f"- **temporal_worst_freshness_status** (`{tw_top}`): worst freshness rolled up for evaluation context — still not a collection-error signal."
        )
    lines.extend(
        [
            "",
            "**Operability `blocked` / `limited`:** computed from golden (and stale rules) **over records loaded** from the signals bundle — "
            "not proof that collectors failed; use `surface_state` to see whether a latest file existed and whether it had rows.",
            "",
        ]
    )
    return lines


def write_signal_contract_artifact(repo_root: Path, product_id: str, payload: dict[str, Any]) -> Path:
    """Write ``runs/debug/signal_contract/<product_id>/latest.json``."""
    root = repo_root.resolve()
    pid = str(product_id or "").strip()
    d = root / "runs" / "debug" / "signal_contract" / pid
    d.mkdir(parents=True, exist_ok=True)
    p = d / "latest.json"
    p.write_text(dumps_json(payload) + "\n", encoding="utf-8")
    return p


def render_signal_contract_markdown(payload: dict[str, Any]) -> str:
    pid = payload.get("product_id", "")
    lines = [
        f"# Signal contract — `{pid}`",
        "",
        f"**Schema:** `{payload.get('schema')}`",
        f"**Evaluated (UTC):** {payload.get('evaluated_at_utc')}",
        "",
        "## Summary",
        "",
        f"- **Product type (raw):** `{payload.get('product_type_raw')}`",
        f"- **Product type bucket:** `{payload.get('product_type_bucket')}` (defaulted: `{payload.get('product_type_bucket_defaulted')}`)",
        f"- **Mission id:** `{payload.get('mission_id')}`",
        f"- **Operability:** `{payload.get('operability_status')}`",
        f"- **Optimization:** `{payload.get('optimization_status')}`",
        "",
        "## Inputs observed (on disk)",
        "",
    ]
    obs = payload.get("inputs_observed")
    if isinstance(obs, dict):
        sig = obs.get("signals_latest_bundle") if isinstance(obs.get("signals_latest_bundle"), dict) else {}
        tmp = obs.get("temporal_latest_bundle") if isinstance(obs.get("temporal_latest_bundle"), dict) else {}
        lines.extend(
            [
                f"- **Signals latest:** `{sig.get('path_relative')}` — surface_state=`{sig.get('surface_state')}`, "
                f"present={sig.get('present')}, record_count={sig.get('record_count')}, "
                f"collected_at_utc={sig.get('collected_at_utc')}",
                f"- **Temporal latest:** `{tmp.get('path_relative')}` — present={tmp.get('present')}, "
                f"worst_freshness_status={tmp.get('worst_freshness_status')}",
                "",
            ]
        )
    else:
        lines.extend(["- _(inputs_observed missing)_", ""])
    lines.extend(_signal_coherence_markdown_lines(payload))
    lines.extend(
        [
        "## Golden (required)",
        "",
        f"- Present: `{payload.get('present_golden_signals')}`",
        f"- Missing: `{payload.get('missing_golden_signals')}`",
        f"- Stale: `{payload.get('stale_golden_signals')}`",
        "",
        "## Mission (recommended)",
        "",
        f"- Present: `{payload.get('present_mission_signals')}`",
        f"- Missing: `{payload.get('missing_mission_signals')}`",
        "",
        "## Builder task candidates (descriptive only)",
        "",
        "```",
        dumps_json(payload.get("builder_task_candidates") or []),
        "```",
        "",
        ],
    )
    return "\n".join(lines).rstrip() + "\n"


__all__ = [
    "SIGNAL_CONTRACT_EVALUATION_SCHEMA",
    "build_signal_contract_summary_from_evaluation",
    "compact_signal_contract_row_fields",
    "compute_signal_contract_hint",
    "evaluate_signal_contract",
    "golden_signals_for_product_type",
    "mission_signals_for_mission",
    "render_signal_contract_markdown",
    "resolve_product_type_bucket",
    "signal_contract_context_for_escalation",
    "write_signal_contract_artifact",
]
