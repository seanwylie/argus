# Product deprecation proposals (`argus.product_deprecation_proposals.v1`)

**CLI:** `argus products propose-deprecation` (`--json`, `--no-save`)

**Module:** `argus/products/deprecation.py`

**Artifacts:** `runs/products/deprecation/latest.{json,md}` and timestamped copies.

## Purpose

Provide a **disciplined, advisory** list of products that look like candidates to **retire**, **harvest**, **archive** (freeze / read-only), or **repair first** (defer wind-down until intervention burden drops). Nothing is executed automatically: no lifecycle changes, no orchestration, no file deletes.

## Inputs (read-only)

| Source | Role |
|--------|------|
| `runs/portfolio/outcomes/latest.json` | Per-product trajectories, intervention/blocked patterns, mission interpretation |
| `runs/portfolio/strategy/latest.json` | Strategic posture (context for scoring) |
| `build_intervention_inbox_payload` | Active inbox items per product |
| `runs/portfolio/operator_queue/latest.json` | Readiness tier, priority (context) |
| `runs/portfolio/patterns/latest.json` | Patterns whose `affected_products` include the product |
| Product inventory | Lifecycle stage |

Missing artifacts weaken signals; the evaluator still runs with whatever is present.

## Deprecation postures

| Posture | Meaning (informal) |
|---------|-------------------|
| `retire` | Wind down and exit active investment |
| `harvest` | Extract value on a short horizon (often with positive trajectory) |
| `archive` | Freeze / park; minimal ops |
| `repair_instead` | Intervention or structural load is high — stabilize before deprecation |

Scoring is **deterministic** and **bounded**: each posture gets a numeric score; the highest above a minimum threshold wins, with tie order **repair_instead → retire → archive → harvest** (remediation first when scores tie).

## Proposal shape

Each proposal includes:

- `proposal_id`, `product_id`, `deprecation_posture`
- `rationale`, `supporting_evidence` (outcomes, strategy, inbox, queue, patterns, score breakdown)
- `confidence` (`high` / `medium` / `low` — from margin between top two postures)
- `recommended_next_step` (operator-facing sentence)

## CLI

```bash
uv run argus products propose-deprecation
uv run argus products propose-deprecation --json
uv run argus products propose-deprecation --no-save
```

## See also

- [portfolio-outcomes.md](portfolio-outcomes.md) — trajectory definitions
- [portfolio-strategy.md](portfolio-strategy.md) — strategic posture
- [portfolio-strategy-integration.md](portfolio-strategy-integration.md) — soft strategy influence
