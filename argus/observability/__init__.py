"""Inspectability helpers for observability contracts (additive; no pipeline side effects)."""

from argus.observability.signal_contract import (
    SIGNAL_CONTRACT_EVALUATION_SCHEMA,
    build_signal_contract_summary_from_evaluation,
    compact_signal_contract_row_fields,
    compute_signal_contract_hint,
    evaluate_signal_contract,
    golden_signals_for_product_type,
    mission_signals_for_mission,
    render_signal_contract_markdown,
    resolve_product_type_bucket,
    signal_contract_context_for_escalation,
    write_signal_contract_artifact,
)

__all__ = [
    "SIGNAL_CONTRACT_EVALUATION_SCHEMA",
    "build_signal_contract_summary_from_evaluation",
    "compact_signal_contract_row_fields",
    "compute_signal_contract_hint",
    "evaluate_signal_contract",
    "golden_signals_for_product_type",
    "mission_signals_for_mission",
    "render_signal_contract_markdown",
    "resolve_product_type_bucket",
    "signal_contract_context_for_escalation",
    "write_signal_contract_artifact",
]
