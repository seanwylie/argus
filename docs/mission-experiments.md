# Mission experiments (`argus.mission_experiment.v2`)

Read-only comparisons of operator-facing behavior under alternate **mission profiles** or **structured mission compositions** (objective + optional driver and guardrail overlays), all from `config/mission_profiles.yaml`. Use this to show Argus is **mission-conditioned** in policy, queue scoring, quiescence, intervention, and policy recommendations — including when objective, drivers, and guardrails are mixed independently.

## Guarantees

- **No progression or execution writes** — evaluates in memory from existing `runs/` inputs where applicable.
- **No truth-layer mutation** — does not edit `products/*/product.yaml`, mission registry files, or stamped mission artifacts.
- **Experiment scope only** — overrides apply inside :func:`argus.mission.mission.mission_experiment_scope`. Outside the scope, canonical resolution (product `mission_id` → repository fallback) is unchanged.

## Structured compositions

Compositions use the same registry ids as structured `mission:` on `product.yaml`: an **objective** profile id plus optional **drivers** and **guardrails** (each entry is another profile id). Policy merging follows :func:`argus.policy.mission_mapping.apply_structured_mission_to_operator_policy` — objective at full strength, drivers at reduced overlay strength, guardrails as constraint nudges — so each axis can move policy independently.

## CLI

```bash
# Targeted: one product, one or more profiles (aligns global mission to each profile for portfolio-wide policy reads)
uv run argus mission test --product-id <id> --profile revenue education --json

# Targeted: compare composed missions (mutually exclusive with --profile)
uv run argus mission test --product-id <id> \
  --composition "objective=revenue drivers=engagement,education" \
  --composition "objective=revenue drivers=engagement guardrails=education" \
  --json

# Sweep: compare several profiles (full portfolio, or restrict with repeated --product-id)
uv run argus mission sweep --profiles revenue,education,engagement --json

# Sweep: compare compositions
uv run argus mission sweep \
  --composition "objective=revenue drivers=engagement" \
  --composition "objective=education guardrails=engagement"

# Mixed portfolio: force missions only for listed products; repository global mission is unchanged
uv run argus mission sweep --profiles revenue,education --product-id alpha --product-id beta
```

Composition string format: space-separated `key=value` tokens — **`objective=<id>`** (required), **`drivers=<id>,<id>`**, **`guardrails=<id>,<id>`**. Values are comma-separated mission profile ids (no spaces inside a value token).

Flags:

- **`--json`** — print `argus.mission_experiment.v2` JSON to stdout.
- **`--no-save`** — skip `runs/mission/experiments/latest.{json,md}` and timestamped copies.

## Artifacts

With writes enabled (default): `runs/mission/experiments/latest.json`, `latest.md`, plus stamped `YYYYMMDDTHHMMSSZ.{json,md}`.

## Payload overview

| Field | Purpose |
|--------|---------|
| `experiment_variant_kind` | `"profiles"` or `"compositions"` |
| `compared_profiles` | Human-readable labels for each variant (profile id or full composition line) |
| `compared_compositions` | When kind is `compositions`, list of `{ objective, drivers, guardrails }`; else `null` |
| `products_evaluated` | Products included in provenance and per-product policy snippets |
| `per_profile[].composition` | When present, structured `{ objective, drivers, guardrails }` for that variant |
| `per_profile[].mission_mix_used` | `argus.portfolio_mission_provenance.v1` under experiment resolution |
| `per_profile[].queue_top_slice` | First slice of the operator queue under that variant |
| `policy_deltas_vs_reference` / `recommendation_deltas_vs_reference` / … | Differences vs the first listed variant |
| `behavior_change_summary` | Short human-readable deltas |
| `areas_no_meaningful_change_vs_reference` | Layers where fingerprints matched the reference variant |
| `narrative` | Operator narrative is history-driven; not recomputed per mission here |

## Python API

```python
from pathlib import Path
from argus.mission.experiment import evaluate_mission_experiment, run_mission_experiment
from argus.mission.mission import MissionExperimentComposition

root = Path(".")
payload = evaluate_mission_experiment(
    root,
    ["revenue", "education"],
    product_ids=["my_product"],
    mode="targeted",
)
# Structured compositions (read-only compare):
payload = evaluate_mission_experiment(
    root,
    compared_compositions=[
        MissionExperimentComposition("revenue", ("engagement",), ("education",)),
        MissionExperimentComposition("education", (), ("engagement",)),
    ],
    product_ids=["my_product"],
    mode="targeted",
)
# Or with artifact writes:
run_mission_experiment(root, ["revenue", "education"], product_ids=["my_product"], mode="targeted")
```

Modes:

- **`targeted`** — requires `product_ids`; per-product overrides + aligned global mission to each variant’s **objective**.
- **`sweep_all`** — full inventory (optional product filter); uniform variant per product + global alignment to objective.
- **`sweep_mixed`** — requires `product_ids`; only those products get overrides; global mission unchanged (mixed portfolio).

## See also

- [mission-model.md](mission-model.md) — canonical `mission_id` on products and resolution order.
- [operator-policy.md](operator-policy.md) — effective policy and mission integration.
- [mission-policy-integration.md](mission-policy-integration.md) — structured `mission:` composition on products.
- `argus policy experiment` — YAML policy profile comparison (orthogonal to registry mission ids).
