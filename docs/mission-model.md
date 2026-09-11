# Mission / ethos model

Argus is **mission-agnostic** in its truth-producing layers: **signals**, **audit**, and **findings** stay evidence-first. **Mission** is operator intent for **policy and interpretation** — expressed per product and, separately, as a **global default** for fallbacks and tooling.

## Product mission vs global mission

| Concept | Role |
|--------|------|
| **Product mission** (operational) | Canonical for an existing product: optional **structured `mission:`** or legacy **`mission_id`** in `products/<id>/product.yaml`, referencing profiles in `config/mission_profiles.yaml`. Drives **operator policy** when `product_id` is known (`load_operator_policy(..., product_id=...)` uses `resolve_product_mission` — global mission does **not** apply when the product declares a mission). |
| **Global mission** | Repository default + overrides: **not** a substitute for per-product intent. Used for: products **without** a mission declaration (fallback chain), **testing/simulation** (`ARGUS_MISSION_ID`, `runs/mission/current.json`), **portfolio-wide baselines** without a product scope, and **`runs/mission/mission_effective.json`** / `argus mission show`. Resolved by `resolve_global_mission` (same as `resolve_effective_mission`). |
| **Creation mission** (placeholder) | **Future** default bias when **creating** new products (scaffold). `resolve_creation_mission(repo_root)` is defined today and equals global resolution; it is **not** wired into `argus products create` yet. Operational behavior always uses **product** or **global fallback** per product, not creation bias. |

**Products carry missions.** Each product may set a mission using either form below.

## Structured product mission (`product.yaml`)

Composable mission without exploding custom profile ids:

```yaml
mission:
  objective: revenue                 # registry profile id — primary policy influence
  drivers: [education, engagement] # optional — lighter additive overlays (same registry ids)
  guardrails: [education]            # optional — constraint-oriented overlays
  risk_posture: conservative         # optional — conservative | moderate | aggressive; applied last
```

**Rules:**

- **`objective`** (required when `mission:` is present) — must exist in `config/mission_profiles.yaml` (merged registry).
- **`drivers`** / **`guardrails`** — lists of profile ids; each must exist in the registry.
- **No duplicate profile ids** across `objective`, `drivers`, and `guardrails` combined.
- **`risk_posture`** — optional; when set, overrides the objective profile’s default posture for **effective** policy (applied after objective/drivers/guardrails in the policy mapper).

## Legacy shorthand: `mission_id`

```yaml
mission_id: revenue
```

Equivalent to:

```yaml
mission:
  objective: revenue
```

Use **either** `mission_id` **or** `mission:` — not both (validated at load).

## Schemas

| Schema | Role |
|--------|------|
| `argus.mission_registry.v1` | Root document in `config/mission_profiles.yaml` — `default_mission_id` and named `profiles`. |
| `argus.mission_profile.v1` | One mission: `id`, `primary_objective`, `drivers`, `risk_posture`, optional `policy_influence_hints`, optional `weights`. |
| `argus.product_mission_spec.v1` | Structured product mission (objective, drivers, guardrails, optional risk posture) — `ProductMissionSpec` in code. |
| `argus.mission_effective.v1` | Resolved mission + `resolution_chain` + `resolved_at_utc`, optional `product_id`, `resolution_scope`, optional **`structured_mission`** (objective, drivers, guardrails, effective risk posture). |
| `argus.mission_provenance.v1` | Compact identity on **per-product** artifacts (e.g. operator snapshot). |
| `argus.portfolio_mission_provenance.v1` | **Portfolio** stamps: `mission_context_by_product` and `mission_mix_summary` (objective counts plus driver/guardrail usage) — no single portfolio-wide “the” mission. |

## Per-product resolution (operational, canonical)

For a given **product id**, effective **objective** profile id is chosen in this order (**first wins**):

1. Active `mission_experiment_scope` **structured composition** (experiment tooling only), else simple profile override
2. **`products/<id>/product.yaml` → `mission.objective` or legacy `mission_id`**
3. **`ARGUS_MISSION_ID`** environment variable
4. **`runs/mission/current.json`** with `{ "mission_id": "<id>" }`
5. **`default_mission_id`** in `config/mission_profiles.yaml` (merged over built-in defaults)

Use **`resolve_product_mission(repo, product_id)`** (or `resolve_effective_mission_for_product`) for the full payload, including **`structured_mission`** when the product declares a mission.

Unknown profile ids raise a clear `ValueError` listing known profile ids.

## Global-only resolution

When no product is in scope, use **`resolve_global_mission(repo)`** — precedence is steps **3 → 5** above (no `product.yaml`). Same function as `resolve_effective_mission`.

## Creation mission (placeholder)

**`resolve_creation_mission(repo_root)`** returns the mission profile intended as **default bias for new product scaffolding** in a future pipeline. Today it delegates to **`resolve_global_mission`**. Do not confuse with per-product operational resolution.

## Risk posture

Allowed values: `conservative`, `moderate`, `aggressive` (validated on registry profiles and optional `mission.risk_posture`).

## Config

- **`config/mission_profiles.yaml`** — optional; merged over embedded defaults. Ships with at least **`revenue`** (default), **`education`**, and **`engagement`**.

## CLI

```bash
uv run argus mission show
uv run argus mission show --json
```

- Shows **global** effective mission (repository-level), not a specific product.

## Python API

- **Operational (product):** `resolve_product_mission`, `resolve_effective_mission_for_product`, `resolve_effective_mission_id_for_product`, `read_product_mission_id` (returns **objective** id)
- **Global / fallback / tooling:** `resolve_global_mission`, `resolve_effective_mission`, `resolve_effective_mission_id`
- **Creation (placeholder):** `resolve_creation_mission`
- Registry: `load_mission_registry`, `load_mission_by_id`, `load_default_mission`, `write_mission_effective_artifact`
- Parsing/validation: `argus.mission.product_mission` — `parse_product_mission_from_yaml`, `validate_product_mission_registry`

Provenance: `argus.mission.provenance` — `build_mission_context_for_product`, `build_repository_mission_context`, `build_portfolio_mission_provenance`.

## Operator policy

`load_operator_policy(repo, product_id=...)` merges mission **per product** when `product_id` is set: **objective** (full mapping), **drivers** (lighter overlays), **guardrails** (constraint overlays), then **effective risk posture**. **Global mission does not influence** policy for that call when the product declares a mission. Without `product_id`, global mission applies. See [mission-policy-integration.md](mission-policy-integration.md).
