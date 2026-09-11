# Operator narrative (`argus.operator_narrative.v1`)

**CLI:** `uv run argus dashboard narrative` (`--json`, `--no-save`, `--limit-history`, optional `--products-dir`)

**Artifacts:** `runs/dashboard/narrative/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}`.

## Purpose

Summarize **what has been happening** across recent portfolio activity in one place, without opening many files. The narrative is **read-only**: it aggregates existing artifacts only (no pipeline execution).

Inputs combined:

- **Portfolio history** (`evaluate_portfolio_history`) — per-product trends, rising/cooling/improving/thrashing summaries
- **Outcomes** (`evaluate_portfolio_outcomes`) — positive / negative / no-movement counts
- **Delta reports** (recent stamped JSONs) — union of `what_improved` / `what_regressed` product ids
- **Patterns** (`evaluate_portfolio_patterns`) — systemic issues, isolated negatives
- **Intervention history** (recent stamped intervention JSONs) — average flagged load; compares older vs recent halves of the series
- **Cycle summaries** (recent stamped cycle JSONs) — counts of `overall_operator_recommendation` values
- **Portfolio lifecycle** (`evaluate_portfolio_lifecycle`) — creation / steady / wind-down lanes, entering/exiting ids, strategy posture (see [portfolio-lifecycle.md](portfolio-lifecycle.md))
- **Operator learning synthesis** (`evaluate_operator_learning_synthesis`) — mission-conditioned associative takeaways (not causal; see learning synthesis under `runs/policy/learning_synthesis/`)

## Schema

- **`sections.overall_trajectory`:** `improving` | `stagnating` | `degrading` | `mixed` | `unknown` — deterministic heuristic from outcomes mix, history trend buckets, and cycle recommendation counts.
- **`sections`:** includes **`key_wins`**, **`key_regressions`**, **`recurring_issues`**, **`notable_product_movements`**, **`systemic_observations`**, **`recommended_next_focus`**, plus:
  - **`lifecycle_transitions`:** Short strings derived from lifecycle counts, entering/exiting ids, strategy posture.
  - **`signal_instrumentation`:** Derived from lifecycle synthesis — distinguishes **still needs instrumentation**, **worker apply follow-up** (partial or still weak post-apply), and **adequate post-apply** (prefer learning over assuming missing observability when scans and applies disagree). Read-only; does not run instrumentation or the worker.
  - **`operator_learning`:** Strings from **`top_lessons_so_far`** (trimmed).
  - **`intervention_pressure`:** Intervention load note (when series is long enough) plus summary of flagged-product counts across stamps — distinct from **recurring_issues** (patterns + history), though **`recurring_issues`** may still include the same intervention load sentence for backward compatibility.
- **`narrative_text`:** Multi-paragraph story with explicit **Portfolio lifecycle**, **Signal instrumentation** (when applicable), **Operator learning**, and **Intervention pressure** paragraphs before wins/regressions-style content.
- **`sparse_history_warning`:** True when few stamped cycles/deltas are loaded — interpret cautiously.
- **`sources`:** Includes **`portfolio_lifecycle_run_id`** and **`operator_learning_synthesis_run_id`**.

## Three lenses in the Markdown report

1. **Portfolio lifecycle** — lanes and transitions (creation vs wind-down).
2. **Signal instrumentation** — effective pressure, apply follow-up, and post-apply resolution (when artifacts exist); see **`runs/products/signal_instrumentation/latest/`** and **`runs/products/signal_instrumentation_apply/latest/`**.
3. **Operator learning** — associative lessons from mission/policy artifacts.
4. **Intervention pressure** — intervention stamp load and trends.

**Portfolio motion** (wins / regressions / movements / systemic observations) follows under clearly labeled headings.

## CLI

```bash
uv run argus dashboard narrative
uv run argus dashboard narrative --json
uv run argus dashboard narrative --no-save
uv run argus dashboard narrative --limit-history 40
```

## See also

- [portfolio-history.md](portfolio-history.md)
- [portfolio-outcomes.md](portfolio-outcomes.md)
- [portfolio-patterns.md](portfolio-patterns.md)
- [portfolio-lifecycle.md](portfolio-lifecycle.md)
- [operator-summary.md](operator-summary.md)
