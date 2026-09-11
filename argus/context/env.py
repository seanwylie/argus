"""Feature flags for the context packet layer."""

from __future__ import annotations

import os


def is_context_packets_enabled() -> bool:
    """When true, refinement uses ``argus.context`` packets for prompt assembly (default: off)."""
    v = (os.environ.get("ARGUS_CONTEXT_PACKETS") or "").strip().lower()
    return v in ("1", "true", "yes", "on")
