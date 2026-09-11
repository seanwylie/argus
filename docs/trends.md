# Trends and drift analysis

The **`argus trends`** commands analyze **historical portfolio snapshots** under `runs/history/` (see [history.md](history.md)). They do **not** call external services or use ML; all rules live in `argus/trends/rules.py`.

## Inputs

- One **time-ordered series** of `ProductSnapshot` rows per product (oldest → newest), loaded from stored `snapshot.json` files.
- Minimum **two** snapshots per product for non-trivial output; otherwise `insufficient_data`.

## Metrics computed

For each product window, `metrics` includes (among others):

| Field | Meaning |
|-------|--------|
| `findings_net_delta` | Last minus first active finding count |
| `findings_increase_steps` / `findings_decrease_steps` | Consecutive up/down steps |
| `priority_delta`, `confidence_delta`, `cost_delta` | First vs last where values exist |
| `action_changes`, `distinct_top_actions` | Top recommendation churn |
| `lifecycle_stage_changes` | Number of stage transitions |
| `escalation_nonzero_snapshots` | Snapshots with at least one escalation packet counted |
| `signal_freshness_trend` | `stale` if `last_signal_at` is identical across snapshots, else `updating` |

## Drift signals (examples)

Explicit patterns include:

- Rising findings (consecutive increases or large net increase)
- Repeated deprecate/hold/pause themes in the top recommendation text
- Cost up more than 10% without growth-oriented wording in the top action
- Frequent top-action changes (thrashing)
- Escalations in multiple snapshots or accumulated volume
- Stagnation: unchanged findings, lifecycle stage, and `last_signal_at` between steps
- Validate stage for the entire window (four or more snapshots)
- Stale signals: `last_signal_at` not advancing across three or more snapshots

## Trend flags

Flags are strings such as: `improving`, `stable`, `drifting`, `stagnating`, `risk_increasing`, `action_thrashing`, `likely_abandon`, `ready_for_scale_review`, `insufficient_data`. Multiple flags may apply.

**Confidence** is a deterministic score in `[0, 1]` based on window length, drift count, and flag count.

## CLI and artifacts

| Command | Behavior |
|---------|----------|
| `argus trends analyze [PRODUCT_ID]` | All products with history, or one product; prints analysis |
| `argus trends summary` | One compact line per product |
| `argus trends drift` | Only products with at least one drift signal (`--json` filters the same way) |

Each run writes:

- `runs/trends/latest.json` — full payload (`schema`: `argus.trends_run.v1`)
- `runs/trends/latest.txt` — human-readable mirror

## Consumption

Downstream tools should read `latest.json`, join on `product_id`, and treat `drift_signals` + `trend_flags` as **advisory** inputs for prioritization, not automatic execution.
