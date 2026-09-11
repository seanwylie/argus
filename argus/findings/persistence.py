"""Filesystem persistence for generated findings."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from argus.core.models.finding import Finding
from argus.core.models.validation import validate_finding
from argus.core.serialize import dumps_json, finding_from_dict, to_jsonable


@dataclass(frozen=True)
class FindingsBundle:
    product_id: str
    generated_at_utc: str
    repo_root: str
    findings: list[Finding]


def generations_dir(repo_root: Path) -> Path:
    return repo_root / "runs" / "findings" / "generations"


def latest_path(repo_root: Path, product_id: str) -> Path:
    return repo_root / "runs" / "findings" / "latest" / f"{product_id}.json"


def save_findings_bundle(
    repo_root: Path,
    product_id: str,
    findings: list[Finding],
    *,
    write_latest: bool = True,
) -> Path:
    root = repo_root.resolve()
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = generations_dir(root)
    base.mkdir(parents=True, exist_ok=True)
    bundle = {
        "schema": "argus.findings_bundle.v1",
        "product_id": product_id,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "finding_count": len(findings),
        "findings": [to_jsonable(f) for f in findings],
    }
    path = base / f"{ts}_{product_id}.json"
    path.write_text(dumps_json(bundle), encoding="utf-8")
    if write_latest:
        lp = latest_path(root, product_id)
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(dumps_json(bundle), encoding="utf-8")
    return path


def load_latest_findings(repo_root: Path, product_id: str) -> FindingsBundle | None:
    lp = latest_path(repo_root.resolve(), product_id)
    if not lp.is_file():
        return None
    return load_findings_file(lp)


def load_findings_file(path: Path) -> FindingsBundle:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("bundle must be a JSON object")
    pid = str(data.get("product_id", ""))
    ts = str(data.get("generated_at_utc", ""))
    repo = str(data.get("repo_root", ""))
    raw = data.get("findings") or []
    findings: list[Finding] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                f = finding_from_dict(item)
                validate_finding(f)
                findings.append(f)
    return FindingsBundle(
        product_id=pid,
        generated_at_utc=ts,
        repo_root=repo,
        findings=findings,
    )


def bundle_to_jsonable(bundle: FindingsBundle) -> dict[str, Any]:
    return {
        "product_id": bundle.product_id,
        "generated_at_utc": bundle.generated_at_utc,
        "repo_root": bundle.repo_root,
        "findings": [to_jsonable(f) for f in bundle.findings],
    }


def load_all_latest_summaries(repo_root: Path) -> dict[str, dict[str, Any]]:
    """For ``findings summary``: read each ``latest/*.json`` if present."""
    latest_dir = repo_root / "runs" / "findings" / "latest"
    if not latest_dir.is_dir():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for p in sorted(latest_dir.glob("*.json")):
        try:
            b = load_findings_file(p)
            out[b.product_id] = {
                "finding_count": len(b.findings),
                "by_kind": _count_kinds(b.findings),
            }
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return out


def _count_kinds(findings: list[Finding]) -> dict[str, int]:
    d: dict[str, int] = {}
    for f in findings:
        k = f.kind.value
        d[k] = d.get(k, 0) + 1
    return d
