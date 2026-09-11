# Portfolio patterns (`argus.portfolio_patterns.v1`)

**CLI:** `uv run argus portfolio patterns` (`--json`, `--no-save`, `--limit-history N`, optional `--products-dir`)

**Artifacts:** `runs/portfolio/patterns/latest.{json,md}` plus timestamped copies.

## Purpose

Answer: **Which problems look systemic (shared across products), not one-off?**

The detector is **deterministic** and uses existing data only:

- Latest **`evaluate_portfolio_outcomes`** (same spine as `argus portfolio outcomes`)
- **`runs/portfolio/intervention/latest.json`** and recent stamped **intervention** JSONs
- **`product.yaml`** via inventory: **`raw_extensions.argus_onboarding.product_shape.label`** and **`raw_extensions.import_state.first_pass_status`**

It does not run importers, orchestration, or models.

## What counts as systemic

Most patterns require **at least two products** in the same bucket (configurable constant `SYSTEMIC_MIN_PRODUCTS` in code). Single-product regressions are listed under **`isolated_negative_products`** when they are not part of a multi-product pattern.

## Pattern families (examples)

| Id prefix | Idea |
|-----------|------|
| `patterns.import.*` | Several products with `partial` / `failed` first-pass import in `product.yaml` |
| `patterns.outcomes.import_health_stagnation_*` | Import health trajectory stuck bad across products (outcomes) |
| `patterns.outcomes.decision_confidence_worsening_cluster` | Confidence worsening on multiple lines |
| `patterns.outcomes.blocked_progression_persisted_multi_product` | Orchestration blocked posture persisting |
| `patterns.outcomes.parallel_stagnation_*` | Flat debt + unchanged readiness + no meaningful movement |
| `patterns.intervention.shared_category.*` | Same intervention category on multiple products (latest intervention run) |
| `patterns.intervention.recurring_category.*` | Same category across products over **multiple** stamped intervention files |
| `patterns.shape.correlated_negative_outcomes.*` | Same **product shape** with **negative** overall outcome |

Each **`detected_patterns`** row includes **`severity`**, **`recurrence`**, **`affected_products`**, and **`recommended_systemic_action`** (portfolio-level guidance, not per-product tickets).

## Markdown sections

Generated Markdown highlights **systemic issues**, **recurring failure modes**, **shared improvement opportunities**, and **possible importer / policy gaps** (heuristic mapping from pattern ids and recommended actions).

## Workflow

1. Keep **`argus portfolio outcomes`** and **`argus portfolio intervention`** up to date on your cadence.
2. Ensure **`product.yaml`** records shape (`argus_onboarding.product_shape`) and **`import_state`** where applicable.
3. Run **`argus portfolio patterns`** and read `latest.md` before deep per-product firefighting.

## Programmatic use

```python
from pathlib import Path
from argus.portfolio.patterns import evaluate_portfolio_patterns, run_portfolio_patterns

payload = evaluate_portfolio_patterns(Path("."), limit_history=50)
run_portfolio_patterns(Path("."), limit_history=50, write_artifacts=True)
```

For tests or custom tooling, **`detect_cross_product_patterns(...)`** accepts plain dicts (outcomes rows, product meta, intervention payloads).

See also [portfolio-outcomes.md](portfolio-outcomes.md), [portfolio-intervention.md](portfolio-intervention.md), and [importer-discovery-classification.md](importer-discovery-classification.md).
