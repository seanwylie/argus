# Mission → operator policy integration

Mission is a **behavioral** input: it shapes **effective operator policy** through a **bounded, inspectable** mapping. It does **not** change how Argus collects **signals**, runs **audit**, or generates **findings** — those layers stay evidence-first and mission-agnostic.

**Products declare mission** via optional structured **`mission:`** or legacy **`mission_id`** in `product.yaml` (see [mission-model.md](mission-model.md), [product-model.md](product-model.md)). **Repository** defaults and env overrides apply when no product is in scope or when a product has no mission declaration.

## Resolution order (policy merge)

1. Merge **defaults** with `config/operator_policy.yaml` (unchanged).
2. Resolve **effective mission** for the scope:
   - **With `product_id`:** **`resolve_product_mission`** — `product.yaml` `mission` / `mission_id` first (operational); **global mission is not consulted** for policy when that declaration is present. If absent, repository fallback (env → `current.json` → default) applies.
   - **Without `product_id`:** **`resolve_global_mission`** — repository-only (`ARGUS_MISSION_ID` → `runs/mission/current.json` → `default_mission_id`).
3. Apply **mission mapping** (`argus.policy.mission_mapping`):
   - **Structured payload** (`structured_mission` on `resolve_product_mission`): **`apply_structured_mission_to_operator_policy`** — **objective** profile at full strength, **drivers** at lighter additive strength, **guardrails** as constraint-oriented overlays, then **effective risk posture** (product `mission.risk_posture` if set, else objective profile posture).
   - **Repository fallback / global-only:** **`apply_mission_profile_to_operator_policy`** — single profile + risk posture (unchanged shape).

Cache invalidation for `load_operator_policy` tracks `operator_policy.yaml` mtime, mission inputs (`ARGUS_MISSION_ID`, `config/mission_profiles.yaml` mtime, `runs/mission/current.json` mtime), and **per-product** `products/<id>/product.yaml` mtime when `product_id` is set.

## What mission may influence

Bounded adjustments target operator-facing knobs only, for example:

- **Queue scoring** — `queue_scoring.family_points`, `lifecycle_points` (e.g. experiments appetite, growth focus).
- **Intervention** — windows and stagnation sensitivity (`progression_runs_window`, `stagnation_min_delta_reports`).
- **Quiescence / materiality** — `debt_delta_material`, `confidence_delta_material`.
- **Confidence** — `confidence.low_threshold`.
- **Readiness** — `gate_debt_caution` (risk posture and guardrail overlays).

Unknown custom **objective** profile ids fall back to **risk posture only** (plus a note in `adjustments_applied`).

## Inspectability

- **`load_operator_policy(repo, product_id=None)`** returns policy with **`mission_integration`** (`schema: argus.operator_policy_mission_context.v1`): resolved id, optional **`product_id`**, **`resolution_scope`**, resolution chain, mission fields, optional **`mission_composition`** (objective, drivers, guardrails, effective risk posture) for structured missions, **`adjustments_applied`**, and **`policy_areas_touched`**.
- **`runs/policy/operator_policy_effective.json`** (written without a product id) reflects **repository-level** mission merge for global inspection.

## Provenance on operator artifacts

Portfolio and dashboard outputs embed **`portfolio_mission_provenance`** (`argus.portfolio_mission_provenance.v1`) when generated at portfolio scope — per-product mission context plus a **mix summary**, not a single portfolio-wide mission id. See [mission-provenance-artifacts.md](mission-provenance-artifacts.md).

## Truth-first boundary

Code under `argus/findings/`, `argus/audit/`, and `argus/signals/` must not call `load_operator_policy`. Tests enforce absence of `load_operator_policy` in those trees.

## See also

- [mission-model.md](mission-model.md)
- [operator-policy.md](operator-policy.md)
