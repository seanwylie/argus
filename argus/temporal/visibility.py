"""
Pipeline and artifact freshness for operators (dashboard + doctor).

Uses filesystem mtimes and bundle timestamps — safe when files are missing (no exceptions).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import FindingKind
from argus.core.models.product import ProductNode
from argus.decision.persistence import latest_product_path
from argus.findings.rules.temporal import TEMPORAL_FINDING_KINDS as RULE_TEMPORAL_FINDING_KINDS
from argus.signals.persistence import latest_path as signals_latest_path
from argus.temporal.models import FreshnessBucket, FreshnessStatus
from argus.temporal.persistence import load_latest_temporal_bundle, temporal_latest_path
from argus.temporal.recency import (
    worst_freshness_bucket_from_signal_dicts,
    worst_freshness_status_from_signal_dicts,
)

logger = logging.getLogger(__name__)

# Signals bundle older than this (by collected_at_utc) is flagged as stale age.
SIGNAL_COLLECTION_MAX_AGE_SEC = 30 * 86400
# If collected_at_utc is this far in the future vs reference time, flag clock skew / bad timestamp.
FUTURE_COLLECTION_TOLERANCE_SEC = 300


def product_expects_signals(node: ProductNode) -> bool:
    """True when product.yaml declares at least one enabled signal source."""
    for s in node.signals:
        if getattr(s, "enabled", True):
            return True
    return False


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()
    except OSError:
        return None


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not str(ts).strip():
        return None
    try:
        t = str(ts).strip().replace("Z", "+00:00")
        return datetime.fromisoformat(t)
    except (ValueError, TypeError):
        return None


def _signals_bundle_parse_ok(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(raw, dict)


def analyze_signals_bundle(path: Path, expected_product_id: str) -> dict[str, Any]:
    """
    Structural + semantic checks for ``runs/signals/latest/<id>.json``.

    Does not raise — surfaces issues for dashboard/doctor instead of silent drops.
    """
    issues: list[str] = []
    if not path.is_file():
        return {
            "parse_ok": False,
            "issues": ["file_missing"],
            "loader_ok": False,
            "collected_at_utc": None,
        }
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return {
            "parse_ok": False,
            "issues": [f"invalid_json:{e!s}"],
            "loader_ok": False,
            "collected_at_utc": None,
        }
    if not isinstance(raw, dict):
        return {
            "parse_ok": False,
            "issues": ["root_not_object"],
            "loader_ok": False,
            "collected_at_utc": None,
        }

    collected_at = str(raw.get("collected_at_utc") or "") or None

    if "records" in raw and not isinstance(raw["records"], list):
        issues.append("records_must_be_array")
    raw_records = raw.get("records")
    if isinstance(raw_records, list):
        for i, item in enumerate(raw_records):
            if not isinstance(item, dict):
                issues.append(f"records[{i}]_not_object")

    pid = raw.get("product_id")
    if pid is not None and str(pid).strip() and str(pid) != expected_product_id:
        issues.append("product_id_mismatch")

    if not str(raw.get("collected_at_utc", "")).strip():
        issues.append("missing_collected_at_utc")

    loader_ok = False
    try:
        from argus.signals.persistence import load_bundle_file

        load_bundle_file(path)
        loader_ok = True
    except (OSError, ValueError, TypeError, KeyError) as e:
        issues.append(f"record_validation:{type(e).__name__}:{e!s}"[:240])

    return {
        "parse_ok": True,
        "issues": issues,
        "loader_ok": loader_ok,
        "semantically_ok": len(issues) == 0,
        "collected_at_utc": collected_at,
    }


def compute_product_temporal_visibility(
    repo_root: Path,
    product_id: str,
    node: ProductNode,
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """
    Single-product temporal summary for dashboard rows.

    ``overall`` is one of: ``not_required``, ``missing``, ``stale``, ``current``, ``unknown``.
    """
    root = repo_root.resolve()
    ref = reference_time or datetime.now(timezone.utc)
    cfg_path = (root / node.config_path).resolve()
    py_mtime = _mtime_iso(cfg_path) if cfg_path.is_file() else None

    if not product_expects_signals(node):
        tp_nr = temporal_latest_path(root, product_id)
        orphan_tb = tp_nr.is_file()
        nr_flags: list[str] = []
        if orphan_tb:
            nr_flags.append("temporal_artifact_without_signal_pipeline")
        return {
            "product_id": product_id,
            "temporal_requirement": "none",
            "temporal_pipeline_expected": False,
            "overall": "not_required",
            "flags": sorted(nr_flags),
            "operator_note": (
                "No enabled signal sources in product.yaml — Argus does not expect signals, findings, "
                "or decisions from the temporal collection pipeline for this product."
            ),
            "signals": {
                "status": "not_required",
                "bundle_mtime_utc": None,
                "collected_at_utc": None,
                "parse_ok": True,
                "validation_issues": [],
            },
            "findings": {"status": "not_required", "mtime_utc": None},
            "decisions": {"status": "not_required", "mtime_utc": None},
            "pipeline": {"coherent": True, "warnings": []},
            "temporal_bundle": {
                "present": orphan_tb,
                "worst_freshness_bucket": None,
                "worst_freshness_status": None,
                "malformed": False,
                "unexpected_for_this_product": orphan_tb,
            },
            "product_yaml_mtime_utc": py_mtime,
        }

    sp = signals_latest_path(root, product_id)
    flags: list[str] = []
    pipe_warnings: list[str] = []
    collected_at: str | None = None
    collection_analysis: dict[str, Any] | None = None
    parse_ok = False
    validation_issues: list[str] = []
    if sp.is_file():
        collection_analysis = analyze_signals_bundle(sp, product_id)
        parse_ok = bool(collection_analysis.get("parse_ok"))
        validation_issues = list(collection_analysis.get("issues") or [])
        if parse_ok:
            collected_at = collection_analysis.get("collected_at_utc")
        if validation_issues:
            flags.append("signals_bundle_validation_issues")
        if collection_analysis and not collection_analysis.get("loader_ok", False):
            flags.append("signals_records_failed_validation")

    sm = _mtime_iso(sp) if sp.is_file() else None
    fp = root / "runs" / "findings" / "latest" / f"{product_id}.json"
    fm = _mtime_iso(fp) if fp.is_file() else None
    dp = latest_product_path(root, product_id)
    dm = _mtime_iso(dp) if dp.is_file() else None

    if not sp.is_file():
        flags.append("missing_signals")
    elif not parse_ok:
        flags.append("signals_bundle_invalid_json")

    sig_status = "missing"
    if sp.is_file():
        if (
            not parse_ok
            or validation_issues
            or (collection_analysis is not None and not collection_analysis.get("loader_ok", False))
        ):
            sig_status = "unknown"
        else:
            sig_status = "current"
            try:
                if cfg_path.is_file() and sp.stat().st_mtime < cfg_path.stat().st_mtime:
                    sig_status = "stale"
                    flags.append("signals_older_than_product_yaml")
            except OSError as e:
                logger.warning(
                    "Could not compare signals vs product.yaml mtimes (%s, %s): %s",
                    sp,
                    cfg_path,
                    e,
                )
            coll_dt = _parse_iso(collected_at)
            if coll_dt is not None:
                cu = coll_dt.astimezone(timezone.utc)
                ref_u = ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
                if cu.tzinfo is None:
                    cu = cu.replace(tzinfo=timezone.utc)
                skew_future_s = (cu - ref_u).total_seconds()
                if skew_future_s > FUTURE_COLLECTION_TOLERANCE_SEC:
                    flags.append("collection_timestamp_in_future")
                    if sig_status == "current":
                        sig_status = "stale"
                age_s = max(0.0, (ref_u - cu).total_seconds())
                if age_s > SIGNAL_COLLECTION_MAX_AGE_SEC:
                    flags.append("signals_stale_by_age")
                    if sig_status == "current":
                        sig_status = "stale"

    fin_status: str = "missing" if not fp.is_file() else "current"
    if not fp.is_file():
        flags.append("missing_findings")

    dec_status: str = "missing" if not dp.is_file() else "current"
    if not dp.is_file():
        flags.append("missing_decisions")

    if sp.is_file() and fp.is_file() and parse_ok:
        try:
            if fp.stat().st_mtime < sp.stat().st_mtime - 0.001:
                pipe_warnings.append(
                    "Findings bundle is older than the latest signals bundle — re-run findings generate."
                )
                flags.append("pipeline_findings_behind_signals")
        except OSError as e:
            logger.warning(
                "Could not compare findings vs signals mtimes (%s, %s): %s",
                fp,
                sp,
                e,
            )

    if fp.is_file() and dp.is_file():
        try:
            if dp.stat().st_mtime < fp.stat().st_mtime - 0.001:
                pipe_warnings.append(
                    "Decisions bundle is older than findings — decisions may reflect stale context."
                )
                flags.append("decisions_depend_on_stale_findings")
        except OSError as e:
            logger.warning(
                "Could not compare decisions vs findings mtimes (%s, %s): %s",
                dp,
                fp,
                e,
            )

    tp = temporal_latest_path(root, product_id)
    tb = load_latest_temporal_bundle(root, product_id)
    t_present = tb is not None
    worst_b: str | None = None
    worst_s: str | None = None
    t_malformed = False
    if tp.is_file() and tb is None:
        t_malformed = True
        flags.append("temporal_bundle_malformed")
    if sp.is_file() and parse_ok and not tp.is_file():
        flags.append("missing_temporal_bundle")
    if isinstance(tb, dict):
        sigs = tb.get("signals")
        if isinstance(sigs, list):
            worst_b = worst_freshness_bucket_from_signal_dicts(sigs)
            worst_s = worst_freshness_status_from_signal_dicts(sigs)
            if worst_b == FreshnessBucket.STALE.value:
                flags.append("temporal_signals_stale")
                if sig_status == "current":
                    sig_status = "stale"
            if worst_s in (FreshnessStatus.STALE.value, FreshnessStatus.EXPIRED.value):
                flags.append("temporal_freshness_status_degraded")
                if sig_status == "current":
                    sig_status = "stale"

    # Roll up overall
    overall = "current"
    if sig_status == "unknown":
        overall = "unknown"
    elif not sp.is_file():
        overall = "missing"
    elif fin_status == "missing" or dec_status == "missing":
        overall = "missing"
    elif (
        sig_status == "stale"
        or "pipeline_findings_behind_signals" in flags
        or "decisions_depend_on_stale_findings" in flags
        or "temporal_signals_stale" in flags
    ):
        overall = "stale"

    return {
        "product_id": product_id,
        "temporal_requirement": "signals",
        "overall": overall,
        "flags": sorted(set(flags)),
        "temporal_pipeline_expected": True,
        "signals": {
            "status": sig_status,
            "bundle_mtime_utc": sm,
            "collected_at_utc": collected_at,
            "parse_ok": parse_ok,
            "validation_issues": validation_issues,
            "analysis": collection_analysis,
        },
        "findings": {"status": fin_status, "mtime_utc": fm},
        "decisions": {"status": dec_status, "mtime_utc": dm},
        "pipeline": {"coherent": len(pipe_warnings) == 0, "warnings": pipe_warnings},
        "temporal_bundle": {
            "present": t_present,
            "worst_freshness_bucket": worst_b,
            "worst_freshness_status": worst_s,
            "malformed": t_malformed,
            "path_repo": f"runs/temporal/latest/{product_id}.json",
        },
        "product_yaml_mtime_utc": py_mtime,
    }


def build_temporal_dashboard_block(
    repo_root: Path,
    valid_products: dict[str, Any],
    *,
    reference_time: datetime | None = None,
) -> dict[str, Any]:
    """Aggregate temporal block for ``build_dashboard_payload``."""
    root = repo_root.resolve()
    ref = reference_time or datetime.now(timezone.utc)
    rows: list[dict[str, Any]] = []
    summary = {"current": 0, "stale": 0, "missing": 0, "unknown": 0, "not_required": 0}
    max_collected: datetime | None = None
    global_flags: list[str] = []

    for pid in sorted(valid_products.keys()):
        rec = valid_products[pid]
        node = rec.node
        vis = compute_product_temporal_visibility(root, pid, node, reference_time=ref)
        rows.append(vis)
        o = vis["overall"]
        if o in summary:
            summary[o] += 1
        ca = vis["signals"].get("collected_at_utc")
        dt = _parse_iso(ca) if ca else None
        if dt:
            if max_collected is None or dt > max_collected:
                max_collected = dt

    pp = root / "runs" / "decisions" / "latest" / "portfolio.json"
    findings_dir = root / "runs" / "findings" / "latest"
    max_f: float | None = None
    if findings_dir.is_dir():
        for f in findings_dir.glob("*.json"):
            try:
                mt = f.stat().st_mtime
                if max_f is None or mt > max_f:
                    max_f = mt
            except OSError as e:
                logger.debug("Skipping findings file after stat error: %s (%s)", f, e)
                continue
    try:
        if pp.is_file() and max_f is not None and pp.stat().st_mtime < max_f:
            global_flags.append("portfolio_json_older_than_newest_findings")
    except OSError as e:
        logger.warning("Could not compare portfolio.json vs findings mtimes (%s): %s", pp, e)

    return {
        "schema": "argus.dashboard_temporal.v2",
        "generated_at_utc": ref.isoformat(),
        "reference_time_utc": ref.isoformat(),
        "last_signal_collection_max_utc": max_collected.isoformat() if max_collected else None,
        "tolerances": {
            "future_collection_timestamp_sec": FUTURE_COLLECTION_TOLERANCE_SEC,
            "signal_collection_max_age_days": SIGNAL_COLLECTION_MAX_AGE_SEC // 86400,
        },
        "summary_counts": summary,
        "global_flags": global_flags,
        "products": rows,
    }


# Rule-engine temporal kinds plus inactivity (operator-facing recency).
RECENT_TEMPORAL_PANEL_KINDS: frozenset[FindingKind] = RULE_TEMPORAL_FINDING_KINDS | frozenset(
    {FindingKind.INACTIVITY}
)


def collect_recent_temporal_findings(
    repo_root: Path,
    product_ids: list[str],
    *,
    limit: int = 16,
) -> list[dict[str, Any]]:
    """Surface temporal / recency-related findings from latest bundles."""
    from argus.findings.persistence import load_latest_findings

    root = repo_root.resolve()
    out: list[dict[str, Any]] = []
    for pid in product_ids:
        fb = load_latest_findings(root, pid)
        if not fb:
            continue
        for f in fb.findings:
            if f.kind not in RECENT_TEMPORAL_PANEL_KINDS:
                continue
            out.append(
                {
                    "finding_id": f.id,
                    "product_id": pid,
                    "kind": f.kind.value,
                    "title": f.title,
                    "summary": (f.summary or "")[:320],
                    "severity": f.severity.value,
                }
            )
            if len(out) >= limit:
                return out
    return out


def build_doctor_temporal_report(repo_root: Path, valid_products: dict[str, Any]) -> dict[str, Any]:
    """
    Structured temporal checks for ``argus doctor`` (text + JSON).

    Categories:
    - ``missing_current`` — required artifact absent
    - ``stale_current`` — artifact exists but out of date vs upstream / threshold
    - ``malformed`` — JSON present but invalid
    - ``unknown`` — could not classify (e.g. signals JSON invalid)
    - ``not_required`` — product has no enabled signals (informational)
    - ``integrity`` — mismatched IDs, impossible timestamps, or bundle shape vs loader
    """
    root = repo_root.resolve()
    ref = datetime.now(timezone.utc)
    checks: list[dict[str, Any]] = []
    summary = {
        "missing_current": 0,
        "stale_current": 0,
        "not_required": 0,
        "malformed": 0,
        "integrity": 0,
    }

    not_required_ids: list[str] = []

    for pid in sorted(valid_products.keys()):
        rec = valid_products[pid]
        vis = compute_product_temporal_visibility(root, pid, rec.node, reference_time=ref)
        req = vis["temporal_requirement"]
        flags = list(vis["flags"])

        if req == "none":
            summary["not_required"] += 1
            not_required_ids.append(pid)
            if "temporal_artifact_without_signal_pipeline" in flags:
                summary["integrity"] += 1
                checks.append(
                    {
                        "product_id": pid,
                        "category": "integrity",
                        "code": "temporal_artifact_without_signals",
                        "message": (
                            f"{pid}: `runs/temporal/latest/{pid}.json` exists but this product has no "
                            "enabled signal sources — remove the stray file or enable signals in product.yaml"
                        ),
                        "severity": "warning",
                    }
                )
            continue

        if "signals_bundle_invalid_json" in flags:
            summary["malformed"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "malformed",
                    "code": "signals_bundle_invalid_json",
                    "message": f"{pid}: signals latest bundle is not valid JSON — re-run signals collect",
                    "severity": "warning",
                }
            )

        if "missing_signals" in flags:
            summary["missing_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "missing_current",
                    "code": "missing_signals",
                    "message": f"{pid}: no saved signals bundle — run `argus signals collect {pid}`",
                    "severity": "warning",
                }
            )
            downstream: list[str] = []
            if "missing_findings" in flags:
                downstream.append("findings bundle missing")
            if "missing_decisions" in flags:
                downstream.append("decisions bundle missing")
            if downstream:
                checks.append(
                    {
                        "product_id": pid,
                        "category": "missing_current",
                        "code": "pipeline_incomplete_while_signals_missing",
                        "message": (
                            f"{pid}: pipeline also incomplete — {', '.join(downstream)} "
                            "(fix signals collection first, then regenerate downstream artifacts)"
                        ),
                        "severity": "info",
                    }
                )

        if (
            "signals_bundle_validation_issues" in flags
            or "signals_records_failed_validation" in flags
        ):
            summary["integrity"] += 1
            iss = vis.get("signals", {}).get("validation_issues") or []
            checks.append(
                {
                    "product_id": pid,
                    "category": "integrity",
                    "code": "signals_bundle_validation",
                    "message": (
                        f"{pid}: signals bundle failed structural/loader validation — "
                        + (", ".join(iss[:8]) if iss else "see runs/signals/latest file")
                    ),
                    "severity": "warning",
                }
            )

        if "collection_timestamp_in_future" in flags:
            summary["integrity"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "integrity",
                    "code": "collection_timestamp_in_future",
                    "message": (
                        f"{pid}: collected_at_utc is more than {FUTURE_COLLECTION_TOLERANCE_SEC}s "
                        "ahead of local clock — fix bundle timestamp or system clock skew"
                    ),
                    "severity": "warning",
                }
            )

        if "missing_temporal_bundle" in flags and "missing_signals" not in flags:
            summary["missing_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "missing_current",
                    "code": "missing_temporal_bundle",
                    "message": (
                        f"{pid}: signals exist but `runs/temporal/latest/{pid}.json` is absent — "
                        "run `argus signals collect` or `argus temporal refresh {pid}`"
                    ),
                    "severity": "warning",
                }
            )

        if "missing_findings" in flags and "missing_signals" not in flags and vis["signals"]["parse_ok"]:
            summary["missing_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "missing_current",
                    "code": "missing_findings",
                    "message": f"{pid}: no findings bundle — run `argus findings generate {pid}`",
                    "severity": "warning",
                }
            )

        if "missing_decisions" in flags and "missing_signals" not in flags:
            summary["missing_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "missing_current",
                    "code": "missing_decisions",
                    "message": f"{pid}: no decisions bundle — run `argus decisions generate {pid}`",
                    "severity": "warning",
                }
            )

        if "temporal_bundle_malformed" in flags:
            summary["malformed"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "malformed",
                    "code": "temporal_bundle_malformed",
                    "message": f"{pid}: runs/temporal/latest/{pid}.json exists but is invalid JSON",
                    "severity": "warning",
                }
            )

        # Stale / drift (only when not purely missing)
        if "signals_older_than_product_yaml" in flags:
            summary["stale_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "stale_current",
                    "code": "signals_older_than_product_yaml",
                    "message": f"{pid}: signals bundle older than product.yaml — run `argus signals collect {pid}`",
                    "severity": "warning",
                }
            )
        if "signals_stale_by_age" in flags:
            summary["stale_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "stale_current",
                    "code": "signals_stale_by_age",
                    "message": f"{pid}: signal collection timestamp is older than 30 days",
                    "severity": "warning",
                }
            )
        if "pipeline_findings_behind_signals" in flags:
            summary["stale_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "stale_current",
                    "code": "findings_behind_signals",
                    "message": f"{pid}: findings older than latest signals — run `argus findings generate {pid}`",
                    "severity": "warning",
                }
            )
        if "decisions_depend_on_stale_findings" in flags:
            summary["stale_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "stale_current",
                    "code": "decisions_behind_findings",
                    "message": f"{pid}: decisions older than findings — run `argus decisions generate {pid}`",
                    "severity": "warning",
                }
            )
        if "temporal_signals_stale" in flags:
            summary["stale_current"] += 1
            checks.append(
                {
                    "product_id": pid,
                    "category": "stale_current",
                    "code": "temporal_signals_stale",
                    "message": f"{pid}: temporal bundle marks at least one signal as stale — refresh temporal/signals",
                    "severity": "warning",
                }
            )

    pp = root / "runs" / "decisions" / "latest" / "portfolio.json"
    findings_dir = root / "runs" / "findings" / "latest"
    max_f: float | None = None
    if findings_dir.is_dir():
        for f in findings_dir.glob("*.json"):
            try:
                mt = f.stat().st_mtime
                if max_f is None or mt > max_f:
                    max_f = mt
            except OSError as e:
                logger.debug("Skipping findings file after stat error: %s (%s)", f, e)
                continue
    try:
        if max_f is not None and not pp.is_file():
            checks.append(
                {
                    "product_id": None,
                    "category": "missing_current",
                    "code": "missing_portfolio_json",
                    "message": (
                        "findings exist under runs/findings/latest/ but "
                        "runs/decisions/latest/portfolio.json is missing — run `argus decisions portfolio` "
                        "or `argus portfolio refresh`"
                    ),
                    "severity": "warning",
                }
            )
            summary["missing_current"] += 1
        elif pp.is_file() and max_f is not None and pp.stat().st_mtime < max_f:
            checks.append(
                {
                    "product_id": None,
                    "category": "stale_current",
                    "code": "portfolio_behind_findings",
                    "message": "portfolio.json is older than the newest findings bundle — run `argus decisions portfolio`",
                    "severity": "warning",
                }
            )
            summary["stale_current"] += 1
    except OSError as e:
        logger.warning("Could not compare portfolio.json vs findings mtimes (%s): %s", pp, e)

    if not_required_ids:
        checks.append(
            {
                "product_id": None,
                "category": "not_required",
                "code": "no_signal_sources",
                "message": (
                    f"{len(not_required_ids)} product(s) have no enabled signal sources — "
                    "temporal collection not required: " + ", ".join(not_required_ids[:24])
                    + (" …" if len(not_required_ids) > 24 else "")
                ),
                "severity": "info",
            }
        )

    by_cat: dict[str, list[str]] = {
        "missing_current": [],
        "stale_current": [],
        "malformed": [],
        "not_required": [],
        "integrity": [],
    }
    for c in checks:
        cat = c["category"]
        if cat in by_cat:
            by_cat[cat].append(c["message"])

    return {
        "schema": "argus.doctor_temporal.v2",
        "reference_time_utc": ref.isoformat(),
        "tolerances": {
            "future_collection_timestamp_sec": FUTURE_COLLECTION_TOLERANCE_SEC,
            "signal_collection_max_age_days": SIGNAL_COLLECTION_MAX_AGE_SEC // 86400,
        },
        "summary": summary,
        "checks": checks,
        "by_category": by_cat,
    }
