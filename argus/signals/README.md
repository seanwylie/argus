# argus.signals

Framework for **collecting** observations and normalizing them into `SignalRecord` objects.

## Two different “manifest” concepts

1. **Product signal manifest** (`argus.product_signal_manifest.v1`) — declared expected signals per product in `products/<id>/signals.yaml` or inline `product.yaml` (`signal_manifest`). Loaded into `ProductNode.signal_manifest`. Used for adapter **enablement** (`SignalAdapter.is_enabled_in_product`), **gap placeholders** after collect (`manifest_collect.reconcile_manifest_declarations`), and **canonical labeling** (`normalize.attach_canonical_to_records`: category, trust, SLA precedence).

2. **Local snapshot / adapter wiring** — which parser runs for which filename patterns (`argus/signals/snapshots/registry.py`) and optional **`config/adapters.json`** pipeline. This is **not** the product declaration file above; it does not carry `SignalManifestCategory` or product policy fields.

- **Contract:** `contract.py` — `SignalAdapter`, `ProductSignalContext`
- **Category / SLA resolution:** `category_mapping.py`, `normalize.py` — explicit `category_source` and `freshness_sla_source` on canonical provenance
- **Registry:** `registry.py` — `AdapterRegistry`
- **Runner:** `runner.py` — `collect_for_product`, `collect_inventory`
- **Persistence:** `persistence.py` — JSON under `runs/signals/`; each save may include **`signal_continuity`** (`argus.signal_continuity.v1`) comparing this run to the previous `latest` (appeared / disappeared / freshness bucket regression / window overlap or lost window metadata). Timestamped history under `runs/signals/collections/` is **trimmed automatically** after each save (see `argus/runs_retention.py`, env `ARGUS_SIGNALS_COLLECTIONS_KEEP_PER_PRODUCT`, CLI `argus signals prune-collections`).
- **Reality classification:** `reality.py` — deterministic `real` / `shallow_real` / `declared_missing` / `invalid` / `stale` / `deprecated` report from manifest + canonical + recency; CLI `argus signals reality <id>` writes `runs/signals/reality/latest/<id>.json` (`argus.signal_reality.v1`)
- **Continuity:** `continuity.py` — deterministic diff helpers (also mirrored under `runs/temporal/latest` when temporal sidecars are written)
- **Adapters:** `adapters/` — filesystem, metrics file, cost file, analytics file, heartbeat, execution, **temporal_snapshots** (local `*temporal_*.json|csv`)
- **Temporal helpers:** `argus/temporal/` — freshness metadata and normalized `SignalRecord` builders used by snapshot parsers

See [Signals and adapters](../../docs/signals-and-adapters.md) for concepts and how to extend.
