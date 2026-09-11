"""
Static registry for Builder execution contract kinds (prepare + reconcile).

**Registry-owned:** ``contract_kind`` → execution contract builder, reconcile outcome deriver,
and :class:`BuilderContractKindSpec` flags (generate-next heuristics, prepare error wrapping).

**Prepare-owned (intentional):** Markdown / prompt templates live in
:mod:`argus.builder.next_expansion_prepare` (``_render_*_markdown``, ``render_prepare_markdown``).
Prepare calls :func:`build_execution_contract_for_kind` so ``builder_task.json`` embeds the same
contract reconcile uses. Keeping renderers out of this module is a deliberate boundary—not a
forgotten plugin hook; folding them in would be a structural refactor, not a bugfix.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from argus.builder.bug_fix_outcome import derive_bug_fix_execution_outcome
from argus.builder.content_slot_outcome import derive_content_slot_execution_outcome
from argus.builder.execution_contract import (
    build_bug_fix_execution_contract,
    build_content_slot_execution_contract,
    build_signal_instrumentation_execution_contract,
)
from argus.builder.signal_instrumentation_outcome import (
    derive_signal_instrumentation_execution_outcome,
)

# --- outcome: unified context (adapts per-kind signatures) ---


@dataclass(frozen=True)
class OutcomeDeriveContext:
    """Arguments needed to classify ``execution_outcome`` for any registered kind."""

    repo_root: Path
    product_id: str
    products_dir: Path | None
    scope_check: dict[str, Any] | None
    invoke_data: dict[str, Any] | None
    task_data: dict[str, Any] | None
    builder_diff_summary: dict[str, Any] | None
    prior_resolved_target: dict[str, Any] | None


ContractBuilder = Callable[..., dict[str, Any]]
OutcomeDeriver = Callable[[OutcomeDeriveContext], dict[str, Any]]


def _outcome_content_slot(ctx: OutcomeDeriveContext) -> dict[str, Any]:
    return derive_content_slot_execution_outcome(
        ctx.repo_root,
        ctx.product_id,
        products_dir=ctx.products_dir,
        scope_check=ctx.scope_check,
        invoke_data=ctx.invoke_data,
        prior_resolved_target=ctx.prior_resolved_target,
        task_data=ctx.task_data,
    )


def _outcome_bug_fix(ctx: OutcomeDeriveContext) -> dict[str, Any]:
    return derive_bug_fix_execution_outcome(
        scope_check=ctx.scope_check,
        invoke_data=ctx.invoke_data,
        task_data=ctx.task_data,
        builder_diff_summary=ctx.builder_diff_summary,
    )


def _outcome_signal_instrumentation(ctx: OutcomeDeriveContext) -> dict[str, Any]:
    return derive_signal_instrumentation_execution_outcome(
        repo_root=ctx.repo_root,
        product_id=ctx.product_id,
        products_dir=ctx.products_dir,
        scope_check=ctx.scope_check,
        invoke_data=ctx.invoke_data,
        task_data=ctx.task_data,
        builder_diff_summary=ctx.builder_diff_summary,
    )


@dataclass(frozen=True)
class BuilderContractKindSpec:
    """Per-kind flags used by prepare and reconcile (no dynamic loading)."""

    kind: str
    display_name: str
    requires_allowed_paths_exact: bool
    skip_generate_next_expansion_heuristic: bool
    """When True, ``--generate-next-expansion`` skips the content-catalog heuristic for this target."""
    wrap_valueerror_as_prepare_error: bool
    """When True, :func:`build_execution_contract_for_kind` ValueErrors are mapped by prepare."""
    manual_set_target_eligible: bool
    """When True, :func:`manual_set_target_kinds` and ``argus builder set-target`` may stamp this kind."""


BUILDER_CONTRACT_KIND_SPECS: dict[str, BuilderContractKindSpec] = {
    "content_slot": BuilderContractKindSpec(
        kind="content_slot",
        display_name="content slot",
        requires_allowed_paths_exact=False,
        skip_generate_next_expansion_heuristic=False,
        wrap_valueerror_as_prepare_error=False,
        manual_set_target_eligible=False,
    ),
    "bug_fix": BuilderContractKindSpec(
        kind="bug_fix",
        display_name="bug fix",
        requires_allowed_paths_exact=True,
        skip_generate_next_expansion_heuristic=True,
        wrap_valueerror_as_prepare_error=True,
        manual_set_target_eligible=True,
    ),
    "signal_instrumentation": BuilderContractKindSpec(
        kind="signal_instrumentation",
        display_name="signal instrumentation",
        requires_allowed_paths_exact=True,
        skip_generate_next_expansion_heuristic=True,
        wrap_valueerror_as_prepare_error=True,
        manual_set_target_eligible=True,
    ),
}

_SUPPORTED_KINDS_TUPLE: tuple[str, ...] = tuple(BUILDER_CONTRACT_KIND_SPECS.keys())


def _non_content_slot_outcome_kinds() -> tuple[str, ...]:
    """Kinds that use a non-:func:`derive_content_slot_execution_outcome` deriver (all registered but ``content_slot``)."""
    return tuple(sorted(k for k in BUILDER_CONTRACT_KIND_SPECS if k != "content_slot"))

_CONTRACT_BUILDERS: dict[str, ContractBuilder] = {
    "content_slot": build_content_slot_execution_contract,
    "bug_fix": build_bug_fix_execution_contract,
    "signal_instrumentation": build_signal_instrumentation_execution_contract,
}

_OUTCOME_DERIVERS: dict[str, OutcomeDeriver] = {
    "content_slot": _outcome_content_slot,
    "bug_fix": _outcome_bug_fix,
    "signal_instrumentation": _outcome_signal_instrumentation,
}


def supported_target_types() -> frozenset[str]:
    """``primary_target.target_type`` values with a registered prepare/reconcile story."""
    return frozenset(BUILDER_CONTRACT_KIND_SPECS.keys())


def manual_set_target_kinds() -> frozenset[str]:
    """
    Contract kinds that ``argus builder set-target`` / :mod:`argus.builder.set_manual_target`
    may write. Subset of registered kinds (excludes ``content_slot`` and any kind only meant for
    generated/heuristic targets). New kinds: set the flag on :class:`BuilderContractKindSpec` and
    extend ``set_manual_target`` / CLI if the kind needs bespoke fields.
    """
    return frozenset(
        k
        for k, spec in BUILDER_CONTRACT_KIND_SPECS.items()
        if spec.manual_set_target_eligible
    )


def get_kind_spec(kind: str) -> BuilderContractKindSpec | None:
    return BUILDER_CONTRACT_KIND_SPECS.get(kind)


def build_execution_contract_for_kind(
    kind: str,
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> dict[str, Any]:
    """
    Dispatch to the registered contract builder. May raise ``ValueError`` from builders
    that validate ``primary_target`` (e.g. bug_fix) — see ``wrap_valueerror_as_prepare_error``.
    """
    fn = _CONTRACT_BUILDERS.get(kind)
    if fn is None:
        raise KeyError(f"unknown contract kind {kind!r} (supported: {sorted(_CONTRACT_BUILDERS)})")
    return fn(
        repo_root=repo_root,
        product_id=product_id,
        products_dir=products_dir,
        raw=raw,
        pt=pt,
    )


def resolve_reconcile_outcome_kind(contract_kind: str, resolved_target_type: str) -> str:
    """
    Choose which outcome deriver key to pass to :func:`derive_execution_outcome_for_kind`.

    If ``contract_kind`` or ``resolved_target_type`` matches any registered non-``content_slot``
    kind, return that kind; otherwise ``content_slot``. Derived from
    ``BUILDER_CONTRACT_KIND_SPECS`` so new non-content-slot kinds do not require a second hard-coded
    tuple here (outcome derivers must still be registered in ``_OUTCOME_DERIVERS``).
    """
    ck = str(contract_kind or "").strip()
    rt = str(resolved_target_type or "").strip()
    for k in _non_content_slot_outcome_kinds():
        if ck == k or rt == k:
            return k
    return "content_slot"


def derive_execution_outcome_for_kind(kind: str, ctx: OutcomeDeriveContext) -> dict[str, Any]:
    """Run the registered outcome deriver for ``kind`` (must be a registered key)."""
    fn = _OUTCOME_DERIVERS.get(kind)
    if fn is None:
        raise KeyError(f"unknown outcome kind {kind!r} (supported: {sorted(_OUTCOME_DERIVERS)})")
    return fn(ctx)


__all__ = [
    "BUILDER_CONTRACT_KIND_SPECS",
    "BuilderContractKindSpec",
    "OutcomeDeriveContext",
    "build_execution_contract_for_kind",
    "derive_execution_outcome_for_kind",
    "get_kind_spec",
    "manual_set_target_kinds",
    "resolve_reconcile_outcome_kind",
    "supported_target_types",
]
