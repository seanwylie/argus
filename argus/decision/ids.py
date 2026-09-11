"""Identifiers for decision candidates."""

from __future__ import annotations

import uuid


def new_decision_id() -> str:
    return f"dec-{uuid.uuid4().hex[:16]}"
