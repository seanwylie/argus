"""Lifecycle readiness scoring (deterministic).

Kill scoring lives in :mod:`argus.lifecycle.kill` (imported separately to avoid
heavy/circular imports at package load time).
"""

from argus.lifecycle.model import LifecycleAssessment
from argus.lifecycle.scoring import assess_lifecycle

__all__ = [
    "LifecycleAssessment",
    "assess_lifecycle",
]
