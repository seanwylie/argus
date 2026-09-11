# Portfolio progression (`argus.portfolio_progression.v1`)

Bounded **multi-product** progression: for each of the top **N** products in the [operator queue](operator-queue.md), Argus runs **at most one** orchestration advancement step — the same primitive as **`argus orchestration advance`** (`advance_orchestration` in `argus/orchestrator/advancement.py`). There is **no** separate execution engine.

## CLI

```bash
uv run argus portfolio progress --limit 5
uv run argus portfolio progress --limit 3 --dry-run
uv run argus portfolio progress --no-execute          # record intent only (no in-process executor)
uv run argus portfolio progress --skip-import-failed --skip-waiting
uv run argus portfolio progress --json
uv run argus portfolio progress --no-save             # stdout only; no progression artifacts
```

Optional **`--products-dir`** matches other portfolio commands.

## Behavior

1. **Load operator queue** from `runs/portfolio/operator_queue/latest.json`, or **rebuild** it (same as `build_operator_queue_payload`) if missing.
2. Take the first **`--limit`** entries (by `queue_rank` order).
3. For each product:
   - **Evaluate** orchestration state (`evaluate_product_orchestration`).
   - Apply **guardrails** (optional):
     - **`--skip-import-failed`**: skip when `import_health` shows failed first-pass or `gating_tier == failed`.
     - **`--skip-waiting`**: skip when `orchestration_status` is `blocked_waiting_input` or `blocked_waiting_approval`.
   - **`--dry-run`**: compute `orchestration_advancement_payload` only — **no** writes under `runs/orchestration/` (no advancement file, no state refresh).
   - Otherwise: **`advance_orchestration(..., execute=not --no-execute)`** once — writes `runs/orchestration/latest/advancements/<id>.json` and refreshes state as today.

## Outcomes (per product)

| `outcome` | Meaning |
|-----------|---------|
| `advanced` | Execution succeeded, or advancement queued with `--no-execute` |
| `blocked_waiting` | Advancement blocked (waiting input) |
| `blocked_approval` | Advancement blocked (approval) |
| `no_action` | Skipped eligible list, dry-run preview, `queued_unhandled`, etc. |
| `failed` | In-process executor reported failure |
| `skipped` | Skipped by `--skip-*` guardrails |

## Outputs

| Path | Role |
|------|------|
| `runs/portfolio/progression/<timestamp>.json` | Full run payload |
| `runs/portfolio/progression/<timestamp>.md` | Markdown summary |
| `runs/portfolio/progression/latest.json` | Copy of last run (plain file) |
| `runs/portfolio/progression/latest.md` | Copy of last run (plain file) |

The JSON includes **`queue_slice`**, per-product **`next_action_before` / `next_action_after`**, advancement subset, and **`summary_counts`**.

## Implementation

- `argus/portfolio/progression.py`
- CLI: `argus portfolio progress` in `argus/cli/portfolio_cmd.py`

## Relation to single-product progression

- **`argus orchestration run-progression`** loops **multiple** advances **per product** until a cap.
- **`argus portfolio progress`** loops **one** advance **per product** across the **portfolio** (by queue rank).
