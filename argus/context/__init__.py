"""Purpose-aware context packets (product.system, artifact.draft)."""

from __future__ import annotations

from argus.context.assemble import (
    PACKET_SCHEMA,
    assemble_context_bundle,
    refinement_grounded_bundle,
)
from argus.context.caps import ContextCaps
from argus.context.env import is_context_packets_enabled
from argus.context.formatting import bundle_to_refinement_prompt_addon
from argus.context.purposes import ContextPurpose

__all__ = [
    "PACKET_SCHEMA",
    "ContextCaps",
    "ContextPurpose",
    "assemble_context_bundle",
    "bundle_to_refinement_prompt_addon",
    "is_context_packets_enabled",
    "refinement_grounded_bundle",
]
