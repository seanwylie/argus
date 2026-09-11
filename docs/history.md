# Portfolio history and snapshots

Argus normally reads **latest** artifacts under `runs/signals/latest/`, `runs/findings/latest/`, `runs/decisions/latest/`, and escalation listings. The **history** subsystem adds **timestamped portfolio snapshots** so you can compare state over time without a database.

## What is a generation / snapshot?

A **portfolio snapshot** is a single JSON document capturing, for every valid product in inventory at capture time:

- Identity and coarse state (`state`, `status`, `lifecycle_stage`)
- Cost (`monthly_cost_usd`)
- Signal freshness (`last_signal_at`)
- Findings counts and severity histogram
- Top decision candidate summary (`top_recommended_action`, `priority_score`, `top_confidence`)
- Lifecycle scores and `kill_candidate`
- Escalation count (from packets in `runs/escalations/latest/`)
- **Source paths** pointing at the `product.yaml` and latest artifact files used to build the row

Snapshots are **read-only aggregates**: they do not run the pipeline; they reflect whatever `latest` files exist when you run `argus history snapshot`.

## Storage layout

All history is under **`runs/history/`** (filesystem only, no network).

| Path | Purpose |
|------|--------|
| `runs/history/snapshots/<snapshot_id>/snapshot.json` | One portfolio snapshot file |
| `runs/history/latest.json` | Pointer to the most recently written snapshot (id, repo-relative path, `observed_at_utc`, optional `label`) |

**Snapshot id** is usually `YYYYMMDDTHHMMSSZ` (UTC). With `--label nightly`, the id becomes `<timestamp>_nightly` (label sanitized to safe characters).

You may copy snapshots elsewhere with `--out`; the canonical pointer is still updated from the primary write under `runs/history/snapshots/`.

## CLI

| Command | Description |
|---------|-------------|
| `argus history snapshot` | Capture current local state into a new snapshot + update `latest.json` |
| `argus history snapshot --label nightly` | Same, with a label suffix on the snapshot id |
| `argus history snapshot --out PATH` | Also write the JSON to a file (or `PATH/<id>.json` if `PATH` is a directory) |
| `argus history diff A B` | Load two snapshot files (paths or snapshot ids under `snapshots/`) and print per-product deltas. Snapshots are ordered by `observed_at_utc` so the delta is always **from earlier → later**. |
| `argus history product <product_id>` | Text (or `--json`) timeline of that product across stored snapshots |

**Delta** fields include: finding count change, cost change, top action changed, lifecycle stage change, escalation count change, confidence delta, `kill_candidate` changes, and `last_signal_at` changed.

## How other systems should consume snapshots

1. **Read JSON as the contract**: schema field `argus.portfolio_snapshot.v1` on each snapshot file; `latest.json` is `argus.history.latest_manifest.v1`.
2. **Prefer snapshot files over re-scanning `latest/`** when you need a **point-in-time** view that will not change if the operator refreshes pipelines.
3. **Join across time** by `snapshot_id` or `observed_at_utc`; per-product key is `product_id`.
4. **Do not assume** every product has findings/signals/decisions; counts and paths may be empty or missing files on disk at capture time.
5. For automation, run `argus history snapshot` after **`argus portfolio refresh`** (or your own pipeline) so `latest` artifacts are stable before capture.

## Temporal analysis

The snapshot model supports:

- Product state / lifecycle changes across captures  
- Finding counts and severity mix  
- Signal freshness (`last_signal_at`)  
- Cost trend (monthly field)  
- Decision drift (top action, confidence, lifecycle scores)  
- Escalation recurrence (count of packets in `latest/` at capture time)  

Downstream analysis can load `snapshot.json` files in order, or use `argus history diff` for ad-hoc comparisons.
