# Adapter layer (`argus.adapters`)

## What adapters are

The **adapter layer** is an explicit integration boundary for **local** data that eventually becomes [`SignalRecord`](argus-object-model.md) data. Each adapter implements a three-step pipeline:

1. **`collect(ctx)`** — Read raw material from the repo (paths, JSON, structured dicts).
2. **`normalize(raw)`** — Canonicalize for stable output (e.g. deterministic sort).
3. **`to_signal_records(normalized, ctx)`** — Emit validated `SignalRecord` instances.

Built-in adapters **wrap** the existing [`argus.signals.adapters`](../argus/signals/adapters/) implementations so behavior stays in one place; the layer adds structure and a registry without duplicating collection logic. Records produced through this pipeline are tagged with **`adapter_layer`** (in addition to any tags from the inner adapter).

## Registry and categories

Adapters register under an **id** and a **category** (`execution`, `filesystem`, `metrics`, reserved `api` for future use). See `argus/adapters/registry.py` and `argus/adapters/builtins/`.

## Configuration

Optional file: **`config/adapters.json`** (repo root).

Default shape:

```json
{
  "schema": "argus.adapters_config.v1",
  "enabled_ids": ["execution", "filesystem", "metrics"],
  "signals_collect": { "merge_adapter_layer": false }
}
```

- **`enabled_ids`** — Which adapter layer pipelines run when the layer is invoked with config-driven loading (`load_adapters_for_repo`).
- **`signals_collect.merge_adapter_layer`** — When `true`, `argus signals collect` appends adapter-layer records after the classic signal adapters (same as CLI `--merge-adapter-layer`).

## CLI

```bash
uv run argus adapters list
uv run argus adapters list --json
uv run argus adapters run execution --product-id MYPRODUCT
uv run argus adapters run all --product-id MYPRODUCT --json
```

Classic collection with optional merge:

```bash
uv run argus signals collect --product-id MYPRODUCT --merge-adapter-layer
```

## How this integrates with signals

1. **Classic path (default)** — `argus signals collect` uses `AdapterRegistry` and `collect_for_product` / `collect_inventory` from `argus.signals.runner` **without** the adapter layer.
2. **Merged path** — With `--merge-adapter-layer` or `config/adapters.json` → `signals_collect.merge_adapter_layer`, the runner **appends** `run_adapter_layer()` output after classic adapters. Layer records may overlap classic output for the same sources; use this for diagnostics or gradual migration.

Loader helpers: `argus.adapters.loader.load_adapter_config`, `instantiate_adapters`, `load_adapters_for_repo`. Pipeline entry: `argus.adapters.pipeline.run_adapter_layer`.

## How to add an adapter

1. Subclass `argus.adapters.base.Adapter` and implement `category`, `collect`, `normalize`, and `to_signal_records`.
2. Call `register_adapter_class(adapter_id, category, YourAdapter)` at import time (or register from a package `__init__` that imports your module).
3. Add the id to **`enabled_ids`** in `config/adapters.json` if it should run in the default config-driven set.
4. Prefer delegating to an existing `SignalAdapter` via `SignalAdapterWrapper` if the behavior already exists under `argus.signals.adapters`.

Keep implementations **deterministic** (no network I/O in core adapters) and validate emitted records the same way the runner does (`validate_signal_record`).
