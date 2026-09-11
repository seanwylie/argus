"""Hard caps for context packets (prevent unbounded growth)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextCaps:
    max_findings: int = 10
    max_capability_gap_ids: int = 10
    max_doctrine_chars: int = 8000
    max_strategy_chars: int = 6000
    max_product_summary_chars: int = 8000
    max_artifact_content_preview: int = 12000
    max_structured_fields_json: int = 4000
    max_audit_implemented_ids: int = 14
    max_audit_missing_ids: int = 10
    max_audit_partial_ids: int = 8
    max_audit_unknown_ids: int = 8
    #: Max ``summary_lines`` per angle in ``audit.angles`` (bundle multi-angle).
    max_audit_summary_lines_per_angle: int = 6
