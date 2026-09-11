"""Validate on-disk Argus artifacts."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from argus.validation.schemas import (
    AUTONOMY_CONFIG_REQUIRED,
    CANONICAL_SIGNAL_SCHEMA,
    DECISIONS_PORTFOLIO_REQUIRED,
    DECISIONS_PRODUCT_REQUIRED,
    EXECUTION_RUN_REQUIRED,
    EXPERIMENT_REQUIRED,
    FINDINGS_LATEST_REQUIRED,
    IDEAS_BUNDLE_REQUIRED,
    SIGNALS_LATEST_REQUIRED,
    SNAPSHOT_REQUIRED,
    TEMPORAL_BUNDLE_SCHEMA,
    TEMPORAL_LATEST_REQUIRED,
    TEMPORAL_SIGNAL_ROW_SCHEMA,
    TRENDS_LATEST_REQUIRED,
)


@dataclass
class ValidationIssue:
    path: str
    message: str
    severity: str = "error"  # error | warning


@dataclass
class ArtifactReport:
    ok: bool
    issues: list[ValidationIssue] = field(default_factory=list)
    checked_files: int = 0


def _req_keys(obj: dict[str, Any], required: frozenset[str], label: str) -> list[str]:
    missing = [k for k in required if k not in obj]
    return [f"{label}: missing keys {missing}"] if missing else []


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        return None, str(e)
    if not isinstance(raw, dict):
        return None, "top level must be a JSON object"
    return raw, None


def validate_execution_runs(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    base = root / "runs" / "execution"
    issues: list[ValidationIssue] = []
    if not base.is_dir():
        return issues
    for d in sorted(x for x in base.iterdir() if x.is_dir() and not x.name.startswith(".")):
        p = d / "run.json"
        if not p.is_file():
            issues.append(
                ValidationIssue(
                    str(p.relative_to(root)),
                    "execution run directory without run.json",
                    "warning",
                )
            )
            continue
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        miss = _req_keys(data, EXECUTION_RUN_REQUIRED, rel)
        for m in miss:
            issues.append(ValidationIssue(rel, m))
    return issues


def validate_findings_latest(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    d = root / "runs" / "findings" / "latest"
    issues: list[ValidationIssue] = []
    if not d.is_dir():
        return issues
    for p in sorted(d.glob("*.json")):
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        for m in _req_keys(data, FINDINGS_LATEST_REQUIRED, rel):
            issues.append(ValidationIssue(rel, m))
    return issues


def validate_decisions_latest(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    d = root / "runs" / "decisions" / "latest"
    issues: list[ValidationIssue] = []
    if not d.is_dir():
        return issues
    port = d / "portfolio.json"
    if port.is_file():
        data, err = _load_json(port)
        rel = str(port.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
        elif data:
            for m in _req_keys(data, DECISIONS_PORTFOLIO_REQUIRED, rel):
                issues.append(ValidationIssue(rel, m))
    for p in sorted(d.glob("*.json")):
        if p.name == "portfolio.json":
            continue
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        for m in _req_keys(data, DECISIONS_PRODUCT_REQUIRED, rel):
            issues.append(ValidationIssue(rel, m))
    return issues


def validate_experiments(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    d = root / "runs" / "experiments"
    issues: list[ValidationIssue] = []
    if not d.is_dir():
        return issues
    for p in sorted(d.glob("exp_*.json")):
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        for m in _req_keys(data, EXPERIMENT_REQUIRED, rel):
            issues.append(ValidationIssue(rel, m))
    return issues


def validate_snapshots(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    base = root / "runs" / "history" / "snapshots"
    issues: list[ValidationIssue] = []
    if not base.is_dir():
        return issues
    for snap_dir in sorted(x for x in base.iterdir() if x.is_dir()):
        p = snap_dir / "snapshot.json"
        if not p.is_file():
            continue
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        for m in _req_keys(data, SNAPSHOT_REQUIRED, rel):
            issues.append(ValidationIssue(rel, m))
    return issues


def validate_trends_latest(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    p = root / "runs" / "trends" / "latest.json"
    issues: list[ValidationIssue] = []
    if not p.is_file():
        return issues
    data, err = _load_json(p)
    rel = str(p.relative_to(root))
    if err:
        issues.append(ValidationIssue(rel, err))
        return issues
    assert data is not None
    for m in _req_keys(data, TRENDS_LATEST_REQUIRED, rel):
        issues.append(ValidationIssue(rel, m))
    return issues


def validate_signals_latest(repo_root: Path) -> list[ValidationIssue]:
    """Validate ``runs/signals/latest/<product_id>.json`` bundles (signal collection v1)."""
    root = repo_root.resolve()
    d = root / "runs" / "signals" / "latest"
    issues: list[ValidationIssue] = []
    if not d.is_dir():
        return issues
    for p in sorted(d.glob("*.json")):
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        for m in _req_keys(data, SIGNALS_LATEST_REQUIRED, rel):
            issues.append(ValidationIssue(rel, m))
        if str(data.get("schema", "")) != "argus.signal_collection.v1":
            issues.append(
                ValidationIssue(
                    rel,
                    "schema must be argus.signal_collection.v1",
                )
            )
        if "records" in data and not isinstance(data["records"], list):
            issues.append(
                ValidationIssue(
                    rel,
                    f"'records' must be a JSON array (got {type(data['records']).__name__})",
                )
            )
            continue
        records = data.get("records")
        if not isinstance(records, list):
            continue
        for i, rec in enumerate(records):
            if not isinstance(rec, dict):
                issues.append(ValidationIssue(rel, f"records[{i}] must be a JSON object"))
                continue
            canon = rec.get("canonical")
            if canon is None:
                issues.append(
                    ValidationIssue(
                        rel,
                        f"records[{i}] missing canonical — re-run `argus signals collect` for normalized rows",
                        severity="warning",
                    )
                )
                continue
            if not isinstance(canon, dict):
                issues.append(ValidationIssue(rel, f"records[{i}].canonical must be a JSON object"))
                continue
            if str(canon.get("schema", "")) != CANONICAL_SIGNAL_SCHEMA:
                issues.append(
                    ValidationIssue(
                        rel,
                        f"records[{i}].canonical.schema must be {CANONICAL_SIGNAL_SCHEMA}",
                    )
                )
            fs = canon.get("freshness_status")
            if not isinstance(fs, str) or not fs.strip():
                issues.append(
                    ValidationIssue(
                        rel,
                        f"records[{i}].canonical.freshness_status must be a non-empty string (temporal qualification)",
                    )
                )
    return issues


def validate_temporal_latest(repo_root: Path) -> list[ValidationIssue]:
    """Validate ``runs/temporal/latest/<product_id>.json`` (argus.temporal_bundle.v1)."""
    root = repo_root.resolve()
    d = root / "runs" / "temporal" / "latest"
    issues: list[ValidationIssue] = []
    if not d.is_dir():
        return issues
    for p in sorted(d.glob("*.json")):
        data, err = _load_json(p)
        rel = str(p.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
            continue
        assert data is not None
        for m in _req_keys(data, TEMPORAL_LATEST_REQUIRED, rel):
            issues.append(ValidationIssue(rel, m))
        if str(data.get("schema", "")) != TEMPORAL_BUNDLE_SCHEMA:
            issues.append(ValidationIssue(rel, f"schema must be {TEMPORAL_BUNDLE_SCHEMA}"))
        if data.get("signals") and isinstance(data["signals"], list) and len(data["signals"]) > 0:
            if "worst_freshness_status" not in data:
                issues.append(
                    ValidationIssue(
                        rel,
                        "missing worst_freshness_status (re-run `argus signals collect` for aggregate freshness)",
                        severity="warning",
                    )
                )
        sigs = data.get("signals")
        if not isinstance(sigs, list):
            issues.append(ValidationIssue(rel, "'signals' must be a JSON array"))
            continue
        for i, row in enumerate(sigs):
            if not isinstance(row, dict):
                issues.append(ValidationIssue(rel, f"signals[{i}] must be a JSON object"))
                continue
            if str(row.get("schema", "")) != TEMPORAL_SIGNAL_ROW_SCHEMA:
                issues.append(
                    ValidationIssue(
                        rel,
                        f"signals[{i}].schema must be {TEMPORAL_SIGNAL_ROW_SCHEMA}",
                    )
                )
            st = row.get("freshness_status")
            fb = row.get("freshness_bucket")
            has_fs = isinstance(st, str) and st.strip()
            has_fb = isinstance(fb, str) and fb.strip()
            if not has_fs and not has_fb:
                issues.append(
                    ValidationIssue(
                        rel,
                        f"signals[{i}] must include freshness_status or freshness_bucket (temporal qualification)",
                    )
                )
    return issues


def validate_ideas_latest(repo_root: Path) -> list[ValidationIssue]:
    """Validate ``runs/ideas/latest.json`` (ideas bundle)."""
    root = repo_root.resolve()
    p = root / "runs" / "ideas" / "latest.json"
    issues: list[ValidationIssue] = []
    if not p.is_file():
        return issues
    data, err = _load_json(p)
    rel = str(p.relative_to(root))
    if err:
        issues.append(ValidationIssue(rel, err))
        return issues
    assert data is not None
    for m in _req_keys(data, IDEAS_BUNDLE_REQUIRED, rel):
        issues.append(ValidationIssue(rel, m))
    if "ideas" in data and not isinstance(data["ideas"], list):
        issues.append(
            ValidationIssue(
                rel,
                f"'ideas' must be a JSON array (got {type(data['ideas']).__name__})",
            )
        )
    return issues


def validate_autonomy_state(repo_root: Path) -> list[ValidationIssue]:
    root = repo_root.resolve()
    issues: list[ValidationIssue] = []
    ap = root / "runs" / "autonomy" / "autonomy.json"
    if ap.is_file():
        data, err = _load_json(ap)
        rel = str(ap.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
        elif data:
            for m in _req_keys(data, AUTONOMY_CONFIG_REQUIRED, rel):
                issues.append(ValidationIssue(rel, m))
    st = root / "runs" / "autonomy" / "state.json"
    if st.is_file():
        data, err = _load_json(st)
        rel = str(st.relative_to(root))
        if err:
            issues.append(ValidationIssue(rel, err))
    return issues


def validate_loop_run(repo_root: Path, run_id: str) -> list[ValidationIssue]:
    root = repo_root.resolve()
    issues: list[ValidationIssue] = []
    run_dir = root / "runs" / "loop" / run_id
    if not run_dir.is_dir():
        issues.append(ValidationIssue(f"runs/loop/{run_id}", "run directory not found"))
        return issues
    for name in ("manifest.json", "summary.json"):
        p = run_dir / name
        if not p.is_file():
            issues.append(ValidationIssue(str(p.relative_to(root)), "missing file"))
            continue
        _, err = _load_json(p)
        if err:
            issues.append(ValidationIssue(str(p.relative_to(root)), err))
    return issues


def validate_all_artifacts(repo_root: Path, *, loop_run_id: str | None = None) -> ArtifactReport:
    issues: list[ValidationIssue] = []
    n = 0

    parts: list[Callable[[Path], list[ValidationIssue]]] = [
        validate_execution_runs,
        validate_findings_latest,
        validate_decisions_latest,
        validate_experiments,
        validate_snapshots,
        validate_trends_latest,
        validate_signals_latest,
        validate_temporal_latest,
        validate_ideas_latest,
        validate_autonomy_state,
    ]
    for fn in parts:
        ch = fn(repo_root)
        issues.extend(ch)
        n += max(1, len(ch))

    if loop_run_id:
        ch = validate_loop_run(repo_root, loop_run_id)
        issues.extend(ch)
        n += max(1, len(ch))

    errs = [i for i in issues if i.severity == "error"]
    ok = not errs
    return ArtifactReport(ok=ok, issues=issues, checked_files=n)


def validate_repo_artifacts(repo_root: Path) -> ArtifactReport:
    """Validate canonical artifact locations (no specific loop run)."""
    return validate_all_artifacts(repo_root, loop_run_id=None)
