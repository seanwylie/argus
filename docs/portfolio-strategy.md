# Portfolio strategy

Whole-portfolio strategic posture answers: **what should the portfolio do next**, not only which single product to touch. The layer is **deterministic**: it reads existing portfolio artifacts and derives a labeled posture plus rationale, pressures, opportunities, risks, and recommended moves.

## Schema

- **`argus.portfolio_strategy.v1`** — emitted by `argus/portfolio/strategy.py`.

## Inputs (read-only)

The evaluator combines:

- **Operator queue** (`argus.operator_queue.v1`) — advance-ready concentration, average understanding debt, queue shape.
- **Portfolio outcomes** (`argus.portfolio_outcomes.v1`) — trajectory mix, mission alignment, blocked/intervention stress.
- **Portfolio patterns** (`argus.portfolio_patterns.v1`) — cross-product severity and pattern ids (e.g. import-related).
- **Intervention inbox** (`argus.intervention_inbox.v1`) — open items, active queue, severity.
- **Operator policy effectiveness** (`argus.operator_policy_effectiveness.v1`) — segment rates (e.g. intervention strain, stagnation) and notable descriptive patterns.
- **Delta report** (`argus.portfolio_delta_report.v1`) when present — e.g. `new_product_in_queue` material rows.
- **Creation proposals** (`argus.product_creation_proposals.v1`) when `runs/products/creation/latest.json` exists — proposal count and creation-mission context.

Missing optional artifacts weaken specific signals but do not abort the run.

## Strategic postures

One primary posture is selected from:

| Posture       | Typical meaning |
|---------------|-----------------|
| **expand**    | Momentum and mission-positive mix — lean into growth and throughput. |
| **consolidate** | Mixed/flat dominance or many missions — narrow bets and standardize. |
| **repair**    | Negative trajectories, systemic patterns, or intervention/policy strain — fix foundations first. |
| **harvest**   | Strong winners with advance-ready, low-debt posture — extract value and document “done”. |
| **create**    | New product materiality and/or creation proposals — prioritize the creation pipeline. |
| **retire**    | Negative or flat tail, cleanup-style patterns — explicit exit or merge criteria. |

Ties resolve in a **fixed priority order** (documented in code) so runs are reproducible.

## Payload fields

- **`strategic_posture`** — one of the postures above.
- **`rationale`** — short bullet lines, including deterministic score snapshot.
- **`dominant_mission_mix`** — from portfolio mission provenance counts (top mission id + summary).
- **`portfolio_pressures`** — human-readable pressure lines (trajectory, patterns, inbox, alignment, etc.).
- **`top_opportunities`** — e.g. positive or advance-ready product ids when known.
- **`top_risks`** — negative trajectories, high-severity patterns, severe inbox items.
- **`recommended_next_portfolio_moves`** — actionable next steps aligned to the posture.
- **`posture_scores`** — numeric scores per posture (transparency for operators and tests).

## CLI

```bash
uv run argus portfolio strategy
uv run argus portfolio strategy --json
uv run argus portfolio strategy --no-save
uv run argus portfolio strategy --limit-history 30
```

`--no-save` evaluates and prints without writing files. Default writes:

- `runs/portfolio/strategy/latest.json` and `latest.md`
- Timestamped `{run_id}.json` / `{run_id}.md` alongside them

## Relation to other portfolio commands

Strategy **does not** mutate policy, products, or orchestration. Run `argus portfolio refresh`, `outcomes`, `patterns`, `intervention-inbox`, and `products creation` (or rely on existing `runs/`) first when you want fresh inputs.
