"""Capability registry and gap awareness (scaffold; no auto-implementation)."""

from argus.capabilities.evaluate import evaluate_capabilities, write_evaluation_artifact
from argus.capabilities.models import Capability, CapabilityEvaluation, MissingCapability
from argus.capabilities.registry import current_capabilities, infer_missing_capabilities

__all__ = [
    "Capability",
    "CapabilityEvaluation",
    "MissingCapability",
    "current_capabilities",
    "evaluate_capabilities",
    "infer_missing_capabilities",
    "write_evaluation_artifact",
]
