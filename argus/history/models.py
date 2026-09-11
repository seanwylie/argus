"""Canonical models for portfolio snapshots and deltas (filesystem-backed, no DB)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass
class ProductSnapshot:
    """Point-in-time summary for one product (derived from latest local artifacts)."""

    snapshot_id: str
    product_id: str
    observed_at_utc: str
    state: str
    status: str
    lifecycle_stage: str
    monthly_cost_usd: float | None
    last_signal_at: str | None
    active_findings_count: int
    findings_by_severity: dict[str, int]
    top_recommended_action: str
    priority_score: float | None
    top_confidence: float | None
    escalation_count: int
    lifecycle_scores: dict[str, float]
    kill_candidate: bool
    source_paths: dict[str, str]


@dataclass
class PortfolioSnapshot:
    """Full portfolio generation: one file per capture under ``runs/history/``."""

    snapshot_id: str
    observed_at_utc: str
    label: str | None
    repo_root: str
    schema: str = "argus.portfolio_snapshot.v1"
    products: list[ProductSnapshot] = field(default_factory=list)


@dataclass
class ProductSnapshotDelta:
    """Per-product change between two portfolio snapshots."""

    product_id: str
    active_findings_count_delta: int
    monthly_cost_usd_delta: float | None
    top_recommended_action_changed: bool
    previous_top_action: str
    current_top_action: str
    lifecycle_stage_changed: bool
    previous_lifecycle_stage: str
    current_lifecycle_stage: str
    escalation_count_delta: int
    top_confidence_delta: float | None
    kill_candidate_changed: bool
    previous_kill_candidate: bool
    current_kill_candidate: bool
    last_signal_changed: bool
    previous_last_signal_at: str | None
    current_last_signal_at: str | None


@dataclass
class SnapshotDelta:
    """Aggregate delta for ``argus history diff``."""

    from_snapshot_id: str
    to_snapshot_id: str
    from_observed_at_utc: str
    to_observed_at_utc: str
    product_deltas: list[ProductSnapshotDelta] = field(default_factory=list)


def product_snapshot_from_dict(d: Mapping[str, Any]) -> ProductSnapshot:
    m = dict(d)
    sev = m.get("findings_by_severity") or {}
    scores = m.get("lifecycle_scores") or {}
    paths = m.get("source_paths") or {}
    return ProductSnapshot(
        snapshot_id=str(m.get("snapshot_id", "")),
        product_id=str(m.get("product_id", "")),
        observed_at_utc=str(m.get("observed_at_utc", "")),
        state=str(m.get("state", "")),
        status=str(m.get("status", "")),
        lifecycle_stage=str(m.get("lifecycle_stage", "")),
        monthly_cost_usd=_maybe_float(m.get("monthly_cost_usd")),
        last_signal_at=_maybe_str(m.get("last_signal_at")),
        active_findings_count=int(m.get("active_findings_count", 0)),
        findings_by_severity={str(k): int(v) for k, v in sev.items()} if isinstance(sev, dict) else {},
        top_recommended_action=str(m.get("top_recommended_action", "")),
        priority_score=_maybe_float(m.get("priority_score")),
        top_confidence=_maybe_float(m.get("top_confidence")),
        escalation_count=int(m.get("escalation_count", 0)),
        lifecycle_scores={
            str(k): float(v) for k, v in scores.items() if isinstance(v, (int, float))
        }
        if isinstance(scores, dict)
        else {},
        kill_candidate=bool(m.get("kill_candidate", False)),
        source_paths={str(k): str(v) for k, v in paths.items()} if isinstance(paths, dict) else {},
    )


def portfolio_snapshot_from_dict(d: Mapping[str, Any]) -> PortfolioSnapshot:
    m = dict(d)
    prods = m.get("products") or []
    plist: list[ProductSnapshot] = []
    if isinstance(prods, list):
        for item in prods:
            if isinstance(item, dict):
                plist.append(product_snapshot_from_dict(item))
    return PortfolioSnapshot(
        snapshot_id=str(m.get("snapshot_id", "")),
        observed_at_utc=str(m.get("observed_at_utc", "")),
        label=_maybe_str(m.get("label")),
        repo_root=str(m.get("repo_root", "")),
        schema=str(m.get("schema", "argus.portfolio_snapshot.v1")),
        products=plist,
    )


def _maybe_float(v: object) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    return None


def _maybe_str(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None
