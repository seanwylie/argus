"""Per-angle deterministic audit runners (multi-angle bundle)."""

from __future__ import annotations

from argus.audit.angles.cost import fingerprint_cost_inputs, run_cost_angle
from argus.audit.angles.performance import fingerprint_performance_inputs, run_performance_angle
from argus.audit.angles.product_gap import run_product_gap_angle
from argus.audit.angles.quality import fingerprint_quality_inputs, run_quality_angle
from argus.audit.angles.security import fingerprint_security_inputs, run_security_angle
from argus.audit.angles.stub import STUB_ANGLE_IDS, stub_angle_payload
from argus.audit.angles.ux import fingerprint_ux_inputs, run_ux_angle

__all__ = [
    "STUB_ANGLE_IDS",
    "fingerprint_cost_inputs",
    "fingerprint_performance_inputs",
    "fingerprint_quality_inputs",
    "fingerprint_security_inputs",
    "fingerprint_ux_inputs",
    "run_cost_angle",
    "run_performance_angle",
    "run_product_gap_angle",
    "run_quality_angle",
    "run_security_angle",
    "run_ux_angle",
    "stub_angle_payload",
]
