# adapters

**Intent:** Layered **collect → normalize → to_signal_records** pipeline over `ProductSignalContext`, registered in `argus/adapters/registry.py`.

Built-in integration adapters live under `argus/adapters/builtins/` (execution, filesystem, metrics, **temporal**) and delegate to `argus.signals.adapters` where applicable. See `docs/adapters.md` and `docs/signals-and-adapters.md`.

**Temporal:** `TemporalIntegrationAdapter` (`adapter_id: temporal`, category `temporal`) wraps `TemporalSnapshotSignalAdapter` for local `*temporal_*.json|csv` snapshots under each product’s `metrics/snapshots/` and `runs/temporal/snapshots/products/<id>/`.
