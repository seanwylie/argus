"""
Deterministic ``builder_next_prompt.md`` + ``builder_task.json`` from ``content/next_expansion.json``.

**Owns:** Markdown prompt rendering (``_render_*_markdown``, :func:`render_prepare_markdown`),
``builder_task.json`` assembly, and filesystem writes (:func:`write_prepare_artifacts`).

**Does not own:** Execution contract logic or outcome classification—those are in
:mod:`argus.builder.execution_contract` and :mod:`argus.builder.contract_registry`.
Prepare imports :func:`~argus.builder.contract_registry.build_execution_contract_for_kind` so the
embedded ``execution_contract`` matches reconcile. That split is intentional.

No execution, scheduling, or LLM planning — template bridge only. For non-content-slot kinds, operators
typically stamp ``primary_target`` first via ``argus builder set-target`` (see
``docs/builder-execution-contract.md``), then run ``prepare``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from argus.builder.contract_registry import (
    build_execution_contract_for_kind,
    get_kind_spec,
    supported_target_types,
)
from argus.core.serialize import dumps_json

NEXT_EXPANSION_RELPATH = "content/next_expansion.json"
SCHEMA_SUPPORTED = frozenset({"argus.next_expansion.v1"})


class NextExpansionPrepareError(ValueError):
    """Invalid or missing next-expansion artifact."""


def _unsupported_target_type_error(ttype: str) -> NextExpansionPrepareError:
    return NextExpansionPrepareError(
        f"Unsupported target_type {ttype!r} (supported: {sorted(supported_target_types())})"
    )


def _products_base(repo_root: Path, products_dir: Path | None) -> Path:
    if products_dir is not None:
        return (repo_root / products_dir).resolve()
    return (repo_root / "products").resolve()


def next_expansion_path(
    repo_root: Path, product_id: str, *, products_dir: Path | None = None
) -> Path:
    return (_products_base(repo_root, products_dir) / product_id / NEXT_EXPANSION_RELPATH).resolve()


def validate_next_expansion_payload(raw: Any) -> dict[str, Any]:
    """
    Same structural checks as :func:`load_next_expansion` after JSON parse (no file I/O).
    Used by CLI helpers that construct ``next_expansion`` documents in memory.
    """
    if not isinstance(raw, dict):
        raise NextExpansionPrepareError("next_expansion root must be an object")
    sch = str(raw.get("schema") or "")
    if sch and sch not in SCHEMA_SUPPORTED:
        raise NextExpansionPrepareError(
            f"Unsupported next_expansion schema {sch!r} (supported: {sorted(SCHEMA_SUPPORTED)})"
        )
    pt = raw.get("primary_target")
    if not isinstance(pt, dict):
        raise NextExpansionPrepareError("primary_target must be an object")
    ttype = str(pt.get("target_type") or "").strip()
    spec = get_kind_spec(ttype)
    if spec and spec.requires_allowed_paths_exact:
        ape = pt.get("allowed_paths_exact")
        if not isinstance(ape, list) or not ape:
            raise NextExpansionPrepareError(
                f"{ttype} primary_target requires non-empty allowed_paths_exact "
                "(repo-relative paths under the product directory)"
            )
        for p in ape:
            if not isinstance(p, str) or not str(p).strip():
                raise NextExpansionPrepareError(
                    f"{ttype} allowed_paths_exact entries must be non-empty strings"
                )
    return raw


def load_next_expansion(
    repo_root: Path, product_id: str, *, products_dir: Path | None = None
) -> dict[str, Any]:
    p = next_expansion_path(repo_root, product_id, products_dir=products_dir)
    if not p.is_file():
        raise NextExpansionPrepareError(
            f"Missing {NEXT_EXPANSION_RELPATH} for product {product_id!r} (expected {p})"
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise NextExpansionPrepareError(f"Invalid JSON in {p}: {e}") from e
    return validate_next_expansion_payload(raw)


def _render_content_slot_markdown(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> str:
    tid = str(pt.get("id") or "").strip()
    group_id = str(pt.get("group_id") or "").strip()
    rationale = str(pt.get("rationale") or "").strip()
    basis = pt.get("basis")
    basis_lines = ""
    if isinstance(basis, list):
        basis_lines = "\n".join(f"- {x}" for x in basis if str(x).strip())
    confidence = str(pt.get("confidence") or "").strip()
    non_tg = raw.get("explicit_non_targets")
    non_block = ""
    if isinstance(non_tg, list) and non_tg:
        non_block = "\n**Explicit non-targets (do not prioritize unless scope changes):**\n"
        for item in non_tg:
            if isinstance(item, dict):
                non_block += f"- `{item.get('id')}`: {item.get('reason', '')}\n"

    ec = build_execution_contract_for_kind(
        "content_slot",
        repo_root=repo_root,
        product_id=product_id,
        products_dir=products_dir,
        raw=raw,
        pt=pt,
    )
    pr_root = ec["repo_product_root_relative"]
    allowed_bullets = "\n".join(f"- `{p}`" for p in ec.get("allowed_paths_exact", []))
    pat_bullets = "\n".join(f"- `{p}`" for p in ec.get("allowed_path_patterns", []))
    forbid_bullets = "\n".join(f"- {x}" for x in ec.get("forbidden_absolute", []))

    return f"""# Builder task — content slot (bounded execution contract)

## Task identity

| Field | Value |
|------|--------|
| **Product** | `{product_id}` |
| **Increment (this run only)** | `{tid}` |
| **Group** | `{group_id}` |
| **Product tree (repo-relative)** | `{pr_root}/` |
| **Contract** | `{ec.get("schema")}` (`{ec.get("contract_kind")}`) |

## Bounded execution mandate (read first)

You are performing **exactly one** bounded increment: **content slot `{tid}`** in **`{pr_root}/`**.

- **One slot per run.** Do not start the next planned slot, group hub-only work, or cross-group work in this session.
- **Stop when `{tid}` is truthfully implemented** at the agreed depth (outline or better). Then **stop** — do not “also” complete future backlog items.
- **No improvisational sprawl:** if you are blocked (missing context, conflicting files), **stop and report the blocker** instead of refactoring broadly or guessing.

## Allowed scope (paths under `{pr_root}/`)

These are the **only** product-relative paths this increment is authorized to touch (plus pattern matches):

**Exact paths:**
{allowed_bullets}

**Patterns (same group / neighbor HTML only):**
{pat_bullets}

## Next expansion / declared target policy

{ec.get("next_expansion_policy_detail", "")}

Do **not** move `primary_target` to a different content slot in this run. Declared target advancement is owned by **reconcile** (`argus builder reconcile --generate-next-expansion`) after verification — not by jumping ahead in the agent session.

## Forbidden

{forbid_bullets}

Additionally:

- Do **not** edit `generated/builder_next_prompt.md` or `generated/builder_task.json` by hand unless an operator explicitly asked (these are Argus outputs).
- Do **not** edit other products under `products/` or Argus core under `argus/`.
- Do **not** perform drive-by cleanup, wide renames, dependency upgrades, or “while I’m here” refactors.

## Source rationale (from product)

- Rationale: {rationale}

**Basis (from product):**
{basis_lines if basis_lines else "- (none listed)"}

**Confidence (heuristic):** {confidence or "unspecified"}
{non_block}

## Argus / MDC constraints

- Keep changes under `{pr_root}/` in this monorepo checkout.
- **No fake users, traffic, or revenue.** Metrics stay honest.
- **No readiness regression:** after edits, `uv run argus signals collect {product_id}` and `uv run argus findings generate {product_id} --json` should remain consistent with `lifecycle.stage` (no fake launch signals).

## Success criteria

{ec.get("success_conditions_summary", "")}

## Stop conditions

{ec.get("stop_conditions_summary", "")}

## Working steps (aligned to scope)

1. Read `content/content_catalog.json` and neighbor slot JSON under `content/slots/` as needed for `{tid}` only.
2. Add or upgrade `content/slots/{tid}.json` for this slot.
3. Update `content_catalog.json` for this slot’s row only (not other groups).
4. Add or update `app/site/slot/{tid}.html`; adjust **only** neighboring slot HTML files if required for prev/next links (same group).
5. Update group hub / index / groups listing only as needed to surface this slot — stay within allowed paths/patterns above.
6. Update metrics JSON / JSONL **only** if counts change materially; preserve disclaimers.
7. **Do not** edit `primary_target` in `content/next_expansion.json` to advance to the next slot; reconcile owns that transition.

## Verification

```bash
uv run argus signals collect {product_id}
uv run argus findings generate {product_id} --json
```

## Required output (honesty contract)

{ec.get("output_contract_summary", "")}

End with a **short report**: files touched, partial vs complete work, blockers, and whether you stayed inside allowed scope.

---
*Generated by Argus Builder (`argus builder prepare`). `execution_contract` is embedded in `builder_task.json` for reconcile scope checks. Human review required before merge.*
"""


def _render_bug_fix_markdown(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> str:
    try:
        ec = build_execution_contract_for_kind(
            "bug_fix",
            repo_root=repo_root,
            product_id=product_id,
            products_dir=products_dir,
            raw=raw,
            pt=pt,
        )
    except ValueError as e:
        raise NextExpansionPrepareError(str(e)) from e

    pr_root = ec["repo_product_root_relative"]
    allowed_bullets = "\n".join(f"- `{p}`" for p in ec.get("allowed_paths_exact", []))
    pat_bullets = "\n".join(f"- `{p}`" for p in ec.get("allowed_path_patterns", []))
    forbid_bullets = "\n".join(f"- {x}" for x in ec.get("forbidden_absolute", []))
    bug_id = ec.get("bug_id", "")
    bs = str(ec.get("bug_statement") or "").strip()
    sc = str(ec.get("success_condition") or "").strip()
    stc = str(ec.get("stop_condition") or "").strip()

    return f"""# Builder task — bug fix (bounded execution contract)

## Task identity

| Field | Value |
|------|--------|
| **Product** | `{product_id}` |
| **Bug fix id (this run only)** | `{bug_id}` |
| **Product tree (repo-relative)** | `{pr_root}/` |
| **Contract** | `{ec.get("schema")}` (`{ec.get("contract_kind")}`) |

## Bounded execution mandate (read first)

You are performing **exactly one** bounded **bug fix** under `{pr_root}/`.

- **Minimal change:** fix only what is required for this bug. Do **not** refactor, rename broadly, upgrade dependencies, or clean up unrelated code.
- **No adjacent features:** do not "also" improve neighboring modules unless explicitly in the allowed paths below.
- **Stop when done:** after the fix is in, **stop** — do not continue with backlog items.
- If blocked (missing context, conflicting files), **stop and report the blocker** instead of guessing.

## Allowed scope (paths under `{pr_root}/`)

**Exact paths:**
{allowed_bullets}

**Patterns (optional):**
{pat_bullets if pat_bullets.strip() else "- *(none)*"}

## Declared target policy

{ec.get("next_expansion_policy_detail", "")}

Do **not** move `primary_target` to a different task in this run.

## Forbidden

{forbid_bullets}

Additionally:

- Do **not** edit `generated/builder_next_prompt.md` or `generated/builder_task.json` by hand unless an operator explicitly asked.
- Do **not** edit other products under `products/` or Argus core under `argus/`.

## Bug statement (from product)

{bs if bs else "(none)"}

## Success condition

{sc if sc else "(see contract success_conditions_summary)"}

## Stop condition

{stc if stc else "(see contract stop_conditions_summary)"}

## Success criteria (summary)

{ec.get("success_conditions_summary", "")}

## Stop conditions (summary)

{ec.get("stop_conditions_summary", "")}

## Working steps

1. Reproduce or understand the bug within scope.
2. Change **only** files under the allowed paths/patterns above.
3. Run minimal verification appropriate to the product (tests, lint) — only if already part of the repo workflow; do not add new infrastructure in this run.

## Required output (honesty contract)

{ec.get("output_contract_summary", "")}

---
*Generated by Argus Builder (`argus builder prepare`). `execution_contract` is embedded in `builder_task.json` for reconcile scope checks. Human review required before merge.*
"""


def _render_signal_instrumentation_markdown(
    *,
    repo_root: Path,
    product_id: str,
    products_dir: Path | None,
    raw: dict[str, Any],
    pt: dict[str, Any],
) -> str:
    try:
        ec = build_execution_contract_for_kind(
            "signal_instrumentation",
            repo_root=repo_root,
            product_id=product_id,
            products_dir=products_dir,
            raw=raw,
            pt=pt,
        )
    except ValueError as e:
        raise NextExpansionPrepareError(str(e)) from e

    pr_root = ec["repo_product_root_relative"]
    allowed_bullets = "\n".join(f"- `{p}`" for p in ec.get("allowed_paths_exact", []))
    pat_bullets = "\n".join(f"- `{p}`" for p in ec.get("allowed_path_patterns", []))
    forbid_bullets = "\n".join(f"- {x}" for x in ec.get("forbidden_absolute", []))
    sig_id = ec.get("signal_id", "")
    ss = str(ec.get("signal_statement") or "").strip()
    sc = str(ec.get("success_condition") or "").strip()
    stc = str(ec.get("stop_condition") or "").strip()
    ep = ec.get("expected_product_paths_exist") or []
    ep_lines = "\n".join(f"- `{p}`" for p in ep) if isinstance(ep, list) and ep else "- *(none declared)*"
    itp = ec.get("instrumentation_touch_paths") or []
    itp_lines = (
        "\n".join(f"- `{p}`" for p in itp) if isinstance(itp, list) and itp else "- *(none — diff-only evidence)*"
    )

    return f"""# Builder task — signal instrumentation (bounded execution contract)

## Task identity

| Field | Value |
|------|--------|
| **Product** | `{product_id}` |
| **Instrumentation id (this run only)** | `{sig_id}` |
| **Product tree (repo-relative)** | `{pr_root}/` |
| **Contract** | `{ec.get("schema")}` (`{ec.get("contract_kind")}`) |

## Bounded execution mandate (read first)

You are performing **exactly one** bounded **signal / instrumentation** change under `{pr_root}/`.

- **Minimal surface:** add or adjust only the wiring needed for the declared signal/instrumentation. Do **not** redesign observability, metrics pipelines, or cross-cutting logging.
- **No adjacent refactors:** do not rename modules, sweep style, or “clean up” callers unless explicitly in allowed paths.
- **No feature creep:** do not ship product features under the guise of instrumentation.
- **Stop when wired:** after the minimal change, **stop** — do not continue with backlog items.
- If blocked (unclear hook points, missing contracts), **stop and report the blocker** instead of guessing.

## Allowed scope (paths under `{pr_root}/`)

**Exact paths:**
{allowed_bullets}

**Patterns (optional):**
{pat_bullets if pat_bullets.strip() else "- *(none)*"}

## Declared target policy

{ec.get("next_expansion_policy_detail", "")}

Do **not** move `primary_target` to a different task in this run.

## Forbidden

{forbid_bullets}

Additionally:

- Do **not** edit `generated/builder_next_prompt.md` or `generated/builder_task.json` by hand unless an operator explicitly asked.
- Do **not** edit other products under `products/` or Argus core under `argus/`.

## Signal / instrumentation statement (from product)

{ss if ss else "(none)"}

## Postcondition paths (optional — reconcile may check existence)

These are **product-relative** paths that should exist after your work (for evidence only — not proof telemetry fires):

{ep_lines}

## Instrumentation touch hints (optional)

If set, git-touched files under the product should match these (fnmatch/exact). Empty means diff-only evidence:

{itp_lines}

## Success condition

{sc if sc else "(see contract success_conditions_summary)"}

## Stop condition

{stc if stc else "(see contract stop_conditions_summary)"}

## Success criteria (summary)

{ec.get("success_conditions_summary", "")}

## Stop conditions (summary)

{ec.get("stop_conditions_summary", "")}

## Working steps

1. Locate the minimal hook or adapter point **only** within allowed paths.
2. Add or adjust instrumentation **only** as needed for this task — no broad redesign.
3. Run `uv run argus signals collect {product_id}` / findings only if already part of the workflow; do not add new infrastructure in this run.

## Required output (honesty contract)

{ec.get("output_contract_summary", "")}

---
*Generated by Argus Builder (`argus builder prepare`). `execution_contract` is embedded in `builder_task.json` for reconcile scope checks. Human review required before merge.*
"""


# Keys must match ``contract_registry.supported_target_types()`` — registry dispatches contract
# builders; this table only maps ``target_type`` → markdown body (prepare-owned).
_RENDERERS: dict[str, Callable[..., str]] = {
    "bug_fix": _render_bug_fix_markdown,
    "signal_instrumentation": _render_signal_instrumentation_markdown,
    "content_slot": _render_content_slot_markdown,
}


def render_prepare_markdown(
    *,
    repo_root: Path,
    product_id: str,
    raw: dict[str, Any],
    products_dir: Path | None = None,
) -> str:
    """Dispatch to the per-kind markdown renderer (prepare-only; no ``execution_contract`` here)."""
    pt = raw["primary_target"]
    ttype = str(pt.get("target_type") or "").strip()
    if ttype not in supported_target_types():
        raise _unsupported_target_type_error(ttype)
    fn = _RENDERERS[ttype]
    return fn(
        repo_root=repo_root,
        product_id=product_id,
        products_dir=products_dir,
        raw=raw,
        pt=pt,
    )


def build_prepare_task_payload(
    *,
    repo_root: Path,
    product_id: str,
    raw: dict[str, Any],
    prompt_markdown: str,
    prompt_template_id: str,
    products_dir: Path | None = None,
    execution_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pt = raw["primary_target"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rel_base = products_dir if products_dir is not None else Path("products")
    out: dict[str, Any] = {
        "schema": "argus.builder_prepare_task.v1",
        "generated_at_utc": now,
        "product_id": product_id,
        "source_next_expansion_path": str(rel_base / product_id / NEXT_EXPANSION_RELPATH),
        "resolved_target": {
            "id": pt.get("id"),
            "target_type": pt.get("target_type"),
            "group_id": pt.get("group_id"),
        },
        "prompt_template_id": prompt_template_id,
        "review_required": True,
        "dry_run": True,
        "prompt_character_count": len(prompt_markdown),
        "repo_root_hint": str(repo_root),
    }
    if execution_contract:
        out["execution_contract"] = execution_contract
    return out


@dataclass(frozen=True)
class PrepareArtifacts:
    """Paths for builder_next_prompt.md and builder_task.json."""

    prompt_md: Path
    task_json: Path


@dataclass(frozen=True)
class PrepareResult:
    """Deterministic prompt + task payload and target output paths (may or may not exist on disk)."""

    markdown: str
    task: dict[str, Any]
    paths: PrepareArtifacts


def prepare_output_dir(
    repo_root: Path,
    product_id: str,
    *,
    under: str,
    products_dir: Path | None = None,
) -> Path:
    """``under`` is ``product`` or ``runs``."""
    if under == "product":
        return (_products_base(repo_root, products_dir) / product_id / "generated").resolve()
    if under == "runs":
        return (repo_root / "runs" / "builder" / "prepare" / product_id).resolve()
    raise ValueError(f"unknown output mode: {under!r}")


def build_prepare_result(
    repo_root: Path,
    product_id: str,
    *,
    under: str = "product",
    products_dir: Path | None = None,
) -> PrepareResult:
    """Load next_expansion, render markdown and task dict; does not write files."""
    raw = load_next_expansion(repo_root, product_id, products_dir=products_dir)
    md = render_prepare_markdown(
        repo_root=repo_root, product_id=product_id, raw=raw, products_dir=products_dir
    )
    pt = raw["primary_target"]
    ttype = str(pt.get("target_type") or "").strip()
    template_id = f"builder_v0.{ttype}"
    execution_contract: dict[str, Any] | None = None
    spec = get_kind_spec(ttype)
    if spec is None:
        raise _unsupported_target_type_error(ttype)
    try:
        execution_contract = build_execution_contract_for_kind(
            ttype,
            repo_root=repo_root,
            product_id=product_id,
            products_dir=products_dir,
            raw=raw,
            pt=pt,
        )
    except ValueError as e:
        if spec.wrap_valueerror_as_prepare_error:
            raise NextExpansionPrepareError(str(e)) from e
        raise
    task = build_prepare_task_payload(
        repo_root=repo_root,
        product_id=product_id,
        raw=raw,
        prompt_markdown=md,
        prompt_template_id=template_id,
        products_dir=products_dir,
        execution_contract=execution_contract,
    )
    out = prepare_output_dir(repo_root, product_id, under=under, products_dir=products_dir)
    prompt_path = out / "builder_next_prompt.md"
    task_path = out / "builder_task.json"
    return PrepareResult(
        markdown=md,
        task=task,
        paths=PrepareArtifacts(prompt_md=prompt_path, task_json=task_path),
    )


def write_prepare_artifacts(
    repo_root: Path,
    product_id: str,
    *,
    under: str = "product",
    products_dir: Path | None = None,
) -> PrepareResult:
    """Write builder_next_prompt.md and builder_task.json; returns content + paths."""
    res = build_prepare_result(
        repo_root, product_id, under=under, products_dir=products_dir
    )
    res.paths.prompt_md.parent.mkdir(parents=True, exist_ok=True)
    res.paths.prompt_md.write_text(res.markdown, encoding="utf-8")
    res.paths.task_json.write_text(dumps_json(res.task), encoding="utf-8")
    return res
