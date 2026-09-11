# Artifact refinement loops

Argus supports a **general** pattern: **draft → stakeholder council → structured reviews → synthesis → regeneration → convergence**, persisted under `runs/refinement/<session_id>/`. Councils are **routed** by artifact type into **grounded** (repo-aware) and **outsider** (limited-context) seats — see [councils.md](councils.md). It extends the optional [LLM integration](llm-integration.md) and reuses the same client and gating (`ARGUS_LLM_ENABLED`, `ARGUS_OPENAI_API_KEY`). Optional **context packets** (`ARGUS_CONTEXT_PACKETS=1`, package `argus.context`) assemble capped **product.system** + **artifact.draft** inputs for prompts; per-member **context policies** differ for outsiders vs grounded. When a product-scoped audit exists, grounded bundles may include a bounded **`audit.summary`** slice. See [audit-mvp.md](audit-mvp.md) and [reasoning-context-refinement-architecture.md](plans/reasoning-context-refinement-architecture.md).

## Why this exists

- **Ideas**, **product specs**, and **implementation plans** need different **questions** and **councils**.
- Reviews must be **structured** (verdict, blocking flag, categories, objections, suggestions) — not vague “feels weak” feedback.
- **Regeneration** must **respond to synthesis** (blocking + required changes), not restart blindly.
- Outcomes remain **explainable**; LLM augments drafting/review text, **not** autonomy or execution.

## Artifact types and councils

Canonical composition lives in **`argus.council.profiles`** (also exposed via `argus council show <type>`). `argus.refinement.routing.council_for()` delegates to the council package.

| Type | Guiding question | Council (summary) |
|------|------------------|-------------------|
| `idea` | Should this be pursued? | Grounded: product, finance, technical — Outsider: investor, marketer, creative (optional) |
| `product_spec` | Is this the right product shape? | Grounded: product, doctrine, ux (+ optional technical, finance) — Outsider: marketer, customer_proxy (optional) |
| `implementation_plan` | Is this build plan sound? | Grounded only: technical, architecture, bones, product |

**Bones** is the structural spine of the plan (sequencing, safety, intent preservation). If bones integration with other subsystems is still partial, reviews remain **explicit** in artifacts; see [stub-inventory.md](stub-inventory.md) for gaps.

## Loop mechanics

1. **`argus refine start`** creates a session (`runs/refinement/<session_id>/session.json`, index entry).
2. **`argus refine run <session_id>`** executes one **cycle**:
   - ensure draft for `current_round` (initial or regenerated from prior synthesis),
   - run council reviews (LLM JSON or deterministic stub),
   - write `reviews/round_<n>.json`, `synthesis/round_<n>.json`, `convergence/round_<n>.json`,
   - evaluate convergence; if not done, increment round and prepare for the next run.
3. Stop when convergence returns **approved**, **approved_with_risks**, **rejected**, or **human_review_required** (max rounds, doctrine/product-spec doctrine path, or blocking thresholds).

## Convergence (deterministic)

Rules live in `argus.refinement.convergence` — **weighted confidence**, **pass ratio**, **blocking fails**, and **hard-block** stakeholders (`argus.refinement.routing.can_hard_block`). **Grounded** reviews drive approval math; **outsider** reviews do not (see [councils.md](councils.md)). Examples:

- **Doctrine** failure on `product_spec` can route to **human_review_required** (not silent approval).
- **Finance** / **critical** blocks can **reject** when multiple blocking fails accumulate.
- **Creative** concerns on **ideas** are **not** hard-block by default.
- **Bones** / **architecture** on **implementation_plan** can hard-block.

## Integration

- **Decision confidence**: `refinement_uncertainty_nudge()` adds a small **uncertainty** contribution when open sessions exist for a product (see `argus.refinement.signals`).
- **Doctrine**: injected into review prompts as text (product-scoped).
- **Strategy**: initial draft prompt includes strategy mode from `runs/strategy/current.json` when present.
- **Autonomy**: refinement does **not** change tiers, modes, or execute commands.

## Operator visibility

- **Dashboard** JSON: `refinement` block (`argus.dashboard_refinement.v2`) — active sessions, blocking counts, verdict tallies, `session_details` drill-down (latest round snippets + round-chain health).
- **Doctor**: `check_refinement_health` for malformed index / missing `session.json` / suspicious `current_round` / orphaned or out-of-order round files under `drafts` → `reviews` → `synthesis` → `convergence`.

## CLI

```text
argus refine start --type idea --source <idea_id> [--product-id <id>] [--max-rounds N]
argus refine run <session_id>
argus refine show <session_id>
argus refine list
argus refine approve <session_id>
argus refine retry <session_id>
```

Use `--json` for machine-readable output.

## Related

- [idea-generation.md](idea-generation.md) — raw ideas; refinement can start from an `idea_id` in `runs/ideas/latest.json`.
- [decision-confidence.md](decision-confidence.md) — assessment vs refinement uncertainty nudge.
- [architecture.md](architecture.md) — subsystem map.
