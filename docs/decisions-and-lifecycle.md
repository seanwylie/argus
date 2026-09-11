# Decisions and lifecycle scoring

Argus **does not execute** actions here. It **selects and ranks** `DecisionCandidate` objects from **findings** plus a deterministic **lifecycle assessment**.

## Lifecycle model

Each product has a **`LifecycleStage`** (`idea` → `build` → `validate` → `grow` → `maintain` → `decline` → `kill`).

`LifecycleAssessment` holds five scores in **`[0, 1]`**, each combining:

1. A **stage prior** (table in `argus/lifecycle/scoring.py`).
2. **Evidence** from findings: each finding nudges relevant dimensions by  
   `0.12 × severity_weight(severity)` (clipped), routed by `FindingKind`.

Dimensions:

| Dimension | Meaning |
|-----------|---------|
| `move_forward` | Ready to advance gate, widen experiment, or invest |
| `hold` | Wait; limited contradictory evidence |
| `improve` | Fix quality, cost posture, or data before bigger bets |
| `deprecate` | Sunsetting path is appropriate |
| `kill` | Terminal wind-down pressure |

### Kill candidate

**`kill_candidate`** is `True` when:

- `kill >= 0.65`, and  
- `move_forward <= 0.38`

So it is a **flag for human escalation**, not an automatic kill. It means “this product’s profile looks like wind-down unless something changes.”

## Decision intents

Candidates carry `metadata["intent"]` (`DecisionIntent`): e.g. `improve_product`, `gather_more_data`, `launch_experiment`, `reduce_cost`, `hold_steady`, `deprecate_product`, `kill_product`, `escalate_to_human`.

`action_type` remains the existing `ActionType` enum (`INVESTIGATE`, `ANALYZE`, `DEPRECATE`, …) for compatibility with later execution.

## Priority score (0–100)

Implemented in `argus/decision/priority.py` with fixed weights:

| Component | Weight |
|-----------|--------|
| Impact (from finding severity) | 0.22 |
| Confidence | 0.18 |
| Urgency (severity + kind tweak) | 0.20 |
| Lifecycle fit (intent vs assessment scores) | 0.15 |
| Cost penalty (spend vs cap) | −0.12 |
| Effort penalty (finding effort bucket) | −0.13 |

Result is a **weighted linear combination**, clipped to [0, 1] then scaled to **100**. No ML.

## Portfolio

`argus decisions portfolio` ranks products by the **top candidate’s** `priority_score` per product (requires `runs/findings/latest/<product_id>.json`).

## CLI

```bash
argus findings generate          # or findings generate <id>
argus decisions generate         # or decisions generate <id>
argus decisions portfolio
argus decisions show <product_id>
```

Artifacts: `runs/decisions/latest/<product_id>.json`, `runs/decisions/latest/portfolio.json` (gitignored).

## Next layer

**Planning / orchestration**: turn ranked `DecisionCandidate`s into `ActionProposal` with concrete `command` paths from `product.yaml`, approvals, and execution — still out of scope for this module.
