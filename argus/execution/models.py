"""Execution run records (subprocess; logged under runs/execution/)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ExecutionStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    BLOCKED = "blocked"  # policy / approval / autonomy / validation — subprocess not launched


@dataclass
class ExecutionRun:
    """One completed or in-progress execution of an ActionContract."""

    run_id: str
    action_id: str
    product_id: str
    command: str
    working_directory: str
    started_at: str
    finished_at: str = ""
    status: ExecutionStatus = ExecutionStatus.PENDING
    output_log: str = ""
    error_log: str = ""
    exit_code: int | None = None
    rollback_notes: str = ""
    schema: str = "argus.execution_run.v1"
    # Phase 1 permission truth + embed (same keys as orchestration execution_detail when governed).
    execution_detail: dict[str, Any] | None = None
    # True after ``subprocess.run`` is invoked; False when blocked or failed before spawn; None legacy.
    subprocess_launched: bool | None = None

    def __post_init__(self) -> None:
        if isinstance(self.status, str):
            self.status = ExecutionStatus(self.status)
