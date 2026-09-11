"""Identifiers for signal records."""

from __future__ import annotations

import uuid


def new_signal_id(prefix: str = "sig") -> str:
    """Return a unique signal id suitable for persistence and correlation."""
    return f"{prefix}-{uuid.uuid4().hex[:16]}"
