# Portfolio cycle scheduler (`argus.portfolio_scheduler_session.v1`)

**CLI:** `argus portfolio schedule` (`--max-cycles`, `--limit-per-cycle`, `--dry-run`, `--skip-import-failed`, `--skip-waiting`, `--json`, `--no-save`)

**Artifacts:** `runs/portfolio/scheduler/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}`.

## Purpose

Run **several bounded** [`argus portfolio cycle`](portfolio-cycle.md) iterations in-process, with **explicit stop rules** so autonomy stays short, capped, and reviewable. After each cycle the scheduler consults the same signals the cycle already surfaces (quiescence recommendation, synthesized overall operator recommendation, material-change counts, intervention load) and decides whether another cycle is justified.

This is **not** a daemon: there is no background loop, no unbounded polling, and no subprocess fan-out—only repeated calls to `run_portfolio_cycle` up to `--max-cycles`.

## Stop reasons

The session ends when **any** of these fire (first match wins in a given iteration after the cycle completes, except the sentinel which is checked **before** starting another cycle):

| Reason | Meaning |
|--------|---------|
| `cycle_overall_recommendation` | `summary.overall_operator_recommendation` is `request_human_review`, `repair_imports`, or `inspect_specific_products` — stop before more blind automation. |
| `quiescence_recommendation` | Quiescence recommends `wait`, `inspect`, `import_refresh`, or `human_review`. |
| `no_material_change_streak` | `products_with_material_change_count` stayed at **0** for **N** consecutive completed cycles (default N=3). |
| `intervention_heavy_streak` | Flagged product count ≥ threshold (default **4**) for **M** consecutive cycles (default **M=2**). |
| `max_cycles_reached` | Ran `--max-cycles` iterations without an earlier stop (hard cap). |
| `explicit_stop_sentinel` | File `runs/portfolio/scheduler/STOP` exists when starting a further iteration (after at least one cycle). Remove the file to clear. |

## Guardrails (non-negotiables)

- **Hard cap:** `--max-cycles` prevents infinite loops.
- **No uncontrolled progression:** each cycle still honors `--limit-per-cycle` inside portfolio progression (same semantics as `argus portfolio cycle --limit`).
- **Intervention and synthesis:** heavy or high-priority operator outcomes stop the session so work does not pile up unattended.
- **Quiescence:** portfolio “wait / inspect / human / import refresh” paths stop the scheduler; only `run_again`-style continuation keeps looping.

## Session payload

Schema: `argus.portfolio_scheduler_session.v1`. Useful fields:

- `cycles_run`, `cycle_run_ids` — linkage to each `argus.portfolio_cycle.v1` `run_id`.
- `stop_reason`, `stop_reason_codes` — machine-readable stop explanation.
- `per_cycle` — one row per cycle (quiescence rec, material Δ count, flagged count, overall recommendation, progression `summary_counts` rollup).
- `session_summary` — aggregates progression counts across the session and an end-of-session `evaluate_portfolio_outcomes` snapshot when possible.

## CLI

```bash
uv run argus portfolio schedule --max-cycles 3 --limit-per-cycle 5
uv run argus portfolio schedule --dry-run
uv run argus portfolio schedule --json
uv run argus portfolio schedule --no-save
```

- **`--no-save`** — Skips **scheduler** artifacts under `runs/portfolio/scheduler/` only. Each cycle still writes stage outputs under `runs/portfolio/{operator_queue,progression,quiescence,delta_report,intervention}/` and `runs/portfolio/cycle/` unless you change cycle behavior separately (not exposed on this command).

Optional **`--products-dir`** follows the same convention as other portfolio commands.

## See also

- [portfolio-cycle.md](portfolio-cycle.md)
- [portfolio-quiescence.md](portfolio-quiescence.md)
- [portfolio-intervention.md](portfolio-intervention.md)
