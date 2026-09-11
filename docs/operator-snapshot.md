# Operator snapshot (`argus.operator_snapshot.v1`)

When orchestration state is written to `runs/orchestration/latest/<product_id>.json`, Argus also writes a **per-product operator snapshot** — a single artifact that consolidates current judgment for human inspection without merging multiple bundles by hand.

## Paths

| File | Purpose |
|------|---------|
| `runs/orchestration/operator_snapshot/<product_id>.json` | Machine-readable snapshot |
| `runs/orchestration/operator_snapshot/<product_id>.md` | Markdown companion (same facts, readable layout) |

Generation is tied to :func:`argus.orchestrator.state_pass.write_orchestration_state_payload` (same payload as the latest orchestration state). It does **not** replace or duplicate `runs/orchestration/latest/<product_id>.json`; it **summarizes** it.

## Payload shape (JSON)

| Field | Source |
|-------|--------|
| `schema` | Always `argus.operator_snapshot.v1` |
| `product_id` | Orchestration state |
| `snapshot_generated_at_utc` | Time of snapshot write |
| `source_evaluated_at_utc` | `evaluated_at_utc` from orchestration state |
| `import_health` | `import_health` on orchestration state |
| `readiness` | Full `readiness` object (`argus.orchestration_readiness.v1`), or recomputed from state fields when missing |
| `understanding_debt` | Copy of `readiness.understanding_debt` for quick scanning |
| `readiness_reason` | Human string from importer gating (`readiness_reason` on state) |
| `next_action` / `next_action_policy` | Same as orchestration state |
| `orchestration_status` / `orchestration_status_reason` | Same as orchestration state |
| `decision_summary` | `readiness.metrics` plus optional `top_decision_title` from `runs/decisions/latest/<product_id>.json` |
| `artifact_freshness_summary` | Phases/staleness from `artifacts` + selected `eligibility_facts` flags |
| `waiting_and_blocking` | `waiting_inputs`, `blockers`, status reason codes |
| `operator_recommendation` | One deterministic paragraph from gate, policy hint, status, waiting kinds, and `next_action` |

## Relationship to other docs

- **Readiness ladder:** [orchestration-readiness.md](orchestration-readiness.md)
- **Next-action selection:** [next-action-policy.md](next-action-policy.md)
- **Orchestration state schema:** `argus.orchestration_state.v1` (see `argus/orchestrator/eligibility.py`)

## Implementation

- Module: `argus/orchestrator/operator_snapshot.py`
- Hooks: `write_operator_snapshot_artifacts` from `write_orchestration_state_payload` in `argus/orchestrator/state_pass.py`
