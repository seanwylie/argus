"""Aggregate counts and notes from capability entries."""

from __future__ import annotations

from collections import Counter

from argus.audit.models import AuditCapabilityEntry, AuditStatus


def counts_by_status(entries: list[AuditCapabilityEntry]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for e in entries:
        c[e.status.value] += 1
    return {k: c[k] for k in sorted(c.keys())}


def ensure_all_status_keys(counts: dict[str, int]) -> dict[str, int]:
    out: dict[str, int] = {s.value: 0 for s in AuditStatus}
    out.update(counts)
    return out
