"""Generate :class:`Finding` objects from signals using registered rules."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from argus.core.models.finding import Finding
from argus.core.models.product import ProductNode
from argus.core.models.signal import SignalRecord
from argus.core.models.validation import validate_finding
from argus.findings.candidates import FindingCandidate
from argus.findings.consolidate import merge_candidates
from argus.findings.context import RuleContext
from argus.findings.heuristics import effort_for_kind, resolve_severity
from argus.findings.ids import new_finding_id
from argus.findings.rules.base import FindingRule
from argus.findings.rules.builtins import default_rules


def candidate_to_finding(c: FindingCandidate, product_id: str) -> Finding:
    hints = [c.severity_hint]
    sev = resolve_severity(c.kind, hints)
    eff = effort_for_kind(c.kind)
    f = Finding(
        id=new_finding_id(),
        product_id=product_id,
        kind=c.kind,
        severity=sev,
        effort=eff,
        title=c.title,
        summary=c.summary,
        recommendation=c.recommendation,
        source_signals=list(dict.fromkeys(c.source_signal_ids)),
        evidence=dict(c.evidence),
        confidence=c.confidence,
        created_at=datetime.now(timezone.utc),
    )
    validate_finding(f)
    return f


def generate_findings(
    product: ProductNode,
    signals: list[SignalRecord],
    *,
    rules: list[FindingRule] | None = None,
    repo_root: Path | None = None,
) -> list[Finding]:
    """Run all rules, consolidate, and return validated findings."""
    ctx = RuleContext(product=product, signals=signals)
    rules = rules if rules is not None else default_rules()
    raw: list[FindingCandidate] = []
    for rule in rules:
        raw.extend(rule.evaluate(ctx))
    merged = merge_candidates(raw)
    base = [candidate_to_finding(c, product.id) for c in merged]
    if repo_root is None:
        return base

    from argus.doctrine.load import load_doctrine_for_product
    from argus.doctrine.violations import doctrine_violation_findings
    from argus.lifecycle.scoring import assess_lifecycle
    from argus.strategy.apply import get_strategy_profile

    doc, _err = load_doctrine_for_product(
        repo_root, product.id, product_root=product.product_root
    )
    if doc is None:
        return base
    profile = get_strategy_profile(repo_root)
    assessment = assess_lifecycle(
        product,
        base,
        kill_score_min=profile.kill_score_min,
        move_forward_max=profile.move_forward_max,
    )
    extra = doctrine_violation_findings(
        product,
        signals,
        doc,
        assessment_for_kill_rule=assessment,
    )
    return base + extra
