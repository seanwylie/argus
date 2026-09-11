# Operator summary (`argus.operator_summary.v1`)

**CLI:** `uv run argus dashboard summary` (`--json`, `--no-save`, `--limit-history N`, optional `--products-dir`)

**Artifacts:** `runs/dashboard/operator_summary/latest.{json,md}` plus timestamped copies.

When not using **`--no-save`**, the same command also writes **`runs/portfolio/builder_activity/latest.{json,md}`** (stamped JSON) — a deterministic Builder invoke/reconcile rollup for portfolio coordination only; **not** outcomes, strategy, or Memory. See [plans/ephemeral-roadmap.md](plans/ephemeral-roadmap.md) Phase 2B.

The HTML dashboard (`argus dashboard`) is unchanged; **`dashboard summary`** is a **single Markdown/JSON pane** meant to be read in under ~30 seconds.

## What it aggregates

Read-only loads (and only **evaluates** portfolio outcomes when `runs/portfolio/outcomes/latest.json` is missing):

| Source | Role |
|--------|------|
| `runs/portfolio/operator_queue/latest.json` | Who is at the top of the queue, readiness tier, debt |
| `runs/portfolio/progression/latest.json` | Recent blocked outcomes |
| `runs/portfolio/quiescence/latest.json` | Quiescence recommendation |
| `runs/portfolio/delta_report/latest.json` | Recommended next portfolio action |
| `runs/portfolio/intervention_inbox/latest.json` | Inbox items (or rebuild in-memory if absent) |
| `runs/portfolio/outcomes/latest.json` | Per-product trajectories (or fresh evaluation) |
| `runs/portfolio/patterns/latest.json` | Systemic patterns (optional) |
| `runs/portfolio/cycle/latest.json` | Latest overall cycle recommendation |
| **`evaluate_portfolio_lifecycle`** | Portfolio lifecycle lanes (creation / steady / wind-down), including **effective signal instrumentation pressure** when scan artifacts exist, refined by optional **`runs/products/signal_instrumentation_apply/latest/*.json`** — see [portfolio-lifecycle.md](portfolio-lifecycle.md), [product-signal-instrumentation.md](product-signal-instrumentation.md) |
| **`evaluate_operator_learning_synthesis`** | Mission-conditioned associative lessons — see policy learning / [operator-policy.md](operator-policy.md) |

## Payload highlights (existing)

- **`headline_status`:** `healthy` · `active` · `degraded` · `attention_needed` (heuristic from blocked counts, negatives, inbox pressure, patterns, quiescence/cycle signals).
- **`top_products_to_watch` / `top_products_to_advance`:** Derived from queue rows (short human reasons).
- **`blocked_products`:** Union of outcomes “blocked” patterns and last progression blocked outcomes.
- **`intervention_inbox_summary` / `outcome_summary` / `systemic_patterns_summary`:** Compact counts and titles.
- **`recommended_next_step`:** One line, preferring the latest **portfolio cycle** recommendation when present.
- **`confidence_level`:** `high` / `medium` / `low` from how many artifact types were found.
- **`builder_activity_snapshot`:** Compact view derived from **`runs/portfolio/builder_activity/latest.json`** when present (`artifact_present`, attention vs routine counts, **`attention_products`** with reason codes, **`recent_runs`**). If the file is missing, **`artifact_present`** is false and an **`operator_hint`** explains how to refresh.

## Additive fields (lifecycle + learning)

- **`lifecycle_snapshot`:** Compact view from portfolio lifecycle: **`lifecycle_counts`**, **`products_entering`**, **`products_exiting`**, **`products_under_repair_pressure`**, **`products_under_retirement_pressure`**, **`products_under_instrumentation_pressure`** (effective), **`products_under_instrumentation_pressure_raw`**, **`products_instrumentation_apply_followup`**, **`products_instrumentation_resolved_via_apply`**, **`products_needing_signal_instrumentation_count`**, **`portfolio_strategy_posture`**, **`summary_narrative`** (when present). **`present`** is true when the lifecycle payload matches `argus.portfolio_lifecycle.v1`.
- **`signal_instrumentation_snapshot`:** **`artifacts_loaded_count`**, **`apply_artifacts_loaded_count`**, **`products_under_instrumentation_pressure`**, **`products_instrumentation_apply_followup`**, **`products_instrumentation_resolved_via_apply`**, optional **`note`** — read-only; does not run instrumentation or apply.
- **`recommended_next_step`:** When the latest cycle recommends **`inspect_specific_products`**, may append a signal-instrumentation hint: **needs contracts** vs **apply follow-up (partial / still weak)** vs **resolved via adequate post-apply** (stagnation may reflect product reality).
- **`top_products_to_watch`:** Queue-derived rows; reasons may distinguish **needs instrumentation**, **apply follow-up**, and **post-apply adequate** (inspect may reflect behavior, not missing telemetry).
- **`learning_snapshot`:** **`top_lessons`** (trimmed from `top_lessons_so_far`), **`sparse_signal_warnings`**, **`strongest_sparse_signal_warning`** (prefers `conflicting_signal:*` when present). **`present`** when learning synthesis schema matches.
- **`inputs`:** Also includes **`products_dir`**, **`lifecycle_run_id`**, **`learning_synthesis_run_id`**.

Older consumers that ignore unknown keys remain compatible; existing top-level fields are unchanged.

## Markdown sections

The report adds **Builder (portfolio rollup)** (after artifact coherence) when the snapshot is present or gracefully notes the missing file; **Portfolio lifecycle** (including instrumentation pressure when present), optional **Signal instrumentation**, and **Operator learning (associative)** before **At a glance**.

## Workflow

Run after your usual portfolio cadence (`portfolio refresh`, `portfolio cycle`, `portfolio outcomes`, `portfolio patterns`, **`portfolio lifecycle`**, policy effectiveness/feedback as needed), then:

```bash
uv run argus dashboard summary
```

**World-context coherence:** When `runs/world_context/latest.json` (and derived interpretation) are present, **`dashboard summary`** also **materializes** `runs/world_context/creation_candidates/latest.json` from the same creation-candidate logic embedded in the operator summary payload — so the on-disk creation-candidate artifact, operator summary JSON, and dashboard/console world-context views stay aligned. (Ingest signals separately via `argus world-context` as needed; then refresh summary.)

Use **`--no-save`** to print only (no files under `runs/dashboard/operator_summary/` or `runs/portfolio/builder_activity/`). With **`--no-save`**, creation-candidate artifacts are **not** written (evaluation still computes embedded fields for stdout JSON when used programmatically).

## Programmatic use

```python
from pathlib import Path
from argus.dashboard.operator_summary import evaluate_operator_summary, run_operator_summary

payload = evaluate_operator_summary(Path("."), limit_history=30, products_dir=None)
run_operator_summary(Path("."), limit_history=30, write_artifacts=True)
```

See also [dashboard.md](dashboard.md) for the HTML dashboard.
