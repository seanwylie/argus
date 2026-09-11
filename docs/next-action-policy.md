# Next-action policy

This note describes how Argus picks a single **`next_action`** from the **eligible** action list in orchestration state.

## Separation of concerns

| Layer | Responsibility |
|--------|----------------|
| **Eligibility** (`argus/orchestrator/eligibility.py`) | Decides which actions are allowed, merges execution-feedback deprioritization, applies **import tier** gating, and applies **state hygiene** ordering (`eligible_actions_order_rule`). Output: ordered `eligible_actions[]`. |
| **Policy** (`argus/orchestrator/next_action_policy.py`) | Chooses one recommended id as **`next_action`** from that ordered list using a **fixed precedence** and optional **soft planning** nudges. |
| **Planning soft priority** (`argus/planning/orchestration_priority.py`) | Reorders priority among *already eligible* ids using small integer boosts (`score = boost - index`). Only used when the policy reaches the “soft planning” step. |

**Progression** (`argus/orchestrator/progression.py`) consumes **`next_action`** from evaluation; it does not re-run policy. Fingerprinting uses `next_action` plus status/eligible rows, not the policy blob.

## Policy precedence (declarative)

1. **Refinement waiting** — If the product is in a refinement “waiting for grounded input” posture (`waiting`), prefer **`refinement_submit_reviews_in`** when it appears in eligible actions; otherwise **`none`**. Planning soft priority is **not** applied (same as before refactor).
2. **Implementation plan progression** — If **`implementation_plan_generate`** is eligible and refinement is not blocked (`stuck`, `human_exhausted`, `rejected`), select it **before** any planning nudge. This preserves the product_spec → implementation_plan corridor.
3. **Soft planning** — Otherwise call **`pick_next_action_soft_planning`** on the ordered eligible id list. Canonical **head** is `eligible_actions[0]`; planning may promote another id if its mode-specific **boost** overcomes index penalty.
4. **No eligible actions** — **`next_action`** = **`none`**.

## Tie-breaking

- **Within soft planning**: higher `boost - index` wins; ties keep the best-so-far scan order (effectively favoring earlier canonical order among ties).
- **Across policy steps**: earlier rule wins (waiting > progression > soft planning).

## Readiness and import influence

- **Import tier** (`import_readiness_tier`) does not add a separate policy branch. Importer gating runs **before** policy and **filters** `eligible_actions`. Policy records **`influences.import_readiness_tier`** and a short **`readiness_reason_excerpt`** for inspectability.
- **Readiness** strings (`readiness_reason` on orchestration state) are human context; they do not change rule order.

## Observability vs generation vs refinement

The policy exposes a coarse **`action_family`** on **`next_action_policy`** (`observability`, `generation`, `experiments`, `refinement`, `governance`, `progression`, `other`) for dashboards and debugging. It does **not** drive selection.

## Inspectable fields

Orchestration state includes:

- **`next_action`** — chosen id.
- **`next_action_policy`** (`argus.next_action_policy.v1`) — **`rule_applied`**, **`ordering_basis`**, **`tie_break`**, **`influences`**, **`planning`**, **`action_family`**.
- **`eligibility_facts.next_action_rule_applied`** / **`next_action_tie_break`** — duplicates key facts next to existing planning explainability keys.

## Extension points

- Add new **precedence** steps in **`resolve_next_action`** (keep ordering explicit and tested).
- Adjust **soft** behavior only in **`pick_next_action_soft_planning`** (boost sets per `planning_mode`).
