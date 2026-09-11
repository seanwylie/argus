"""
External opportunity signals — advisory only, normalized into ``runs/world_context/latest.json``.

No portfolio scoring or autonomous decisions; provenance and freshness are explicit.
"""

from argus.world_context.interpretation import (
    build_interpretation_payload,
    operator_advisory_from_world_context,
)
from argus.world_context.persist import (
    WORLD_CONTEXT_INTERPRETATION_SCHEMA,
    WORLD_CONTEXT_SCHEMA,
    world_context_output_dir,
    write_interpretation_artifact,
    write_world_context_artifact,
)
from argus.world_context.service import (
    build_world_context_payload,
    load_world_context,
    load_world_context_interpretation,
    summarize_for_operator,
)

__all__ = [
    "WORLD_CONTEXT_INTERPRETATION_SCHEMA",
    "WORLD_CONTEXT_SCHEMA",
    "build_interpretation_payload",
    "build_world_context_payload",
    "load_world_context",
    "load_world_context_interpretation",
    "operator_advisory_from_world_context",
    "summarize_for_operator",
    "world_context_output_dir",
    "write_interpretation_artifact",
    "write_world_context_artifact",
]
