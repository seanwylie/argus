# Signals and adapters

In Argus, **everything becomes a signal first**. A **signal** is a normalized observation about a product or its environment, represented as a `SignalRecord` (`argus.core.models.signal`: type, source, timestamp, JSON payload, optional severity hint and confidence).

Signals are **not** findings yet: they are raw material for audit, planning, and decisions later (`signals` → `findings` → `decisions` → `actions`).

## Product manifest vs snapshot wiring

Do not confuse these:

| Concept | Schema / location | Role |
|--------|-------------------|------|
| **Product signal manifest** | `argus.product_signal_manifest.v1` in `products/<id>/signals.yaml` (or inline `signal_manifest` in `product.yaml`) | Declares **what** the product expects (ids, category, `source_type`, path/ref, `freshness_sla`, trust). Drives adapter enablement, manifest gap rows after collect, and canonical **category** / **SLA** / **trust** (see `argus.signals.normalize`). |
| **Snapshot / parser wiring** | Filename patterns in `argus/signals/snapshots/registry.py`, optional `config/adapters.json` | Binds **local files** to parsers and the adapter pipeline — **not** a substitute for the product manifest. |

## Adapters

An **adapter** implements `SignalAdapter` (`argus.signals.contract`):

- Declares its `SignalType` (e.g. `filesystem`, `metrics`).
- Is **enabled per product** when **`product.yaml`** lists the type under `signals:` (`enabled: true`) **or** an **enabled** row in the product signal manifest (`signals.yaml` or inline `signal_manifest`) declares that `source_type`.
- Implements `collect(ctx) → list[SignalRecord]`, reading **local** paths (typically under the product directory; **execution** reads `runs/execution/<product_id>/` under the repo root).

Built-in adapters live under `argus/signals/adapters/`:

| Adapter id | Signal type | Role |
|------------|-------------|------|
| `filesystem` | `filesystem` | Layout under `metrics/` (or `metrics.local_paths`): presence, mtimes, staleness |
| `metrics_file` | `metrics` | Parse `metrics/*.json` and `*.jsonl` |
| `cost_file` | `cost` | Read `metrics/cost.snapshot.json` or `config/cost.snapshot.json` |
| `analytics_file` | `analytics` | Read `metrics/analytics.snapshot.json` or `config/analytics.snapshot.json` |
| `heartbeat` | `health` | Activity / “heartbeat” from mtimes under `metrics/`, `app/`, `config/`, `scripts/` or `.argus-activity` |
| `execution` | `execution` | Execution outcomes from `runs/execution/<product_id>/*.json` (`execution_success`, `execution_failure`, `execution_duration`, `execution_error` in payload) |
| `temporal_snapshots` | `temporal` | Time-aware local snapshots: filenames must contain `temporal_market`, `temporal_news`, or `temporal_recency`; JSON/CSV under `metrics/snapshots/` or `runs/temporal/snapshots/products/<id>/` |

Enable with `- type: temporal` in `product.yaml` under `signals:`.

Writers (orchestrator, scripts) should drop JSON files such as:

```json
{
  "schema": "argus.execution_record.v1",
  "product_id": "myproduct",
  "success": true,
  "duration_seconds": 1.25,
  "error": null,
  "finished_at_utc": "2026-04-12T12:00:00+00:00"
}
```

Enable with `- type: execution` in `product.yaml` under `signals:`.

**Experiment linkage:** optional `experiment_id` and/or `action_id` in each JSON file. If only `action_id` is set, resolve the experiment via optional `runs/experiments/action_to_experiment.json` (`{"<action_id>": "<experiment_id>"}`). After `argus signals collect` (or `argus experiments apply-execution`), linked experiments update status: success advances `proposed`→`active`→`completed`; three consecutive failures → `failed`.

Register adapters on an `AdapterRegistry` (see `default_builtin_adapters()`). The **runner** (`collect_for_product`, `collect_inventory`) invokes every adapter that is both registered and enabled for the product, then **`reconcile_manifest_declarations`** appends explicit gap rows for enabled manifest declarations with no matching collected record (`collection_status` **`missing`**) or no registered adapter for that type (**`unsupported_source_type`**). This is **distinct** from the **local snapshot manifest** (`argus.local_snapshot_manifest.v1` under `metrics/snapshots/local/`), which only drives **`LocalSnapshotAdapter`** enumeration.

## Persistence

`argus signals collect` writes:

- `runs/signals/collections/<timestamp>_<product_id>.json` — history
- `runs/signals/latest/<product_id>.json` — last run (used by `argus signals show`)

Format: JSON with schema label `argus.signal_collection.v1` and a `records` array.

Bundles may include **`signal_continuity`** (`schema: argus.signal_continuity.v1`): a deterministic diff versus the previous `latest` file before overwrite—`appeared` / `disappeared` (by stable continuity key), **collection-recency bucket** regression (`realtime` → … → `stale`), and **window** checks (`overlap` when the current `source_window_start` parses to a time **before** the prior `source_window_end` (inconsistent ordering); `metadata_lost` when window fields existed in the prior row but not the current). First run sets `compared: false`. The same block is copied into `runs/temporal/latest/<product_id>.json` when the temporal sidecar is written.

Each record includes the legacy `SignalRecord` fields plus an optional **`canonical`** object (`schema: argus.canonical_signal.v1`) added at write time by `argus.signals.normalize`: stable fields such as `signal_id`, `product_id`, `category`, `value` / `value_type`, `source_type`, `source_ref`, `provenance`, `observed_at`, `collected_at`, window and freshness metadata, `trust_level`, and `collection_status` (including `ok` | `degraded` | `missing` | `error`, and manifest gap **`unsupported_source_type`** when applicable). Payload may also set `collection_status` explicitly (e.g. snapshot parsers). Bundles written before this field existed load unchanged (`canonical` absent).

## Reality classification (inspectable report)

`argus signals reality <product_id>` reads `runs/signals/latest/<product_id>.json`, the product’s **signal manifest** (when present), and optionally notes whether `runs/temporal/latest/<product_id>.json` exists. It writes a deterministic report to `runs/signals/reality/latest/<product_id>.json` (`schema: argus.signal_reality.v1`): per-record **reality_status** (`real`, `shallow_real`, `declared_missing`, `invalid`, `stale`, `deprecated`) with **reasons** (machine strings), plus **manifest_gaps** for enabled manifest entries with no matching collected row. Rules are conservative and structural (canonical recency bucket, collection_status, trust_level, manifest match); they do not interpret business outcomes.

## Business snapshot ingestion (local files)

Separate from `SignalAdapter.collect`, **snapshot ingesters** live under `argus/signals/snapshots/`. They read **local JSON or CSV** exports (PostHog-, GA-, AWS Cost Explorer–, Stripe-, and mobile-style shapes), resolve `product_id` from the file or path, and emit **`SignalRecord` with `signal_type: custom`** and `payload.business_signal` set to a normalized kind (`traffic_up`, `conversion_down`, `cost_spike`, etc.).

- Scan locations: `products/<id>/metrics/snapshots/*` first; then `fixtures/business_snapshots/<id>/` for the same product (skipped if a file with the same basename already exists under the product dir).
- CLI: `argus signals snapshot-types`, `argus signals ingest-snapshots [PRODUCT_ID]` (`--no-merge`, `--no-fixtures`, `--no-save`).

Live integrations can later **write the same JSON shape** to disk or call the same parse functions with bytes—no API code in Argus yet.

## Temporal snapshots (time-aware, local files only)

**Parsers** live under `argus/signals/snapshots/` (`temporal_market.py`, `temporal_news.py`, `temporal_recency.py`) and are registered in `argus/signals/snapshots/registry.py` by filename substring (same mechanism as GA/Stripe snapshots).

- **Signal type:** `temporal` (`SignalType.TEMPORAL`). Payload includes `source_timestamp_utc`, `fetched_at_utc`, `freshness`, and a `normalized` object (domain fields).
- **Binding:** `scope` + optional `product_id` in the file, or path conventions:
  - `runs/temporal/snapshots/global/` → synthetic product id `_global_`
  - `runs/temporal/snapshots/portfolio/` → `_portfolio_`
  - `runs/temporal/snapshots/products/<id>/` → that product
  - `products/<id>/metrics/snapshots/` → product from path
- **CLI:** `argus temporal adapters`, `argus temporal ingest [PRODUCT_ID]` — scans `runs/temporal/snapshots/**` and `fixtures/temporal/snapshots/**`, merges into `runs/signals/latest/<id>.json` per bucket. Per-product collection also runs via `argus signals collect` when `temporal` is enabled.

Example fixtures: `fixtures/temporal/snapshots/.../temporal_*_example.json`.

## Optional Cursor signal review (interpretation only)

**Not** part of deterministic collection. After `argus signals collect`, operators may run **`argus signals cursor-prompt --product-id <id>`** (prints a prompt grounded in `runs/signals/latest/<id>.json`), then paste Cursor output into a JSON file and run **`argus signals cursor-ingest --product-id <id> --file <path>`**. Argus validates **`argus.signal_cursor_review.v1`** and writes **`runs/signals/review/<id>.json`** (`argus.signal_review_bundle.v1`) without changing `runs/signals/latest/`. See **`docs/model-contracts.md`** (deterministic signals vs Cursor signal review).

## CLI

- `argus signals adapters` — list adapters (`--json` for machine-readable).
- `argus signals snapshot-types` — business snapshot adapter ids and filename patterns.
- `argus signals ingest-snapshots` — ingest local snapshot files → CUSTOM `SignalRecord`s; optional `PRODUCT_ID`.
- `argus signals collect` — all valid inventory products; optional `PRODUCT_ID` for one product.
- `argus signals show <product_id>` — last persisted bundle (human summary includes manifest/temporal hints; `--json` adds `operator`).
- `argus signals reality <product_id>` — classify latest bundle vs manifest + temporal presence → `runs/signals/reality/latest/<id>.json`.
- `argus signals cursor-prompt --product-id <id>` — print Cursor prompt for optional **`argus.signal_cursor_review.v1`** ingest (requires prior collect).
- `argus signals cursor-ingest --product-id <id> --file <path>` — merge validated review JSON into `runs/signals/review/<id>.json`.
- `argus temporal adapters` — temporal filename patterns + `temporal_snapshots` signal adapter.
- `argus temporal ingest [PRODUCT_ID]` — ingest repo-level temporal snapshot trees (`runs/temporal/snapshots`, `fixtures/temporal/snapshots`).

## Adding a new adapter

1. Subclass `SignalAdapter`, set `adapter_id` and `signal_type`.
2. Implement `collect(ctx: ProductSignalContext)` using `ctx.product_root` (absolute) and `ctx.product`.
3. Emit records with `new_signal_id()` (or stable ids in tests).
4. Register the class on an `AdapterRegistry` (or extend `default_builtin_adapters()`).
5. Ensure products list your signal type under `signals:` in `product.yaml`, **or** declare it in the product signal manifest so the runner enables the adapter.

Do not add business rules here—only observation and normalization.
