# Lifecycle-aware autonomy (scheduler & autonomous runner)

Bounded sessions (`argus portfolio schedule`, `argus portfolio run-autonomous`) include **soft, inspectable** lifecycle context so operators see **where products sit in their lifecycle** and **what the portfolio most needs**, without changing orchestration eligibility or queue math.

## What is added

Each session payload includes **`lifecycle_session_influence`** (schema `argus.portfolio_lifecycle_session_influence.v1`):

| Field | Role |
|-------|------|
| `primary_signal` | One of `create_heavy`, `repair_heavy`, `retirement_heavy`, `mixed_sparse`, `neutral` — derived from lifecycle synthesis |
| `inputs_snapshot` | Bounded snapshot: `lifecycle_counts`, entering/exiting ids, repair/retirement pressure lists, `portfolio_strategy_posture` |
| `session_notes` | Human-readable emphasis (creation vs repair vs exit vs conservative) |
| `priority_hints` | Soft triage hints — **not** queue rewrites or blocks |
| `stop_continue_context` | Bias label (`creation_forward`, `repair_first`, `exit_aware`, `conservative`, `neutral`) plus a short note for when to prefer pausing vs continuing |
| `strategy_alignment` | Merges `describe_soft_influence(posture)` with lifecycle signal and optional **alignment notes** when posture and lifecycle pressure disagree (e.g. `expand` vs repair pressure) |

## Data sources

Influence is computed from the same fields as portfolio lifecycle synthesis:

- `lifecycle_counts`
- `products_under_repair_pressure` / `products_under_retirement_pressure`
- `products_entering` / `products_exiting`
- `portfolio_strategy_posture` (from `runs/portfolio/strategy/latest.json` via lifecycle evaluation)

**Scheduler** evaluates `evaluate_portfolio_lifecycle` once at session start (read-only synthesis; no duplicate artifact write from that call).

**Autonomous runner** computes influence from each iteration’s **lifecycle payload** after `run_portfolio_lifecycle`; the session-level `lifecycle_session_influence` reflects the **last completed iteration**.

## What does *not* change

- No hard gates on progression, orchestration, or imports.
- No suppression of legal actions.
- Operator queue scoring in `operator_queue.py` is unchanged; lifecycle influence for sessions is **documentation and bias context** only.

For queue-level **bounded numeric nudges** from strategy posture, see `strategy_influence.queue_priority_nudge` — orthogonal to session notes.

## Markdown

Session `latest.md` files include a **Lifecycle-aware context** section with primary signal, notes, hints, and stop/continue bias.

## Tests

```bash
uv run pytest tests/test_lifecycle_session_influence.py
```

## See also

- `docs/autonomous-runner.md` — autonomous runner CLI
- `argus/portfolio/strategy_influence.py` — strategic posture hints
- `argus/portfolio/lifecycle.py` — lifecycle synthesis
