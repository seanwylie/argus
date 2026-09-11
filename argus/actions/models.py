"""Formal action contract for planned Argus execution (validation + dry-run only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from argus.core.models.enums import ActionType


def _parse_iso_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        # datetime.fromisoformat handles "2026-01-01T12:00:00Z" in 3.11+
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _coerce_action_type(raw: object) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    return s or None


@dataclass
class LifecycleTransitionSpec:
    """Optional declared lifecycle change associated with an action (for safety checks)."""

    from_stage: str
    to_stage: str


@dataclass
class ActionContract:
    """
    Machine- and human-readable description of a single proposed shell action.

    This is the bridge between recommendations and execution; callers must
    validate and dry-run first. Real runs go through ``argus execution`` (opt-in).
    """

    action_id: str
    product_id: str
    action_type: str
    command: str
    working_directory: str
    expected_outcome: str = ""
    rollback_notes: str = ""
    requires_approval: bool = True
    safe_to_auto_execute: bool = False
    estimated_duration: str = ""
    estimated_cost_usd: float | None = None
    created_at: datetime | None = None
    lifecycle_transition: LifecycleTransitionSpec | None = None
    #: Optional link to ``runs/experiments/<id>.json`` for autonomous policy (e.g. block infra experiments).
    experiment_id: str | None = None
    #: Optional override for Phase 1 project permission key (``argus.project_permissions``); inferred if unset.
    project_permission_key: str | None = None

    def normalized_action_type(self) -> str:
        return self.action_type.strip().lower()

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> ActionContract:
        """Build from a YAML/JSON mapping (e.g. loaded action file)."""
        lt_raw = data.get("lifecycle_transition")
        lt: LifecycleTransitionSpec | None = None
        if isinstance(lt_raw, Mapping):
            fs = lt_raw.get("from") or lt_raw.get("from_stage")
            ts = lt_raw.get("to") or lt_raw.get("to_stage")
            if fs is not None and ts is not None:
                lt = LifecycleTransitionSpec(
                    from_stage=str(fs).strip(),
                    to_stage=str(ts).strip(),
                )

        created = _parse_iso_datetime(data.get("created_at"))

        est_cost = data.get("estimated_cost_usd")
        est_cost_f: float | None = None
        if est_cost is not None and est_cost != "":
            try:
                est_cost_f = float(est_cost)
            except (TypeError, ValueError):
                est_cost_f = None

        ex = data.get("experiment_id")
        experiment_id = str(ex).strip() if ex is not None and str(ex).strip() else None

        ppk_raw = data.get("project_permission_key")
        ppk = str(ppk_raw).strip() if ppk_raw is not None and str(ppk_raw).strip() else None

        return cls(
            action_id=str(data.get("action_id", "")).strip(),
            product_id=str(data.get("product_id", "")).strip(),
            action_type=_coerce_action_type(data.get("action_type")) or "",
            command=str(data.get("command", "")).strip(),
            working_directory=str(data.get("working_directory", "")).strip(),
            expected_outcome=str(data.get("expected_outcome", "") or ""),
            rollback_notes=str(data.get("rollback_notes", "") or ""),
            requires_approval=bool(data.get("requires_approval", True)),
            safe_to_auto_execute=bool(data.get("safe_to_auto_execute", False)),
            estimated_duration=str(data.get("estimated_duration", "") or ""),
            estimated_cost_usd=est_cost_f,
            created_at=created,
            lifecycle_transition=lt,
            experiment_id=experiment_id,
            project_permission_key=ppk,
        )


@dataclass
class FileCheckResult:
    """Whether a path referenced by the command exists on disk."""

    path: str
    kind: str  # "script" | "path" | "other"
    exists: bool


@dataclass
class DryRunResult:
    """Outcome of dry-run analysis (no command execution)."""

    action_id: str
    product_id: str
    rendered_preview: str
    working_directory_resolved: str
    validation_errors: list[str] = field(default_factory=list)
    file_checks: list[FileCheckResult] = field(default_factory=list)
    dangerous_flags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True when validation passed (dry-run may still report danger)."""
        return not self.validation_errors

    @property
    def safe_to_proceed(self) -> bool:
        """True when valid and no dangerous patterns detected."""
        return self.ok and not self.dangerous_flags


@dataclass
class ExecuteResult:
    """Outcome of an attempted real execution (after approval gate)."""

    action_id: str
    product_id: str
    returncode: int | None
    stdout: str
    stderr: str
    dry_run_snapshot: DryRunResult | None = None


KNOWN_ACTION_TYPES: frozenset[str] = frozenset(a.value for a in ActionType)
