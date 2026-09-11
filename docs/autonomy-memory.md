# Cross-session autonomy memory

**Schema:** `argus.portfolio_autonomy_memory.v1`  
**Artifacts:** `runs/portfolio/autonomy_memory/latest.{json,md}` and timestamped copies.

## Purpose

Bounded autonomous sessions often stop early on **inspect**, **quiescence**, **no material change**, or **sparse lifecycle** signals. Individually those stops are correct; across many sessions they can look like “the runner never commits,” even when the environment is **stable but thin** rather than **newly risky**.

The autonomy memory layer is a **read-only, deterministic digest** of recent stamped session JSONs (and light context from runner heartbeat + escalation inbox). By itself it **does not** change behavior. The **autonomous runner** (`argus portfolio run-autonomous`) may consume it via **`evaluate_autonomous_confidence_adjustment`** for **small, bounded** optional effects: at most **+1** iteration on `max_cycles` and/or **one** deferred caution-class stop per session, only when escalation and recent failure history allow it (see [autonomous-runner.md](autonomous-runner.md)). Promotions, unsafe stops, and pipeline failures are never relaxed from this layer.

Use it to:

- Separate **chronic structural sparsity** from **one-off noise**
- See **repeated stop reasons** and **guardrail code families** side by side
- Notice **safe caution loops** vs **failure-class or review-class** stops
- Track **products repeatedly under lifecycle pressure** while stops stay “soft”

## Inputs

| Source | Role |
|--------|------|
| `runs/portfolio/autonomous_runner/<session_id>.json` | Primary: `stop_reason`, `stop_reason_codes`, `lifecycle_session_influence`, promotion fields |
| `runs/portfolio/runner_service/latest.json` (+ optional stamped `*__*.json`) | Service loop count, last autonomous stop, service stop reason (context only) |
| `runs/portfolio/escalation_inbox/latest.json` | Open-item product id tallies (attention, not causality) |
| `runs/products/signal_instrumentation/latest/<id>.json` | Optional: weak/sparse/missing coverage per product (see [product-signal-instrumentation.md](product-signal-instrumentation.md)) — used for **advisory** alignment notes only |

`latest.json` under autonomous runner is **not** double-counted; only distinct stamped filenames are scanned (up to `--limit-history`).

## CLI

```bash
argus portfolio autonomy-memory
argus portfolio autonomy-memory --json
argus portfolio autonomy-memory --no-save
argus portfolio autonomy-memory --limit-history 30
argus portfolio autonomy-memory --products-dir /path/to/products
```

- **`--json`** — print payload only (no Markdown summary to stdout except when not using `--json`).
- **`--no-save`** — compute and print without writing `runs/portfolio/autonomy_memory/`.
- **`--limit-history`** — max stamped autonomous session JSON files to consider (newest first by filename).
- **`--products-dir`** — recorded in payload `inputs` for provenance (memory aggregation is artifact-driven).

## Payload highlights

- **`session_window_summary`** — how many sessions loaded, newest/oldest ids, runner snapshots, escalation distinct product count.
- **`stop_reason_frequencies`**, **`lifecycle_signal_frequencies`** — sorted maps for stable diffs.
- **`repeated_stop_reasons`**, **`repeated_lifecycle_primary_signals`** — only values with count ≥ 2 in-window.
- **`repeated_sparse_or_mixed_conditions`** — sessions whose lifecycle primary was `mixed_sparse`.
- **`safe_but_stalled_products`** — product ids seen under lifecycle pressure lists on **≥2** sessions that also ended on **caution-class** stops (quiescence / no material change / max cycles).
- **`recurring_no_progress_sessions`** — session ids stopped on quiescence or no-material-change streak.
- **`recurring_no_promotion_sessions`** — sessions with promotion skipped, blocked opportunities, or failed promotion steps when opportunities existed.
- **`recurring_guardrail_stops_by_type`** — aggregated buckets from `stop_reason_codes` (quiescence, cycle_overall, intervention_heavy, etc.).
- **`confidence_accumulation_notes`** / **`confidence_adjustment_hints`** — descriptive hints when repetition suggests **stable** sparsity (not bravery recommendations).
- **`caution_persistence_notes`** — when failure-class stops repeat or promotions never land.
- **`sparse_signal_persistence`** — counts and narrative for chronic `mixed_sparse`.
- **`top_autonomy_lessons`** — short bullets for operators.
- **`runner_service_heartbeat`** — snapshot list for dashboards or audits.
- **`signal_instrumentation_advisory_notes`** — when repeated **inspect_specific_products**-class autonomous stops coincide with on-disk **instrumentation pressure** product ids, emits deterministic advisory lines (does **not** run `instrument-signals`).
- **`signal_instrumentation_context`** — **`artifacts_loaded_count`**, **`products_under_instrumentation_pressure`**, **`latest_dir_present`** — read-only bookkeeping.

## Markdown interpretation section

The generated Markdown includes three advisory subsections:

1. **What looks chronic** — repeated stops / recurring `mixed_sparse`.
2. **What looks safely repetitive** — caution-class stops dominating without failure-class stops in-window.
3. **What still looks genuinely risky** — unsafe session ids and caution notes tied to failures or review-class cycle overall stops.

## Autonomous confidence adjustment (runner integration)

**Schema:** `argus.portfolio_autonomous_confidence_adjustment.v1` (returned by `evaluate_autonomous_confidence_adjustment`, embedded under `confidence_adjustment` on autonomous session payloads).

Preconditions (all deterministic):

- Enough stamped sessions in-window (minimum 4).
- No `unsafe_to_continue` (or critical / serious blocked-promotion) **open** escalation items.
- No failure-class autonomous session ids aggregated in the current memory window for “unsafe” classification (`cycle_overall` only counts `request_human_review` and `repair_imports` as unsafe for this purpose — `inspect_specific_products` is treated as repeated caution, not escalation-grade risk).
- At least one **repeated low-risk** signal: e.g. repeated `inspect_specific_products` stops, repeated `mixed_sparse` lifecycle primary, recurring no-progress / no-promotion session lists, or repeated caution-only sessions.

When eligible, the runner may apply **at most one** deferral for inspect-class / no-material streak / quiescence-inspect, and/or extend the iteration budget by **+1**. Intervention-heavy, pipeline errors, and human-review / import-repair cycle overall stops are unchanged.

## Future work (not in this layer)

- Broader **bravery** or **action budget** policies (explicit product decision).
- Any automatic promotion expansion (out of scope).
