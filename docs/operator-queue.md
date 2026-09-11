# Portfolio operator queue (`argus.operator_queue.v1`)

The operator queue answers: **which product should I touch next?** across the whole portfolio. It ranks valid products by a **deterministic, inspectable score** derived primarily from per-product [operator snapshots](operator-snapshot.md).

## Outputs

| Path | Description |
|------|-------------|
| `runs/portfolio/operator_queue/latest.json` | Machine-readable queue |
| `runs/portfolio/operator_queue/latest.md` | Markdown table + full recommendations |

## CLI

```bash
uv run argus portfolio operator-queue
uv run argus portfolio operator-queue --json
uv run argus portfolio operator-queue --no-save    # stdout only; no files
```

Optional `--products-dir` follows the same convention as other portfolio commands.

## Inputs (per product)

1. **`runs/orchestration/operator_snapshot/<product_id>.json`** when present (`argus.operator_snapshot.v1`).
2. Else **`runs/orchestration/latest/<product_id>.json`** — converted with `build_operator_snapshot()` (same facts as a snapshot, without requiring the snapshot file to exist).
3. Else a **fallback** row: score includes `no_operator_artifact` and the recommendation tells you to run `argus orchestration state --product-id <id>`.

## Scoring (weights v1)

Higher **`priority_score`** ⇒ **attend sooner**. The JSON payload includes a full **`weight_ledger`** with every constant used; the module mirrors these in `argus/portfolio/operator_queue.py` (prefix `POINTS_`, `TIER_POINTS`, `DEBT_SCALE`, etc.).

Summary of factors:

| Factor | Role |
|--------|------|
| **readiness_tier** | Coarse ladder (`unprofiled` … `advance_ready`) — higher weight on gaps than on “ready”. |
| **understanding_debt** | `DEBT_SCALE` × debt in `[0, 1]`. |
| **first_pass_status** | From `import_health` (`failed` / `partial` / …). |
| **Artifact freshness** | Stale/absent signals bundles; eligibility flags (`signals_collection_time_stale`, `signals_refresh_needed`, `temporal_freshness_stale`). |
| **Waiting / blocking** | Non-empty `waiting_inputs`; `orchestration_status` containing `blocked_waiting`; non-empty `blockers`. |
| **Decision confidence** | Penalty when top confidence is below `0.45` or missing. |
| **next_action_policy.action_family** | From snapshot (`refinement`, `observability`, `generation_chain`, …). |
| **Lifecycle** | Small nudge from `product.yaml` `lifecycle.stage` (`idea`, `build`, `grow`, …). |
| **No snapshot/orch** | Fixed `POINTS_NO_OPERATOR_INPUT` so empty trees are surfaced. |

**Tie-break:** higher `priority_score` first; then **ascending `product_id`**.

## Entry fields

Each `entries[]` object includes:

- `queue_rank` (1 = first)
- `priority_score`, `priority_score_breakdown`, `priority_reason`
- `next_action`, `readiness_tier`, `understanding_debt`, `orchestration_status`, `recommendation`
- `operator_view_source`: `snapshot_file` | `orchestration_latest` | `none`

## Implementation

- `argus/portfolio/operator_queue.py`
- Not wired into `argus portfolio refresh` — run **`argus portfolio operator-queue`** when you want an updated queue.

**See also:** [portfolio-progression.md](portfolio-progression.md) — one bounded `advance_orchestration` step per top-N queued product (`argus portfolio progress`).
