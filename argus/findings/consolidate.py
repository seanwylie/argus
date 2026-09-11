"""Merge finding candidates that address the same issue (multi-signal support)."""

from __future__ import annotations

from argus.findings.candidates import FindingCandidate


def merge_candidates(candidates: list[FindingCandidate]) -> list[FindingCandidate]:
    """
    One merged candidate per ``(kind.value, rule_id, issue_key)``.

    Combines ``source_signal_ids`` (deduplicated), merges evidence shallowly, and keeps
    the stronger confidence when both are set.
    """
    buckets: dict[tuple[str, str, str], FindingCandidate] = {}
    for c in candidates:
        key = (c.kind.value, c.rule_id, c.issue_key)
        if key not in buckets:
            buckets[key] = c
            continue
        o = buckets[key]
        merged_ids = list(dict.fromkeys(o.source_signal_ids + c.source_signal_ids))
        ev = {**o.evidence, **c.evidence}
        ev["source_signal_ids"] = merged_ids
        confs = [x for x in (o.confidence, c.confidence) if x is not None]
        new_conf = max(confs) if confs else None
        summary = o.summary if o.summary == c.summary else f"{o.summary} | {c.summary}"
        buckets[key] = FindingCandidate(
            rule_id=o.rule_id,
            issue_key=o.issue_key,
            kind=o.kind,
            title=o.title,
            summary=summary,
            recommendation=o.recommendation,
            source_signal_ids=merged_ids,
            evidence=ev,
            severity_hint=o.severity_hint or c.severity_hint,
            confidence=new_conf,
        )
    return list(buckets.values())
