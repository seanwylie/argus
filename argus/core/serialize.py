"""JSON- and YAML-friendly serialization for Argus domain models."""

from __future__ import annotations

import json
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any, TypeVar, cast

from argus.core.models.canonical_signal import CanonicalSignal
from argus.core.models.decision import ActionProposal, DecisionCandidate
from argus.core.models.enums import (
    ActionType,
    EffortBucket,
    FindingKind,
    LifecycleStage,
    RunResult,
    RunStage,
    SeverityLevel,
    SignalType,
)
from argus.core.models.finding import Finding
from argus.core.models.product import (
    ActionsMap,
    ConstraintsDefinition,
    CostDefinition,
    MetricsDefinition,
    OwnerInfo,
    ProductLifecycle,
    ProductNode,
    ProductTypeInfo,
    SignalDefinition,
)
from argus.core.models.run import RunRecord
from argus.core.models.signal import SignalRecord
from argus.core.models.signal_manifest import (
    PRODUCT_SIGNAL_MANIFEST_SCHEMA,
    ManifestRequiredFor,
    ManifestTrustLevel,
    ManifestValueType,
    ProductSignalManifest,
    ProductSignalManifestEntry,
    SignalManifestCategory,
    SourceWindowKind,
)

T = TypeVar("T")


def to_jsonable(obj: object) -> object:
    """Recursively convert dataclasses / enums / datetimes to JSON-safe data."""
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    if is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_jsonable(getattr(obj, f.name)) for f in fields(obj)}
    raise TypeError(f"Unsupported type for JSON conversion: {type(obj).__name__}")


def dumps_json(obj: object, *, indent: int | None = 2) -> str:
    """Serialize ``obj`` to a JSON string (dataclasses supported)."""
    return json.dumps(to_jsonable(obj), indent=indent, sort_keys=True)


def loads_json(s: str) -> Any:
    """Parse JSON text to Python data (datetimes remain strings)."""
    return json.loads(s)


def dumps_yaml(obj: object) -> str:
    """Serialize ``obj`` to a YAML string. Requires PyYAML."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover - exercised when PyYAML missing
        raise ImportError("YAML support requires the 'pyyaml' package.") from e
    return yaml.safe_dump(
        to_jsonable(obj),
        sort_keys=True,
        default_flow_style=False,
    )


def loads_yaml(s: str) -> Any:
    """Parse YAML text to Python data."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:  # pragma: no cover
        raise ImportError("YAML support requires the 'pyyaml' package.") from e
    return yaml.safe_load(s)


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Expected datetime or ISO string, got {type(value).__name__}")


def owner_info_from_dict(d: dict[str, Any]) -> OwnerInfo:
    return OwnerInfo(team=str(d.get("team", "")), operator=str(d.get("operator", "")))


def metrics_definition_from_dict(d: dict[str, Any]) -> MetricsDefinition:
    return MetricsDefinition(
        local_paths=list(d.get("local_paths") or []),
        primary=list(d.get("primary") or []),
    )


def cost_definition_from_dict(d: dict[str, Any]) -> CostDefinition:
    m = d.get("monthly_usd")
    return CostDefinition(
        monthly_usd=float(m) if m is not None else None,
        notes=(None if d.get("notes") is None else str(d["notes"])),
    )


def signal_definition_from_dict(d: dict[str, Any]) -> SignalDefinition:
    return SignalDefinition(
        type=str(d.get("type", "")),
        enabled=bool(d.get("enabled", True)),
    )


def actions_map_from_dict(d: dict[str, Any]) -> ActionsMap:
    known = {"start", "stop", "analyze"}
    extra: dict[str, str] = {}
    for k, v in d.items():
        if k in known:
            continue
        if v is not None:
            extra[str(k)] = str(v)
    return ActionsMap(
        start=(None if d.get("start") is None else str(d["start"])),
        stop=(None if d.get("stop") is None else str(d["stop"])),
        analyze=(None if d.get("analyze") is None else str(d["analyze"])),
        extra=extra,
    )


def constraints_from_dict(d: dict[str, Any]) -> ConstraintsDefinition:
    mx = d.get("max_monthly_cost_usd")
    mn = d.get("min_activity_threshold")
    return ConstraintsDefinition(
        max_monthly_cost_usd=(float(mx) if mx is not None else None),
        min_activity_threshold=(float(mn) if mn is not None else None),
    )


def product_lifecycle_from_dict(d: dict[str, Any]) -> ProductLifecycle:
    stage_raw = d.get("stage", "idea")
    stage = LifecycleStage(str(stage_raw))
    ng = d.get("next_gate")
    return ProductLifecycle(
        stage=stage,
        next_gate=None if ng is None else str(ng),
    )


def product_type_info_from_dict(d: dict[str, Any] | None) -> ProductTypeInfo | None:
    if not d:
        return None
    return ProductTypeInfo(
        type=str(d.get("type", "unknown")),
        status=str(d.get("status", "unknown")),
        state=(None if d.get("state") is None else str(d["state"])),
    )


def product_signal_manifest_entry_from_dict(d: dict[str, Any]) -> ProductSignalManifestEntry:
    swk = d.get("source_window_kind")
    return ProductSignalManifestEntry(
        id=str(d["id"]),
        category=SignalManifestCategory(str(d["category"])),
        source_type=SignalType(str(d["source_type"])),
        source_ref=(None if d.get("source_ref") is None else str(d["source_ref"])),
        path=(None if d.get("path") is None else str(d["path"])),
        freshness_sla=str(d["freshness_sla"]),
        value_type=ManifestValueType(str(d["value_type"])),
        required_for=ManifestRequiredFor(str(d["required_for"])),
        trust_level=ManifestTrustLevel(str(d["trust_level"])),
        description=(None if d.get("description") is None else str(d["description"])),
        unit=(None if d.get("unit") is None else str(d["unit"])),
        source_window_kind=(None if swk is None else SourceWindowKind(str(swk))),
        owner=(None if d.get("owner") is None else str(d["owner"])),
        enabled=bool(d.get("enabled", True)),
    )


def product_signal_manifest_from_dict(d: dict[str, Any] | None) -> ProductSignalManifest | None:
    if not d:
        return None
    sch = str(d.get("schema") or PRODUCT_SIGNAL_MANIFEST_SCHEMA)
    sigs_raw = d.get("signals") or []
    if not isinstance(sigs_raw, list):
        raise TypeError("signal_manifest.signals must be a list")
    entries = [product_signal_manifest_entry_from_dict(x) for x in sigs_raw if isinstance(x, dict)]
    return ProductSignalManifest(schema=sch, signals=entries)


def product_node_from_dict(d: dict[str, Any]) -> ProductNode:
    """Build a :class:`ProductNode` from a plain mapping (e.g. merged YAML + paths)."""
    life = d.get("lifecycle") or {}
    if not isinstance(life, dict):
        life = {}
    metrics = d.get("metrics") or {}
    cost = d.get("cost") or {}
    sigs = d.get("signals") or []
    actions = d.get("actions") or {}
    cons = d.get("constraints") or {}
    owner = d.get("owner") or {}

    signals = [signal_definition_from_dict(x) for x in sigs if isinstance(x, dict)]

    sm_raw = d.get("signal_manifest")
    signal_manifest = (
        product_signal_manifest_from_dict(sm_raw)
        if isinstance(sm_raw, dict)
        else None
    )

    from argus.mission.product_mission import parse_product_mission_from_yaml

    spec, m_err = parse_product_mission_from_yaml(d)
    if m_err:
        raise ValueError(m_err)
    mission_id = spec.objective if spec else None
    mission = spec if spec is not None and isinstance(d.get("mission"), dict) else None

    return ProductNode(
        id=str(d["id"]),
        name=str(d.get("name", d["id"])),
        owner=owner_info_from_dict(owner if isinstance(owner, dict) else {}),
        metrics=metrics_definition_from_dict(metrics if isinstance(metrics, dict) else {}),
        cost=cost_definition_from_dict(cost if isinstance(cost, dict) else {}),
        signals=signals,
        actions=actions_map_from_dict(actions if isinstance(actions, dict) else {}),
        constraints=constraints_from_dict(cons if isinstance(cons, dict) else {}),
        lifecycle=product_lifecycle_from_dict(life),
        product_root=str(d["product_root"]),
        config_path=str(d["config_path"]),
        type_info=product_type_info_from_dict(
            {
                "type": d.get("type"),
                "status": d.get("status"),
                "state": d.get("state"),
            }
        )
        if any(k in d for k in ("type", "status", "state"))
        else None,
        tags=list(d.get("tags") or []),
        raw_extensions=dict(d.get("raw_extensions") or {}),
        signal_manifest=signal_manifest,
        mission_id=mission_id,
        mission=mission,
    )


def canonical_signal_from_dict(d: dict[str, Any]) -> CanonicalSignal:
    return CanonicalSignal(
        signal_id=str(d["signal_id"]),
        product_id=str(d["product_id"]),
        category=str(d["category"]),
        value=d.get("value"),
        value_type=str(d.get("value_type", "unknown")),
        unit=(None if d.get("unit") is None else str(d["unit"])),
        source_type=str(d.get("source_type", "unknown")),
        source_ref=str(d.get("source_ref", "")),
        provenance=dict(d.get("provenance") or {}),
        observed_at=str(d.get("observed_at", "")),
        collected_at=str(d.get("collected_at", "")),
        source_window_start=(
            None if d.get("source_window_start") is None else str(d["source_window_start"])
        ),
        source_window_end=(
            None if d.get("source_window_end") is None else str(d["source_window_end"])
        ),
        freshness_sla=(None if d.get("freshness_sla") is None else str(d["freshness_sla"])),
        freshness_status=str(d.get("freshness_status", "unknown")),
        trust_level=str(d.get("trust_level", "unverified")),
        collection_status=str(d.get("collection_status", "ok")),
        schema=str(d.get("schema", "argus.canonical_signal.v1")),
    )


def signal_record_from_dict(d: dict[str, Any]) -> SignalRecord:
    canon_raw = d.get("canonical")
    canonical: CanonicalSignal | None = None
    if isinstance(canon_raw, dict):
        try:
            canonical = canonical_signal_from_dict(canon_raw)
            if canonical is not None and not str(canonical.signal_id).strip():
                canonical = None
        except (KeyError, TypeError, ValueError):
            canonical = None
    return SignalRecord(
        id=str(d["id"]),
        product_id=str(d["product_id"]),
        signal_type=SignalType(str(d["signal_type"])),
        source=str(d["source"]),
        observed_at=cast(datetime, _parse_dt(d["observed_at"])),
        payload=dict(d.get("payload") or {}),
        severity_hint=(
            None
            if d.get("severity_hint") is None
            else SeverityLevel(str(d["severity_hint"]))
        ),
        confidence=(None if d.get("confidence") is None else float(d["confidence"])),
        tags=list(d.get("tags") or []),
        canonical=canonical,
    )


def finding_from_dict(d: dict[str, Any]) -> Finding:
    return Finding(
        id=str(d["id"]),
        product_id=str(d["product_id"]),
        kind=FindingKind(str(d["kind"])),
        severity=SeverityLevel(str(d["severity"])),
        effort=EffortBucket(str(d["effort"])),
        title=str(d["title"]),
        summary=str(d["summary"]),
        recommendation=str(d["recommendation"]),
        source_signals=list(d.get("source_signals") or []),
        evidence=dict(d.get("evidence") or {}),
        confidence=(None if d.get("confidence") is None else float(d["confidence"])),
        created_at=_parse_dt(d["created_at"]) if d.get("created_at") is not None else None,
    )


def decision_candidate_from_dict(d: dict[str, Any]) -> DecisionCandidate:
    return DecisionCandidate(
        id=str(d["id"]),
        product_id=str(d["product_id"]),
        action_type=ActionType(str(d["action_type"])),
        summary=str(d["summary"]),
        expected_impact=str(d.get("expected_impact", "")),
        estimated_cost=(
            None if d.get("estimated_cost") is None else float(d["estimated_cost"])
        ),
        confidence=(None if d.get("confidence") is None else float(d["confidence"])),
        rationale=str(d.get("rationale", "")),
        priority_score=(
            None if d.get("priority_score") is None else float(d["priority_score"])
        ),
        metadata=dict(d.get("metadata") or {}),
    )


def action_proposal_from_dict(d: dict[str, Any]) -> ActionProposal:
    return ActionProposal(
        id=str(d["id"]),
        product_id=str(d["product_id"]),
        action_type=ActionType(str(d["action_type"])),
        command=str(d["command"]),
        reason=str(d["reason"]),
        expected_outcome=str(d.get("expected_outcome", "")),
        rollback_notes=str(d.get("rollback_notes", "")),
        selected_at=_parse_dt(d["selected_at"]) if d.get("selected_at") is not None else None,
    )


def run_record_from_dict(d: dict[str, Any]) -> RunRecord:
    findings_raw = d.get("findings") or []
    actions_raw = d.get("selected_actions") or []
    findings = [finding_from_dict(x) for x in findings_raw if isinstance(x, dict)]
    selected = [action_proposal_from_dict(x) for x in actions_raw if isinstance(x, dict)]
    return RunRecord(
        run_id=str(d["run_id"]),
        started_at=cast(datetime, _parse_dt(d["started_at"])),
        finished_at=_parse_dt(d["finished_at"]) if d.get("finished_at") else None,
        stage=RunStage(str(d.get("stage", RunStage.PENDING.value))),
        product_ids=list(d.get("product_ids") or []),
        findings=findings,
        selected_actions=selected,
        result=RunResult(str(d.get("result", RunResult.UNKNOWN.value))),
        notes=str(d.get("notes", "")),
        metadata=dict(d.get("metadata") or {}),
    )


def product_node_to_dict(obj: ProductNode) -> dict[str, Any]:
    return cast(dict[str, Any], to_jsonable(obj))


def signal_record_to_dict(obj: SignalRecord) -> dict[str, Any]:
    return cast(dict[str, Any], to_jsonable(obj))


def finding_to_dict(obj: Finding) -> dict[str, Any]:
    return cast(dict[str, Any], to_jsonable(obj))


def decision_candidate_to_dict(obj: DecisionCandidate) -> dict[str, Any]:
    return cast(dict[str, Any], to_jsonable(obj))


def action_proposal_to_dict(obj: ActionProposal) -> dict[str, Any]:
    return cast(dict[str, Any], to_jsonable(obj))


def run_record_to_dict(obj: RunRecord) -> dict[str, Any]:
    return cast(dict, to_jsonable(obj))


def roundtrip_json(instance: T, from_dict_fn: Any) -> T:
    """Round-trip through JSON using ``dumps_json`` / ``loads_json`` / ``from_dict_fn``."""
    blob = loads_json(dumps_json(instance))
    return from_dict_fn(blob)
