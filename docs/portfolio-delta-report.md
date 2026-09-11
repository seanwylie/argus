# Portfolio delta report (`argus.portfolio_delta_report.v1`)

**Command:** `argus portfolio delta-report`  
**Purpose:** One operator-friendly artifact that answers: *what changed across the last portfolio cycle, and why does it matter?*

The report compares **current** operator queue rows + per-product fingerprints (from `runs/orchestration/operator_snapshot/<product_id>.json` when present) against a **prior baseline**:

1. **`runs/portfolio/delta_report/latest.json`** → `baseline_for_next_run.per_product` (self‑chaining across runs), else  
2. **`runs/portfolio/quiescence/latest.json`** → `baseline_for_next_run` (same fingerprint shape as quiescence), else  
3. **No baseline** — first run emits `delta_reason_codes` including `delta_report.no_prior_baseline` and recommendation `establish_baseline`.

Portfolio **progression** (`runs/portfolio/progression/latest.json`) is summarized for headline context; **prior stamped** progression file (second‑newest `YYYYMMDDTHHMMSSZ.json` in that directory, when present) is referenced for run lineage only.

## Determinism and thresholds

Material changes reuse **the same** numeric thresholds as portfolio quiescence (see `argus/portfolio/quiescence.py` and [portfolio-quiescence.md](portfolio-quiescence.md)):

| Constant | Role |
|----------|------|
| `DEBT_DELTA_MATERIAL` | Understanding debt swing |
| `CONFIDENCE_DELTA_MATERIAL` | Top decision confidence swing |
| `RANK_SHIFT_MATERIAL` | Queue rank movement |
| `SCORE_DELTA_MATERIAL` | Priority score swing |

Reason codes from material comparison align with `quiescence.material_change.*` where applicable.

## Outputs

| Path | Content |
|------|---------|
| `runs/portfolio/delta_report/latest.json` | Full payload (`schema: argus.portfolio_delta_report.v1`) |
| `runs/portfolio/delta_report/latest.md` | Operator-friendly Markdown |
| `runs/portfolio/delta_report/<run_id>.json` | Timestamped copy |
| `runs/portfolio/delta_report/<run_id>.md` | Timestamped Markdown |

## CLI

```bash
uv run argus portfolio delta-report
uv run argus portfolio delta-report --json
uv run argus portfolio delta-report --no-save
```

- **`--json`** — print JSON to stdout (still writes artifacts unless `--no-save`).  
- **`--no-save`** — evaluate only; no files under `runs/portfolio/delta_report/`.

## Payload shape (high level)

- **`baseline`** — `source` (`delta_report_prior` \| `quiescence_baseline` \| `none`), `evaluated_at_utc`, `had_per_product`.  
- **`delta_reason_codes`** — e.g. `delta_report.no_prior_baseline`.  
- **`products_advanced_this_pass`** — product ids with `outcome == "advanced"` in latest progression.  
- **`products_newly_actionable`** / **`products_newly_blocked`** — from orchestration status + `next_action` (same semantics as quiescence).  
- **`readiness_tier_changed`**, **`next_action_changed`**, **`priority_score_changed_materially`**, **`decision_confidence_changed_materially`**, **`queue_movement`**, **`import_health_changed`**.  
- **`what_improved`** / **`what_regressed`** / **`what_stayed_stuck`** — concise buckets for operators.  
- **`recommended_next_portfolio_action`** — deterministic hint (e.g. `inspect_import_health`, `run_progression_or_queue`, `continue_monitoring`).  
- **`baseline_for_next_run`** — fingerprints + queue order for the next delta pass.

## Related

- [portfolio-quiescence.md](portfolio-quiescence.md) — quiescence evaluation and baselines.  
- [operator-queue.md](operator-queue.md) — queue scoring.  
- Orchestration operator snapshots: `argus/orchestrator/operator_snapshot.py`.
