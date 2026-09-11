# Orchestration readiness (`argus.orchestration_readiness.v1`)

Orchestration state (`argus.orchestration_state.v1`) includes a **`readiness`** object so “is this product ready to advance?” is an explicit, deterministic computation—not only an emergent side effect of eligibility booleans and `next_action`.

Readiness is built in `argus/orchestrator/readiness.py` and merged into the payload in `argus/orchestrator/eligibility.py` (`_stage_assemble_orchestration_payload`). Inputs are limited to artifacts and metadata already loaded for orchestration: `import_state`, `import_health`, artifact summaries, `eligibility_facts`, waiting inputs, orchestration status, `next_action`, and optional top decision confidence from `runs/decisions/latest/<product_id>.json`.

## Fields

| Field | Meaning |
|--------|---------|
| `schema` | Always `argus.orchestration_readiness.v1`. |
| `readiness_tier` | Coarse ladder: `unprofiled` → `import_incomplete` → `observe_gap` → `interpret_gap` → `advance_ready`. |
| `understanding_debt` | Float in `[0, 1]`: higher means more gaps vs a “fully understood” product (weighted sum of documented conditions, capped). |
| `confidence_gate` | `open` \| `caution` \| `blocked`: how conservative to be about progression given import outcome, staleness, spine completeness, debt, and top decision confidence. |
| `readiness_reason_codes` | Stable string codes with prefix `readiness.` (sorted, unique). |
| `readiness_policy_hint` | Short operator hint string. **Not** the structured `next_action_policy` object on the parent payload—that remains the output of `NextActionPolicy`. |
| `progression_quiescence` | Heuristic: no queued `next_action`, not blocked on import first-pass wait, and orchestration status not in a blocked-waiting state. |
| `metrics` | `top_decision_confidence`, `advisor_conflict`, `confidence_threshold` (for the confidence gate). |

## Relation to timing-analysis primitives

| Concept | Where it lives |
|--------|----------------|
| Artifact readiness tier | `readiness_tier` |
| Confidence gate | `confidence_gate` + `metrics` |
| Understanding debt | `understanding_debt` |
| Next action policy | Still the top-level **`next_action_policy`** object on orchestration state; readiness adds **`readiness_policy_hint`** as a short parallel hint only. |
| Progression quiescence | `progression_quiescence` |

## Backward compatibility

`readiness` is **additive**: existing orchestration state keys are unchanged. Consumers that ignore `readiness` behave as before.
