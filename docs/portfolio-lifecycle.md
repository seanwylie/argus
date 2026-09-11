# Portfolio lifecycle synthesis

Answers: **where each product sits** (creation → steady → wind-down) and **which transitions are visible** from local artifacts only.

## Command

```bash
argus portfolio lifecycle
argus portfolio lifecycle --json
argus portfolio lifecycle --no-save
argus portfolio lifecycle --products-dir /path/to/products
```

| Flag | Effect |
|------|--------|
| `--json` | Print `argus.portfolio_lifecycle.v1` JSON to stdout |
| `--no-save` | Evaluate only; do not write `runs/portfolio/lifecycle/*` |
| `--products-dir` | Alternate products root (same semantics as other portfolio commands) |

## Artifacts

Under `runs/portfolio/lifecycle/`:

- `latest.json`, `latest.md` — current synthesis
- `{run_id}.json`, `{run_id}.md` — stamped copies (same content as latest for that run)

## Inputs (read-only)

| Source | Role |
|--------|------|
| Product inventory (`lifecycle.stage` in `product.yaml`) | Baseline per product |
| `runs/products/creation/latest.json` | Proposals not yet scaffolded → **proposed** rows |
| `runs/products/creation_scaffold/*.json` | Successful scaffolds; newest per product by `run_id` |
| `runs/products/creation_bootstrap/*.json` | Successful bootstrap runs per product |
| `runs/products/signal_instrumentation/latest/<id>.json` | Optional signal coverage / observability readiness (see [product-signal-instrumentation.md](product-signal-instrumentation.md)) |
| `runs/products/signal_instrumentation_apply/latest/<id>.json` | Optional **worker apply** outcomes (`argus.product_signal_instrumentation_apply.v1`); refines “instrumentation pressure” vs latest scan (read-only; see [product-signal-instrumentation.md](product-signal-instrumentation.md)) |
| `runs/portfolio/outcomes/latest.json` | Trajectories (e.g. `mixed` → **mixed_or_unclear**; decline handling) |
| `runs/products/deprecation/latest.json` | Deprecation posture per product |
| `runs/products/deprecation_plan/*.json` | Plan posture when proposal row missing (falls back to plan) |
| Portfolio strategy (`load_latest_strategic_posture`) | Posture; with **retire**, adds negative-trajectory products to retirement pressure |

## Lifecycle lanes (`lifecycle_status`)

| Status | Meaning |
|--------|---------|
| `proposed` | Creation proposal exists; no successful scaffold artifact for that proposal |
| `incubating` | Stage `idea` / `build`, or scaffold without successful bootstrap |
| `active` | `validate` / `grow` / `maintain` without conflicting signals |
| `repairing` | Deprecation / plan posture `repair_instead` |
| `harvesting` | Posture `harvest`, or `decline` + positive trajectory |
| `retiring` | Posture `retire`, or stage `kill` (unless repair conflict) |
| `archived_candidate` | Posture `archive` |
| `mixed_or_unclear` | e.g. `kill` + `repair_instead`, or mixed outcomes trajectory |

Per row: **`reason_codes`** (sorted, deterministic), **`evidence`** (deprecation postures, scaffold/bootstrap flags, trajectory, optional **`signal_instrumentation`** when `runs/products/signal_instrumentation/latest/<id>.json` exists — includes optional **`latest_apply`** when an apply artifact exists).

## Payload (`argus.portfolio_lifecycle.v1`)

| Field | Description |
|-------|-------------|
| `per_product_lifecycle` | Rows: inventory products + unscaffolded proposals (`product_id` may be null) |
| `lifecycle_counts` | Counts for each lane in `LIFECYCLE_STATUSES` (zeros included) |
| `products_entering` | Incubating product ids and `proposal:<creation_proposal_id>` |
| `products_exiting` | Products in **retiring**, **harvesting**, or **archived_candidate** |
| `products_under_repair_pressure` | **repairing** lane |
| `products_under_retirement_pressure` | Harvest / retire / archived_candidate lanes, plus **negative trajectory** ids when strategy posture is **retire** |
| `products_under_instrumentation_pressure` | **Effective** pressure: ids that are weak/sparse/missing on the latest **scan** artifact, **excluding** ids where the latest **apply** artifact reports **`apply_status == success`** and **`post_apply.instrumentation_status == adequate`** |
| `products_under_instrumentation_pressure_raw` | Same weak/sparse/missing list **before** apply refinement |
| `products_instrumentation_resolved_via_apply` | Ids removed from effective pressure because of adequate **`post_apply`** on a successful apply |
| `products_instrumentation_apply_followup` | Still on raw pressure **and** apply is partial, or success without adequate **`post_apply`** |
| `instrumentation_pressure_refinement` | Full refinement dict from `refine_instrumentation_pressure_with_apply_context` (counts + lists) |
| `signal_instrumentation_latest_dir_present` | Whether `runs/products/signal_instrumentation/latest/` exists on disk |
| `signal_instrumentation_apply_latest_dir_present` | Whether `runs/products/signal_instrumentation_apply/latest/` exists on disk |
| `portfolio_lifecycle_summary` | Dict: `narrative`, `inventory_product_count`, … plus **`products_needing_signal_instrumentation_count`** (effective), **`products_instrumentation_raw_pressure_count`**, **`products_instrumentation_resolved_via_apply_count`**, **`products_instrumentation_apply_followup_count`**, **`signal_instrumentation_artifact_files`**, **`signal_instrumentation_apply_artifact_files`**, optional **`instrumentation_pressure_note`** |
| `recommended_lifecycle_attention` | `[{ "attention_rank", "note" }]` |
| `portfolio_strategy_posture` | From strategy artifact (or null) |
| `portfolio_strategy_loaded` | Whether strategy payload was present |
| `creation_proposals_loaded` | Whether creation proposals artifact matched schema |
| `deprecation_proposals_loaded` | Whether deprecation proposals artifact matched schema |
| `inputs` | `products_dir`, `valid_product_count`, `outcomes_artifact_present`, **`signal_instrumentation_artifacts_loaded`**, **`signal_instrumentation_apply_artifacts_loaded`**, `paths` to key dirs/files (includes **`signal_instrumentation_latest`**, **`signal_instrumentation_apply_latest`**) |

**Note:** Strategy posture appears once at **`portfolio_strategy_posture`** (not duplicated inside `portfolio_lifecycle_summary`).

## Operational notes

- Re-run after refreshing outcomes, deprecation proposals, or creation artifacts to refresh lanes.
