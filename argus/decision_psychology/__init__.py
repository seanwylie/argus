"""
Decision psychology — confidence/uncertainty adjustments (architecture stubs, gaps).

The scoring implementation lives in :mod:`argus.decision.stub_awareness` and is applied
when building decision candidates (see :func:`argus.decision.from_findings.build_candidates`).
"""

from argus.decision.stub_awareness import (
    StubGapContext,
    apply_stub_awareness_to_candidates,
    infer_stub_gap_context,
)

__all__ = [
    "StubGapContext",
    "apply_stub_awareness_to_candidates",
    "infer_stub_gap_context",
]
