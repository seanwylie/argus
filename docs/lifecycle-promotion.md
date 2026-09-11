# Lifecycle promotion

Argus exposes **explicit, traceable** lifecycle promotions through `argus.products.promotion`: creation proposal → product scaffold (and optionally first bootstrap), scaffolded product → bootstrap, and deprecation proposal → **plan-only** deprecation plan artifact. Those primitives are safe by construction: creation refuses existing product directories; deprecation plans do not delete files or mutate `product.yaml`.

## Autonomous session integration

`argus portfolio run-autonomous` **always** runs a read-only **promotion opportunity scan** after the session loop (`collect_promotion_opportunities` in `argus.portfolio.lifecycle`). The autonomous runner session payload includes:

- `promotable_actions` — structured rows the operator (or tooling) can act on
- `promotion_recommendations` — short human-readable lines
- `blocked_promotions` — items that are not auto-safe (duplicate product path, invalid posture, plan already exists, invalid inventory, etc.)
- `promotion_opportunities` — full scan payload (schema `argus.promotion_opportunities.v1`)
- `promotion_execution` — what ran when `--allow-promotion` is set (see below)

Detection does **not** require `--allow-promotion`.

## Bounded execution (`--allow-promotion`)

With `--allow-promotion`, the runner may invoke the same promotion helpers the CLI uses, under tight caps:

1. At most **one** deprecation plan promotion (evaluate or materialize; dry-run uses evaluation only).
2. At most **one** creation scaffold promotion (optional chained bootstrap when not in effective dry-run).
3. If no creation promotion applies and `--promotion-bootstrap` is set, at most **one** standalone bootstrap for a scaffolded product that still needs bootstrap.

Effective dry-run is true when `--dry-run` is set **or** stage persistence is off (so preview paths stay consistent with the rest of the pipeline). In effective dry-run mode, chained bootstrap is surfaced as a **bootstrap preview** step after a successful scaffold preview when `--promotion-bootstrap` is enabled.

Promotion steps are recorded under `promotion_execution.steps` with nested `argus.lifecycle_promotion_action.v1` payloads where applicable. When `--allow-promotion` is omitted, `promotion_execution.skipped_reason` explains that only detection ran.

## Artifacts

- Session record: `runs/portfolio/autonomous_runner/latest.json` (and stamped copies)
- Promotion actions (when writes are enabled and not dry): `runs/products/promotion_actions/latest.json`

## Related modules

- `argus.products.promotion` — promotion wrappers and promotion action schema
- `argus.portfolio.lifecycle` — `collect_promotion_opportunities`
- `argus.portfolio.autonomous_runner` — session orchestration and markdown rendering
