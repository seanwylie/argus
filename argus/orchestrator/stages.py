"""Loop stage identifiers and ordering for the Argus analysis orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LoopStage(str, Enum):
    """Ordered stages for ``argus loop run`` (analysis spine only; no planning or escalation)."""

    DISCOVER = "discover"
    SIGNALS = "signals"
    FINDINGS = "findings"
    DECISIONS = "decisions"


LOOP_STAGE_ORDER: tuple[LoopStage, ...] = (
    LoopStage.DISCOVER,
    LoopStage.SIGNALS,
    LoopStage.FINDINGS,
    LoopStage.DECISIONS,
)


@dataclass
class StageResult:
    """Outcome of one stage (persisted under ``runs/loop/<run_id>/stages/<stage>/``)."""

    stage: LoopStage
    ok: bool
    started_at_utc: str
    finished_at_utc: str
    error: str | None = None
    output_relpath: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
