# Portfolio quiescence

**CLI:** `argus portfolio quiescence` (`--json`, `--no-save`)

**Schema:** `argus.portfolio_quiescence.v1`

**Artifacts:** `runs/portfolio/quiescence/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}` (same pattern as portfolio progression).

## Purpose

Answer: **Should I run another portfolio pass now, and what changed enough to matter?**

The evaluator compares the current **operator queue**, **per-product operator snapshots**, and the **latest portfolio progression** run against a **baseline** stored in the previous quiescence output (`baseline_for_next_run` in `latest.json`). It also compares the **two newest timestamped** progression JSON files under `runs/portfolio/progression/` (excluding reliance on `latest.json` alone) to detect **stuck / repeating** blocked outcomes.

If there is **no prior** `runs/portfolio/quiescence/latest.json`, the baseline is empty: recommendation defaults to **`run_again`** so the first evaluation establishes fingerprints for the next run.

## Inputs

| Source | Role |
|--------|------|
| `runs/portfolio/operator_queue/latest.json` | Ranked queue entries (and merge keys into fingerprints) |
| `runs/orchestration/operator_snapshot/<id>.json` | Per-product readiness, import health, waiting/blocking, confidence, `next_action` |
| Prior `runs/portfolio/quiescence/latest.json` | `baseline_for_next_run.per_product` + `queue_product_order` |
| `runs/portfolio/progression/<stamp>.json` | Two newest timestamped files for stuck detection |
| `runs/portfolio/progression/latest.json` | Metadata only in `inputs` (progression run id / time) |

## Material change (grounded)

A product is **materially changed** when any of these differ meaningfully between baseline and current fingerprints:

- **Discrete:** `readiness_tier`, `next_action`, `orchestration_status`, `first_pass_status`, `gating_tier`
- **Counts:** `waiting_inputs_count`, `blockers_count` (any delta)
- **Numeric:** `understanding_debt` (absolute delta ≥ `DEBT_DELTA_MATERIAL`, default `0.08`, or null↔value)
- **Confidence:** `top_decision_confidence` (absolute delta ≥ `CONFIDENCE_DELTA_MATERIAL`, default `0.10`, or null↔value)
- **Rank / score:** `queue_rank` (absolute shift ≥ `RANK_SHIFT_MATERIAL`, default `2`), `priority_score` (absolute delta ≥ `SCORE_DELTA_MATERIAL`, default `15.0`)
- **New** product id appears in the queue vs baseline → `quiescence.material_change.new_product_in_queue`

Thresholds are echoed under `thresholds` in the JSON output.

## Outputs (key fields)

| Field | Meaning |
|-------|---------|
| `portfolio_quiescent` | `true` only when there is a prior baseline, no material changes, no stuck progression rows, recommendation is **`wait`**, and the operator queue is present |
| `quiescence_reason_codes` | Sorted union of codes (e.g. `quiescence.no_prior_baseline`, material codes, `quiescence.progression_stuck_repeat`, `quiescence.missing_operator_queue`, `quiescence.missing_operator_snapshots`) |
| `products_with_material_change` | `{ product_id, codes[] }` |
| `products_newly_actionable` | Unblocked with a real `next_action` vs prior blocked/waiting posture |
| `products_still_blocked` | Blocked in both baseline and current |
| `products_stuck_or_repeating` | Same `blocked_waiting` / `blocked_approval` outcome in two consecutive timestamped progression runs |
| `queue_rank_changes` | Prior list order vs current `queue_rank` (non-zero deltas) |
| `recommendation` | See below |

## Recommendations

| Value | When |
|-------|------|
| `run_again` | No prior baseline; newly actionable products; any material change; queue rank / score shifts driving re-run |
| `wait` | Prior baseline exists and nothing material changed; no stuck repeat |
| `inspect` | Operator queue missing (with prior baseline); or missing operator snapshot files while baseline exists |
| `import_refresh` | Material codes touch import / gating / first-pass fields (or collected import-related codes) |
| `human_review` | Stuck / repeating blocked progression outcomes |

## Operator workflow

1. Run `argus portfolio operator-queue` and/or `argus portfolio progress` as usual.
2. Run `argus portfolio quiescence` to persist `latest.json` / `latest.md` and read the recommendation.
3. On the next run, the tool compares against the **previous** `latest.json` baseline automatically.

Use **`--no-save`** to evaluate without writing artifacts; **`--json`** prints the full payload to stdout.

## Related

- **[portfolio-delta-report.md](portfolio-delta-report.md)** — narrative “what changed” report across the portfolio (`argus portfolio delta-report`); reuses the same fingerprints and material thresholds.
