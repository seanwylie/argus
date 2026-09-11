# Portfolio intervention (`argus.portfolio_intervention.v1`)

**CLI:** `argus portfolio intervention` (`--json`, `--no-save`)

**Artifacts:** `runs/portfolio/intervention/latest.{json,md}` plus timestamped `YYYYMMDDTHHMMSSZ.{json,md}` (same pattern as portfolio progression and quiescence).

## Purpose

Answer: **Which products need intervention instead of another routine pass?**

The evaluator is **deterministic** and reads only **local artifacts** already produced by the operator loop: operator queue, per-product operator snapshots, stamped portfolio progression runs, stamped delta reports (readiness / debt history), and quiescence output when present. It does not call models or subprocesses.

Use it after **`argus portfolio operator-queue`**, **`argus portfolio progress`**, **`argus portfolio delta-report`**, and **`argus portfolio quiescence`** so fingerprints and progression history are populated.

## Inputs

| Source | Role |
|--------|------|
| `runs/portfolio/operator_queue/latest.json` | Queue rank, score, merged `next_action`, blocking / waiting hints |
| `runs/orchestration/operator_snapshot/<id>.json` | Readiness tier, understanding debt, import health, gating, confidence |
| `runs/portfolio/progression/<stamp>.json` | Repeated outcomes, `next_action_after`, blocked / advance results across runs |
| `runs/portfolio/delta_report/<stamp>.json` | Tier + debt trajectories vs prior baselines (stagnation) |
| `runs/portfolio/quiescence/latest.json` | Optional: `products_stuck_or_repeating` cross-check |

## Detection patterns (summary)

Patterns are implemented in `argus/portfolio/intervention.py` and emit **`detection_reason_codes`** (stable string identifiers). Examples:

- Repeated **`blocked_waiting_input`** / **`blocked_waiting_approval`** across recent progression runs
- Same **`next_action_after`** (or oscillation between a small set) with no readiness gain
- Same **readiness tier** with **equal-or-worse understanding debt** across consecutive delta baselines (material thresholds align with quiescence / delta report)
- Repeated **partial** or **failed** import signals without recovery
- **Oscillation** between two values for `next_action_after` over recent runs
- **Low** top-decision confidence while the product is not **advance-ready**, without material artifact improvement (queue / progression context)
- **High queue prominence** (top ranks and score) without any **`advanced`** outcome in recent progression history
- Quiescence **`products_stuck_or_repeating`** when present

Products that clearly match a **stable benign** heuristic (e.g. advance-ready, low debt, no queued next action, not blocked) and trigger **no** detection codes are listed under **`stable_benign_products`** and are not flagged.

## Intervention categories

Each flagged product receives an **`intervention_category`** chosen deterministically from:

| Category | Typical meaning |
|----------|-----------------|
| `import_repair` | Import health / first-pass stuck; fix sources or importer path |
| `evidence_refresh` | Stale or thin signals; collect / ingest before more decisions |
| `human_review` | Approval or input gate; operator decision required |
| `policy_tuning` | Autonomy / gates / doctrine mismatch with observed loop |
| `product_cleanup` | Deprecate, merge, or archive product node |
| `safe_to_ignore` | Benign repetition or intentional wait; document and stop churning |
| `continue_monitoring` | Emerging or low-severity; watch next cycle |

## Severity and chronicity

- **`severity`**: `low` | `medium` | `high` — from code count, queue position, and blocking duration
- **`chronicity`**: `newly_emerging` | `chronic` — from how many stamped runs span the issue and how long patterns persist

## Per-flagged product fields

- `product_id`
- `intervention_category`
- `severity`
- `detection_reason_codes`
- `evidence_summary` (short deterministic narrative)
- `recommended_operator_action`
- `chronicity` (and related hints in the JSON payload)

## CLI

```bash
uv run argus portfolio intervention
uv run argus portfolio intervention --json
uv run argus portfolio intervention --no-save    # stdout only; no files under runs/portfolio/intervention/
```

## See also

- [operator-queue.md](operator-queue.md) — queue construction
- [portfolio-progression.md](portfolio-progression.md) — stamped progression runs
- [portfolio-delta-report.md](portfolio-delta-report.md) — delta baselines and material thresholds
- [portfolio-quiescence.md](portfolio-quiescence.md) — stuck/repeating signals and quiescence baseline
