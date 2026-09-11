# Temporal signals

Argus **signals** (`SignalRecord`, persisted under `runs/signals/`) describe *what was observed*. **Temporal signals** add a deterministic view of **how current** those observations are at the time the bundle is evaluated—without changing the canonical signal payload or requiring advisors or live network calls.

## Concepts

| Term | Meaning |
|------|--------|
| **SignalRecord** | Canonical normalized row: `id`, `product_id`, `signal_type`, `source`, `observed_at`, `payload`, etc. |
| **TemporalSignal** | Same facts plus **`fetched_at`** / **`collected_at`** (ingest time), legacy **buckets** (`freshness_score`, `freshness_bucket` from `observed_at` vs reference), and **freshness sidecar** fields (`freshness_age`, `freshness_status`, optional window bounds and SLA) from `argus/temporal/freshness_compute.py`. |
| **Adapter `freshness`** | For `SignalType.TEMPORAL` snapshots, ``payload.temporal.freshness`` may include ``freshness_metadata()`` (source vs fetch lag). That is **orthogonal** to collection recency below. |

Temporal **collection** data is **derived** from any `SignalRecord` list. Advisors are **not** the source of truth for “current events.”

## How it differs from trends

- **Trends** (`argus trends`) compare **portfolio snapshots over time** (history under `runs/history/`).
- **Temporal** evaluates **per-signal observation timestamps** inside the latest collection to classify recency **now** (or at `collected_at_utc` when the bundle was written).

## Collection recency (runs/temporal/)

All rules are **fixed and documented in code** (`argus/temporal/recency.py`):

- **Age** = reference time − `observed_at` (non-negative; UTC-normalized).
- **Score**: linear decay from 1.0 at age 0 to 0.0 at 30 days (`SCORE_MAX_AGE_S`).
- **Buckets** (by age only):
  - `realtime` — ≤ 1 hour  
  - `recent` — ≤ 24 hours  
  - `aging` — ≤ 7 days  
  - `stale` — \> 7 days  
  - `unknown` — reserved for invalid timestamps (should not occur for valid `SignalRecord`).

`fetched_at` is set to the signal bundle’s **`collected_at_utc`** so operators can see both “when the fact was observed” and “when Argus last ingested this snapshot.”

## Freshness sidecar (age / continuity only)

Optional fields on **`SignalRecord.payload`** (either top-level or under **`temporal_sidecar`**) are read deterministically when building `runs/temporal/` rows:

| Field | Role |
|-------|------|
| `observed_at` | On `SignalRecord` (canonical observation time). |
| `collected_at` | On each temporal row: same instant as `fetched_at` / bundle `collected_at_utc`. |
| `source_window_start` / `source_window_end` | ISO8601 bounds; **end** anchors freshness age when present (otherwise age uses `observed_at`). Non-overlapping span is reported as `source_window_span_seconds` only. |
| `freshness_sla` | Positive seconds; if set, tiers are **25% / 75% / 100%** of SLA for `fresh` / `aging` / `stale`, else `expired`. |
| `freshness_age` | Seconds from anchor to reference. |
| `freshness_status` | One of: `fresh`, `aging`, `stale`, `expired`, `unknown` (no business meaning). |

Without SLA, fixed tiers match `recency.py` cutoffs (24h / 7d / 30d). `payload["temporal"]` remains for **interpretation** rules (findings); **do not** mix semantics into the sidecar.

Bundles include **`worst_freshness_status`** (stalest row) alongside legacy **`worst_freshness_bucket`**.

## Persistence

When `runs/signals/latest/<product_id>.json` is written (via `save_collection`), a matching temporal bundle is written:

- `runs/temporal/latest/<product_id>.json`
- `runs/temporal/collections/<timestamp>_<product_id>.json`

Schema: `argus.temporal_bundle.v1` with `worst_freshness_status` and a `signals` array of `argus.temporal_signal.v1` objects (additive fields on each signal row).

## Signal history continuity (same write as temporal)

Each `save_collection` also embeds **`signal_continuity`** (`schema: argus.signal_continuity.v1`) on the **signal** bundle under `runs/signals/latest/<product_id>.json`. It is a deterministic diff versus the previous `latest` before overwrite: **appeared** / **disappeared** (by stable continuity key), **freshness_regressed** (collection-recency bucket moved toward stale), and **window_continuity_broken** (overlapping windows or window metadata dropped). The same object is **copied** onto `runs/temporal/latest/<product_id>.json` when the temporal sidecar is written (`argus/signals/continuity.py`). This is not forecasting—structural comparison only.

`argus temporal summary` writes `runs/temporal/summary.json` (aggregate over products).

## CLI

```bash
argus temporal show <product_id>       # Latest temporal bundle (table or --json)
argus temporal freshness <product_id>    # Bucket counts + mean score
argus temporal summary                   # All products; writes summary.json
argus temporal refresh <product_id>     # Recompute from runs/signals/latest without collecting
```

## Compatibility

- **SignalRecord** shape and validation are unchanged.
- Existing adapters and collectors still emit only `SignalRecord`; temporal enrichment happens at **persistence** time (and via `temporal refresh`).
- If temporal persistence fails, signal collection **still succeeds** (best-effort sidecar).

See **[temporal-intelligence.md](temporal-intelligence.md)** for how this layer relates to findings, decisions, advisors, and autonomy.
