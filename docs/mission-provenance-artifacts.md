# Mission provenance on operator artifacts

Stamped JSON and Markdown record **which mission** influenced operator-facing behavior. **Argus does not assume one global mission for the repository** — products declare mission (structured **`mission:`** or legacy **`mission_id`**); **portfolio** artifacts use an explicit **mix** representation.

## Per-product: `mission_context`

Built by `build_mission_context_for_product(repo_root, product_id)` (`schema: argus.mission_provenance.v1`):

- **`resolved_mission_id`** — **objective** profile id (same as legacy single-mission behavior)
- **`mission_objective`**, **`mission_drivers`**, **`mission_guardrails`** — when structured `mission:` is present
- **`mission_risk_posture_override`** — when `mission.risk_posture` is set in YAML
- **`mission_profile_id`**, **`risk_posture`** (effective for display), **`primary_objective`**
- **`resolution_chain`** — human-readable precedence
- **`product_id`**, **`resolution_scope`** (`product` \| `repository_fallback`)
- **`effective_mission_evaluated_at_utc`**

**Where:** e.g. `runs/orchestration/operator_snapshot/<product_id>.json` (+ Markdown).

## Repository fallback: `build_repository_mission_context`

Used when documenting **repository-level** mission only (no product), e.g. legacy tooling. **`resolution_scope`** is `repository`.

## Portfolio scope: `portfolio_mission_provenance`

Built by `build_portfolio_mission_provenance(repo_root, product_ids)` (`schema: argus.portfolio_mission_provenance.v1`):

- **`mission_context_by_product`** — map of product id → per-product `mission_context` block
- **`mission_mix_summary`** — includes:
  - **`distinct_mission_ids`** / **`counts_by_mission_id`** — **objective** profile id counts (same meaning as before for legacy-only products)
  - **`driver_profile_counts`** — how often each profile id appears in **`mission_drivers`** across products
  - **`guardrail_profile_counts`** — how often each profile id appears in **`mission_guardrails`**
  - **`products_using_repository_fallback`** — products with no mission declaration in YAML
- **`evaluated_at_utc`**

Portfolio artifacts **do not** use a single top-level `resolved_mission_id` for the whole portfolio (that would be misleading when products differ). Older stamped files may still carry legacy **`mission_context`** (single repo snapshot); Markdown renderers show a **legacy** section when present.

## Where portfolio provenance appears

| Artifact | Typical path |
|----------|----------------|
| Operator queue | `runs/portfolio/operator_queue/latest.json` |
| Portfolio cycle | `runs/portfolio/cycle/latest.json` |
| Portfolio outcomes | `runs/portfolio/outcomes/latest.json` |
| Operator policy feedback | `runs/policy/feedback/latest.json` |
| Operator policy recommendations | `runs/policy/recommendations/latest.json` |
| Operator summary (dashboard) | `runs/dashboard/operator_summary/latest.json` |
| Operator narrative (dashboard) | `runs/dashboard/narrative/latest.json` |

**Note:** Portfolio **outcomes** materiality thresholds use **repository-level** `load_operator_policy(repo)` (single comparison baseline across products); per-product mission is still recorded in `portfolio_mission_provenance`.

## Backward compatibility

- Legacy portfolio stamps may omit **`portfolio_mission_provenance`** or only have **`mission_context`**; renderers accept both.
- **`counts_by_mission_id`** remains **objective**-centric; newer **`driver_profile_counts`** / **`guardrail_profile_counts`** default to empty mappings when no structured missions exist.
- Consumers should treat missing blocks as **unknown** and fall back to current `product.yaml` / `argus mission show`.

## See also

- [mission-model.md](mission-model.md)
- [mission-policy-integration.md](mission-policy-integration.md)
- [product-model.md](product-model.md)
