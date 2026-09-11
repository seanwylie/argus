# Portfolio history (`argus.portfolio_history.v1`)

**CLI:** `argus portfolio history` (`--json`, `--no-save`, `--limit-history N`)

**Artifacts:** `runs/portfolio/history/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}` (same pattern as other portfolio tools).

## Purpose

Provide a **single canonical spine** for listing, loading, and summarizing stamped portfolio artifacts under `runs/portfolio/` so evaluators and operators do not each re-implement directory walks.

This module **does not** replace quiescence, delta report, or intervention logic; it aggregates **existing** JSON for trend views.

## Shared listing (`argus.portfolio.artifact_index`)

Timestamped portfolio JSON files use the filename pattern **`YYYYMMDDTHHMMSSZ.json`**.  
:func:`argus.portfolio.artifact_index.list_timestamped_portfolio_json_files` returns matching paths **newest first** (lexicographic order matches UTC chronology).

Quiescence, delta report, and intervention reuse this helper instead of duplicating regex walks.

## Artifact types

| Type | Directory | Stamped files |
|------|-------------|----------------|
| `operator_queue` | `runs/portfolio/operator_queue/` | **No** — only `latest.json` (history uses `generated_at_utc` as a synthetic id when present) |
| `progression` | `runs/portfolio/progression/` | Yes |
| `quiescence` | `runs/portfolio/quiescence/` | Yes |
| `delta_report` | `runs/portfolio/delta_report/` | Yes |
| `intervention` | `runs/portfolio/intervention/` | Yes |
| `cycle` | `runs/portfolio/cycle/` | Yes |

## Payload (concise)

- **`artifact_counts`** — Counts of stamped files per type + whether `operator_queue/latest.json` exists.
- **`latest_timestamps`** — Latest stamp (filename stem) per type; queue uses a synthetic `queue:…` id when possible.
- **`alignment.cycles`** — For each recent **cycle** JSON: linked `progression` / `delta` / `intervention` run ids from the cycle summary; **quiescence** is inferred as the greatest stamped quiescence id `≤ cycle_run_id` when cycle artifacts exist (if all quiescence stamps are after the cycle, inference is `null`).
- **`per_product_trends`** — Recent slices of queue rank, readiness tier, next_action, understanding debt, priority score (from **delta** baselines, with **quiescence** baselines filling gaps), plus progression outcomes and intervention rows.
- **`trend_summaries`** — Deterministic buckets such as rising/cooling priority, chronic blocked progression, steady improvement, thrashing / oscillation patterns.

## CLI

```bash
uv run argus portfolio history
uv run argus portfolio history --limit-history 30
uv run argus portfolio history --json
uv run argus portfolio history --no-save
```

## See also

- [portfolio-cycle.md](portfolio-cycle.md)
- [portfolio-delta-report.md](portfolio-delta-report.md)
- [portfolio-quiescence.md](portfolio-quiescence.md)
- [portfolio-intervention.md](portfolio-intervention.md)
