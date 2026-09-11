# Doctrine (per-product policy)

Optional file: **`products/<id>/doctrine.yaml`**.

## Schema

Top-level `schema: argus.doctrine.v1`. See `argus.doctrine.validate` for fields.

Rough shape:

- `summary`, `principles` — human context; surfaced to advisors.
- `constraints` — e.g. `max_monthly_cost_usd`, `require_human_review_when_kill_candidate`.
- `scoring` — `intent_priority_multiplier` (decision intent strings → multiplier), `experiment_score_boost`.

## Behavior

- **Findings**: Violations emit `FindingKind.DOCTRINE_VIOLATION` when `generate_findings(..., repo_root=...)` runs.
- **Decisions**: Nudges `priority_score` via `apply_doctrine_to_decision_candidates` after candidates are built.
- **Experiments**: `experiment_score_boost` applied in experiment prioritization when doctrine is loaded.
- **Ideas**: `experiment_score_boost` nudges **expected value** for `explore` / `invent` ideas when `argus ideas generate` runs with a product scope (`apply_doctrine_to_ideas` in `argus/doctrine/scoring.py`); see `runs/ideas/latest.json` → `meta.doctrine_ideas`.
- **Advisors**: Doctrine JSON is included in consultation context.

## CLI

`argus doctrine show <product_id> [--json]`

Doctrine is **advisory + scoring**; **autonomy** still gates execution.
