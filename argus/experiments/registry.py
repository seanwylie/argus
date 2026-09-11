"""Experiment type/status sets and valid state transitions."""

from __future__ import annotations

from argus.experiments.models import ExperimentStatus, ExperimentType

ALL_EXPERIMENT_TYPES: tuple[ExperimentType, ...] = tuple(ExperimentType)
ALL_EXPERIMENT_STATUSES: tuple[ExperimentStatus, ...] = tuple(ExperimentStatus)


def is_terminal(status: ExperimentStatus) -> bool:
    return status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED)


def can_transition(from_status: ExperimentStatus, to_status: ExperimentStatus) -> bool:
    """Allowed lifecycle moves (deterministic rules)."""
    if from_status == to_status:
        return True
    if is_terminal(from_status):
        return False
    if from_status == ExperimentStatus.PROPOSED:
        return to_status in (ExperimentStatus.ACTIVE, ExperimentStatus.FAILED)
    if from_status == ExperimentStatus.ACTIVE:
        return to_status in (ExperimentStatus.COMPLETED, ExperimentStatus.FAILED)
    return False
