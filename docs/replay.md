# Forensic replay (read-only)

Replay commands **reconstruct** operator-visible state from **existing** JSON under `runs/` — they do **not** call eligibility evaluation, portfolio progression, or any mutating pipeline.

**Schemas:** `argus.orchestration_replay.v1` (product), `argus.portfolio_replay.v1` (portfolio).

## Product-level: `argus orchestration replay`

```bash
uv run argus orchestration replay --product-id <id>
uv run argus orchestration replay --product-id <id> --json
uv run argus orchestration replay --product-id <id> --no-save
```

**Writes (default):**

- `runs/orchestration/replay/<product_id>/<timestamp>.json` and `.md`
- `runs/orchestration/replay/<product_id>/latest.json` and `latest.md`

**What it reads (when present):**

| Role | Artifact path |
|------|-----------------|
| Product manifest | `products/<id>/product.yaml` |
| Signals | `runs/signals/latest/<id>.json` |
| Findings | `runs/findings/latest/<id>.json` |
| Decisions | `runs/decisions/latest/<id>.json` |
| Decision assessment | `runs/decision_assessment/latest/<id>.json` |
| Orchestration state (output of `orchestration state`) | `runs/orchestration/latest/<id>.json` |
| Operator snapshot | `runs/orchestration/operator_snapshot/<id>.json` |
| Task | `runs/orchestration/tasks/latest/<id>.json` |
| Advancement | `runs/orchestration/latest/advancements/<id>.json` |
| Progression run | `runs/orchestration/latest/progression_runs/<id>.json` |

The JSON payload classifies paths as **inputs** (upstream pipeline artifacts) vs **outputs** (orchestration-emitted artifacts), lists **reason codes** surfaced from persisted state/task, and includes a short **“why this state likely occurred”** narrative.

## Portfolio-level: `argus portfolio replay`

```bash
uv run argus portfolio replay
uv run argus portfolio replay --json
uv run argus portfolio replay --no-save
```

**Writes (default):**

- `runs/portfolio/replay/<timestamp>.json` and `.md`
- `runs/portfolio/replay/latest.json` and `latest.md`

**What it reads:**

- `runs/portfolio/cycle/latest.json` (if present) — full **cycle** bundle summary and per-stage status (including errors)
- `runs/portfolio/operator_queue/latest.json`
- For each product in the queue (top entries): `runs/orchestration/operator_snapshot/<id>.json` (as inputs)
- Latest `progression`, `quiescence`, `delta_report`, `intervention` under `runs/portfolio/*`

**Outputs** listed include the portfolio artifacts that those commands normally write (cycle, progression, quiescence, delta, intervention).

When a **cycle** artifact exists with `ok: false`, failed stage names appear under `failure_stages` and in the Markdown.

## See also

- [operator-snapshot.md](operator-snapshot.md)
- [portfolio-cycle.md](portfolio-cycle.md)
- [orchestrator README](../argus/orchestrator/README.md)
