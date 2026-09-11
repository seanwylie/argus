"""Orchestrated analysis loop (discovery → signals → findings → decisions). Planning and escalation are separate CLIs."""

from argus.orchestrator.advancement import advance_orchestration
from argus.orchestrator.eligibility import evaluate_product_orchestration
from argus.orchestrator.loop import new_run_id, run_analysis_loop
from argus.orchestrator.stages import LOOP_STAGE_ORDER, LoopStage, StageResult
from argus.orchestrator.state_pass import write_orchestration_state

__all__ = [
    "LOOP_STAGE_ORDER",
    "LoopStage",
    "StageResult",
    "advance_orchestration",
    "evaluate_product_orchestration",
    "new_run_id",
    "run_analysis_loop",
    "write_orchestration_state",
]
