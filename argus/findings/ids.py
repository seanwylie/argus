"""Identifiers for findings."""

from __future__ import annotations

import uuid


def new_finding_id() -> str:
    return f"find-{uuid.uuid4().hex[:16]}"
