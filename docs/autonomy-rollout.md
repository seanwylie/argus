# Bounded autonomy rollout

Argus separates **what the operator allows** (tier + mode + policy) from **what the repo can prove** (signals, findings, decision assessment). This document is the contract for **safe unattended operation**.

**LLM layers** (`ARGUS_LLM_ENABLED`, optional OpenAI calls) do **not** change autonomy mode, tier, quotas, or execution policy. They only produce advisory text and optional assessment nudges; enforcement stays in `argus.autonomy` and execution gates. See [llm-integration.md](llm-integration.md).

## Tiers (0–4)

| Tier | Name | Typical mode | Intent |
|------|------|----------------|--------|
| 0 | Observe only | `off` | Collect and analyze; **no** mutating execution or scaffold quotas |
| 1 | Suggest | `manual` | Plans and artifacts; execution needs explicit operator invocation |
| 2 | Safe execution | `supervised` | Local, reversible work within tight **action/cost** budgets |
| 3 | Bounded execution | `limited` | Broader automation; destructive moves still gated |
| 4 | Full autonomy | `active` | **Reserved** — requires `ARGUS_ENABLE_TIER4=1`; otherwise treated like tier 3 for enforcement |

`runs/autonomy/autonomy.json` may include `"tier": N`. Legacy configs without `tier` map **`active` mode → tier 3 (bounded)** so “full” is never implied silently.

CLI: `argus autonomy status` (or `show`), `argus autonomy set-tier <0–4>`, `argus autonomy explain <matrix_key|path/to/action.yaml>`.

## Action classes (matrix)

Conceptual classes (not identical to `ActionType`) are documented in `argus.autonomy.action_matrix`. Examples:

- `analysis`, `local_generation`, `experiments`, `config_change`, `product_scaffolding`, `local_execution`, `external_api_calls`, `publishing`, `shutdown_cleanup`, …
- `account_creation` / `account_setup` and `destructive_cleanup` are **always forbidden** for autonomous matrix permission (human-driven workflows).

Use `argus autonomy explain local_execution` or `argus autonomy explain path/to/action.yaml` to see whether your **effective tier** permits the class (YAML loads infer a matrix key).

## Guardrails (central enforcement)

- **Execution budgets** — `max_actions_per_run`, `max_cost_per_day` in `AutonomyPolicy` (`argus autonomy policy`).
- **Daily quotas** — `max_product_spawns_per_utc_day`, `max_experiments_per_utc_day`, **`max_shutdowns_per_utc_day`** (tier ceilings applied in `effective_policy`; counters in `runs/autonomy/state.json`).
- **Confidence floor (optional)** — `min_confidence_autonomous` in policy (via `policy_overrides`): when &gt; 0, autonomous execution may be denied if `runs/decision_assessment/latest/<id>.json` confidence is below the threshold.
- **Spawn / experiment / shutdown apply** — `argus autonomy spawn --apply`, `argus experiments create`, and **`argus autonomy shutdown --apply`** consult `argus.autonomy.quotas` before succeeding.

## Escalation (hard rules)

`argus escalation generate` merges classic triggers with **bounded rollout** triggers in `argus.escalation.rules`, including:

- Decision assessment **escalation_pressure** ≥ 0.85  
- **Low confidence + high risk** pair from assessment  
- Autonomy **block_streak** ≥ 5 (repeated policy blocks)

**Write deduplication:** by default, Argus **does not write** a new packet if the same **product** and **triggering_rules** set was already saved within **`--dedupe-hours`** (default 24). Use **`--force-save`** to persist anyway. Dashboard **`escalation_dedupe`** groups repeat fingerprints for operator triage.

## Coherence with decisions and stubs

- **Most restrictive wins** between tier matrix, `AutonomyPolicy`, quotas, and execution sandbox—see `argus.autonomy.policy_resolution`.
- **Stub / capability gaps** reduce confidence only when the **top decision** intersects documented stubs (`argus.decision.stub_awareness`), aligned with [stub-inventory.md](stub-inventory.md).

## Policy precedence

`argus.autonomy.policy_resolution.resolve_policy_layers` documents deterministic precedence:

**doctrine → safety_invariants → autonomy_policy → strategy_mode → decision_candidate**

Pass **`safety_invariants`** (human-readable blockers) when integrating external safety checks. Use `argus autonomy status` for a short **policy_resolution_preview** in JSON.

## Operator responsibilities

- Set tier intentionally; keep `runs/autonomy/autonomy.json` in version control when it reflects team policy.
- Review `runs/autonomy/state.json` block streak and guardrail events when automation misbehaves.
- Treat tier 4 as **opt-in** only after runbooks and approvals are ready.

See also [autonomy.md](autonomy.md), [decision-confidence.md](decision-confidence.md), [execution.md](execution.md).
