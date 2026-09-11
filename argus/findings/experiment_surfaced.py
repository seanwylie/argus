"""Durable experiment-outcome projections as finding-shaped evidence (in-repo; not raw telemetry)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.enums import EffortBucket, FindingKind, SeverityLevel
from argus.core.models.finding import Finding
from argus.core.models.validation import validate_finding
from argus.core.serialize import dumps_json, to_jsonable
from argus.experiments.models import EvaluationVerdict, Experiment, ExperimentStatus
from argus.findings.persistence import load_latest_findings

EXPERIMENT_SURFACING_RULE_ID = "experiment_outcome_surface_v1"
EXPERIMENT_SURFACED_SCHEMA = "argus.findings_experiment_surfaced.v1"


def experiment_surfaced_latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root.resolve() / "runs" / "findings" / "experiment_surfaced" / "latest" / f"{product_id}.json"


def has_usable_experiment_outcome(e: Experiment) -> bool:
    """Terminal lifecycle and/or a persisted evaluation verdict string."""
    if e.status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED):
        return True
    v = (e.last_evaluation_verdict or "").strip()
    return bool(v)


def _kind_severity(e: Experiment) -> tuple[FindingKind, SeverityLevel]:
    st = e.status
    verdict = (e.last_evaluation_verdict or "").strip().lower()
    if st == ExperimentStatus.FAILED or verdict == EvaluationVerdict.FAILED.value:
        return FindingKind.CURRENT_RISK, SeverityLevel.HIGH
    if st == ExperimentStatus.COMPLETED or verdict in (
        EvaluationVerdict.SUCCESS.value,
        EvaluationVerdict.PARTIAL_SUCCESS.value,
    ):
        return FindingKind.GROWTH_OPPORTUNITY, SeverityLevel.MEDIUM
    if verdict == EvaluationVerdict.INCONCLUSIVE.value:
        return FindingKind.NO_RECENT_EVIDENCE, SeverityLevel.LOW
    return FindingKind.CURRENT_OPPORTUNITY, SeverityLevel.MEDIUM


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts or not str(ts).strip():
        return None
    s = str(ts).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _finding_created_at_utc(e: Experiment) -> datetime:
    """Clock from persisted experiment fields only (stable across process reruns)."""
    for ts in (e.last_evaluation_at, e.created_at, e.start_at):
        if ts and (t := _parse_iso(str(ts))) is not None:
            return t
    return datetime(1970, 1, 1, tzinfo=timezone.utc)


def finding_from_experiment(e: Experiment, *, product_id: str) -> Finding:
    """One deterministic Finding per experiment id (stable id for dedupe across reruns)."""
    kind, sev = _kind_severity(e)
    fid = f"exp_surface:{product_id}:{e.id}"
    title = f"Experiment outcome: {e.id}"
    summary = (
        f"status={e.status.value}"
        + (
            f", verdict={e.last_evaluation_verdict}"
            if (e.last_evaluation_verdict or "").strip()
            else ""
        )
        + (f", summary={e.last_evaluation_summary}" if (e.last_evaluation_summary or "").strip() else "")
    ).strip()[:2000]
    recommendation = (
        "Review persisted experiment JSON and evaluation evidence; "
        "this row is synthetic surfaced evidence, not independent measurement."
    )
    evidence: dict[str, Any] = {
        "provenance": "experiment_surfaced",
        "surfacing_rule": EXPERIMENT_SURFACING_RULE_ID,
        "synthetic": True,
        "experiment_id": e.id,
        "experiment_status": e.status.value,
        "last_evaluation_verdict": e.last_evaluation_verdict,
        "last_evaluation_at": e.last_evaluation_at,
        "last_evaluation_summary": e.last_evaluation_summary,
    }
    return Finding(
        id=fid,
        product_id=product_id,
        kind=kind,
        severity=sev,
        effort=EffortBucket.SMALL,
        title=title,
        summary=summary,
        recommendation=recommendation,
        source_signals=[f"experiment:{e.id}"],
        evidence=evidence,
        confidence=0.75,
        created_at=_finding_created_at_utc(e),
    )


def build_surfaced_findings(experiments: list[Experiment], product_id: str) -> list[Finding]:
    """Deterministic list: sort by (experiment id), one finding per eligible experiment."""
    eligible = [e for e in experiments if e.product_id == product_id and has_usable_experiment_outcome(e)]
    eligible.sort(key=lambda x: x.id)
    out: list[Finding] = []
    for e in eligible:
        f = finding_from_experiment(e, product_id=product_id)
        validate_finding(f)
        out.append(f)
    return out


def save_experiment_surfaced_bundle(repo_root: Path, product_id: str, findings: list[Finding]) -> Path:
    root = repo_root.resolve()
    path = experiment_surfaced_latest_path(root, product_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": EXPERIMENT_SURFACED_SCHEMA,
        "surfacing_rule": EXPERIMENT_SURFACING_RULE_ID,
        "product_id": product_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "finding_count": len(findings),
        "findings": [to_jsonable(f) for f in findings],
    }
    path.write_text(dumps_json(bundle) + "\n", encoding="utf-8")
    return path


def load_experiment_surfaced_findings(repo_root: Path, product_id: str) -> list[Finding]:
    """Load surfaced findings sidecar; empty list if absent or invalid."""
    from argus.core.serialize import finding_from_dict

    p = experiment_surfaced_latest_path(repo_root, product_id)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        return []
    if not isinstance(data, dict):
        return []
    if str(data.get("schema") or "") != EXPERIMENT_SURFACED_SCHEMA:
        return []
    raw = data.get("findings") or []
    out: list[Finding] = []
    if not isinstance(raw, list):
        return []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            f = finding_from_dict(item)
            validate_finding(f)
            out.append(f)
        except (ValueError, TypeError, KeyError):
            continue
    return out


def merged_findings_for_decisions(repo_root: Path, product_id: str) -> list[Finding]:
    """Latest signal-derived findings plus experiment-surfaced findings (deterministic concat)."""
    fb = load_latest_findings(repo_root, product_id)
    base = list(fb.findings) if fb else []
    extra = load_experiment_surfaced_findings(repo_root, product_id)
    return base + extra
