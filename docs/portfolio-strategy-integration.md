# Portfolio strategy integration (soft influence)

Portfolio **strategic posture** (`create`, `repair`, `expand`, `consolidate`, `harvest`, `retire`) is computed in [`argus/portfolio/strategy.py`](../argus/portfolio/strategy.py) and written to `runs/portfolio/strategy/latest.json`. Downstream tools **read** that artifact when present and apply **bounded nudges** only — they do not replace eligibility, safety, or orchestration gates.

## Principles

- **Soft:** weights, labels, and ordering hints — not hard blocks unless existing logic already enforced a block.
- **Inspectable:** every consumer adds explicit JSON fields (`portfolio_strategy_influence`, `strategy_influence`, `soft_influence`, etc.).
- **Backward compatible:** if `runs/portfolio/strategy/latest.json` is missing or invalid, behavior matches the pre-integration baseline (neutral influence).

## Module

| Concern | Module |
|--------|--------|
| Posture → hints and queue nudge math | `argus/portfolio/strategy_influence.py` |

## Consumers

### Portfolio strategy artifact

`build_portfolio_strategy_payload` includes **`soft_influence`**: human-readable **downstream hints** (creation vs queue vs lifecycle cleanup) plus **`max_queue_priority_nudge`** for transparency.

### Creation proposals

`evaluate_creation_proposals` loads the latest posture and sets:

- **`portfolio_strategy_influence`** — `creation_appetite` (`elevated` / `neutral` / `suppressed` / …), `creation_emphasis_score`, `default_proposal_surfacing`, `notes`.
- Per proposal: **`strategy_influence.surfacing`** (`highlight` / `normal` / `muted` / …) and a short **note**.

Gaps and proposals are **not** removed when appetite is “suppressed”; messaging de-emphasizes net-new creation until repair stabilizes.

### Operator queue

`build_operator_queue_payload` may add **`strategy_priority_nudge`** (bounded, typically within ±1.5 score units) and extends **`priority_score_breakdown.strategy_posture_nudge`**. Entries include **`lifecycle_stage`** for clarity.

Rules are posture-specific, for example:

- **`repair`** — boost rows with blocked/waiting orchestration or import-stress language in recommendations.
- **`create`** — small boost for **idea / build / validate** lifecycle stages.
- **`consolidate` / `harvest`** — slight de-emphasis of pure **advance_ready** tier when that posture is active.
- **`retire`** — attention toward **decline / kill / maintain** lifecycles.

### Portfolio progression

Progression records **`portfolio_strategy_context`** (whether a posture was loaded and a short note). It still advances using the same orchestration rules; only queue **ordering** may have changed upstream.

### Portfolio cycle

Cycle **`summary.portfolio_strategy_context`** includes an **operator_cycle_note** linking strategy to queue and creation behavior without changing **`overall_operator_recommendation`** enums.

## Operational order

For strategy-aware queue and creation:

1. Run **`argus portfolio strategy`** (or refresh) so `runs/portfolio/strategy/latest.json` exists.
2. Run **`argus portfolio refresh`**, **`argus products propose-creation`**, or **`argus portfolio cycle`** as usual.

Stale or missing strategy files simply yield neutral influence.

## See also

- [portfolio-strategy.md](portfolio-strategy.md) — posture scoring
- [product-creation.md](product-creation.md) — creation proposals
- [execution.md](execution.md) — operator cycle artifacts
