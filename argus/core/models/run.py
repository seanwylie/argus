"""Run records and artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from argus.core.models.decision import ActionProposal
from argus.core.models.enums import RunResult, RunStage
from argus.core.models.finding import Finding


@dataclass
class RunRecord:
    """
    Generic artifact for one Argus operation (audit pass, plan, execute).

    Embeds findings and selected actions so exports are self-contained.
    """

    run_id: str
    started_at: datetime
    finished_at: datetime | None
    stage: RunStage
    product_ids: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    selected_actions: list[ActionProposal] = field(default_factory=list)
    result: RunResult = RunResult.UNKNOWN
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
