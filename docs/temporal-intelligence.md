# Temporal intelligence (operator guide)

This document ties together **how Argus represents “current reality”** versus **interpretation** and **downstream effects**. Canonical mechanics live in [temporal.md](temporal.md), [signals-and-adapters.md](signals-and-adapters.md), and [model-contracts.md](model-contracts.md).

## Four layers (do not conflate them)

| Layer | What it is | Source of truth? |
|-------|------------|-------------------|
| **1. Observations** | `SignalRecord` rows in `runs/signals/latest/` (`observed_at`, `payload`, …) | Yes — normalized facts Argus persisted. |
| **2. Collection recency** | `runs/temporal/latest/<product>.json` (`argus.temporal_bundle.v1`): per-row `freshness_score` / `freshness_bucket` from age vs a reference clock | Derived **deterministically** at collection time; not LLM output. |
| **3. Adapter / domain “temporal” blocks** | `payload.temporal` on snapshot-derived signals (`SignalType.TEMPORAL`) may include source-vs-fetch lag via `freshness_metadata()` | Facts from files; still not advisor opinion. |
| **4. Interpretation** | Findings rules (`argus.findings.rules.temporal`), decisions, advisors | **Not** ground truth for “what is happening now” — rules and models **interpret** (1–3). |

Malformed snapshot files under `metrics/snapshots/` are **skipped** with a **WARNING** log from `TemporalSnapshotSignalAdapter` (not a silent drop).

**Principle:** *Temporal data provides current (artifact-bound) reality. Advisors provide interpretation. Decisions consume both and annotate confidence when inputs are stale or missing.*

## Freshness models (two kinds)

1. **Collection recency** (`argus/temporal/recency.py`) — compares each signal’s `observed_at` to the bundle reference time; emits `freshness_score` (0–1) and buckets `realtime` | `recent` | `aging` | `stale` | `unknown`. Used in `runs/temporal/` and dashboard/doctor **worst bucket** rollups.

2. **Adapter payload freshness** (`argus/temporal/freshness.py`) — for snapshot parsers, compares **source timestamp** to **fetch time** inside the payload. Orthogonal to (1); see [temporal.md](temporal.md).

## Decision freshness gating (`argus.decision.freshness`)

For a **small set** of finding kinds (growth, cost risk, launch, retention, reliability), decisions attach:

- `freshness_inputs` — newest **operational** observations (metrics, analytics, cost, health), *not* `SignalType.TEMPORAL` snapshot rows.
- `freshness_warnings`, `stale_data_affected_confidence`, `freshness_recommend_gather_data`, `freshness_escalation` (stale inputs + launch-experiment intent).

Naming note: **`TemporalFreshnessContext`** here means “time-sensitive operational signals,” not the snapshot TEMPORAL enum.

## Autonomy

When `freshness_escalation` is set on a candidate (stale operational inputs for a gated finding + launch-experiment intent), autonomy may block autonomous execution of experiment-linked actions until human approval. See [autonomy.md](autonomy.md) and `argus.autonomy.constraints.freshness`.

## Advisors

`build_temporal_grounding` loads artifact timestamps, recent signal excerpts, and (when present) **`collection_recency`** from `runs/temporal/latest`. Prompts instruct the model **not** to invent live telemetry; stub mode encodes the same separation.

## Dashboard and doctor

- **Dashboard** — `temporal_visibility` per product + `temporal` aggregate block (`argus.dashboard_temporal.v2`).
- **Doctor** — `argus.doctor_temporal.v1`: missing signals, missing temporal sidecar, malformed temporal JSON, stale collection age, pipeline ordering, portfolio drift.

## Further reading

- [temporal.md](temporal.md) — CLI, persistence, bucket thresholds.
- [advisors.md](advisors.md) — prompts and consensus.
- [dashboard.md](dashboard.md) — UI surfaces.
- [idea-generation.md](idea-generation.md) — structured ideas (separate from temporal grounding, but both feed operator views).
