# Portfolio allocation

**Attention allocation** (`argus portfolio allocate`) turns local artifacts into **percentage focus** per product and **push / ignore** lists.

## Inputs (automatic)

For each valid product, `gather_allocation_inputs` combines:

- **Priority** — Top decision candidate score from `build_portfolio_from_inventory` / `rank_portfolio_from_decisions` (already **strategy-aware** via `generate_decisions` + `compute_priority_score` when strategy file exists).
- **Trends** — `runs/trends/latest.json` summary row per product (flags, drift count).
- **Economics** — Cost, ROI hints, spikes from economics analysis.
- **Experiments** — Counts of active/proposed/completed/failed from `runs/experiments/`.
- **Signals** — Record count from latest signal bundle.

## Strategy alignment

Changing **`argus strategy set`** affects **decision priority scores first**; allocation inherits those scores as the dominant weight in `_raw_weight`, then applies trend/economics/experiment multipliers. Experiment **ranking** (`argus experiments rank`) uses the same strategy profile directly.

## Interpretation

- **Focus %** — Relative attention, not dollar budgeting; sums to ~100% across products.
- **Push** — Top-quartile or high-priority + activity signals.
- **Ignore** — Kill-candidate + low score, stagnation, or very low focus (see `compute_allocation` rules).

Refresh summaries (`argus portfolio refresh`) complement allocation: refresh regenerates decisions/findings; allocate answers “where should I spend my *time* this week?”.

**Economics on refresh:** each ranked row in `runs/portfolio/latest/refresh.json` includes an **`economics`** object (per-product `ProductEconomics` from declared cost + latest signal bundle). The payload also includes **`economics_by_product`** for quick joins. **Weekly planning** (`argus planning weekly`) appends cost / ROI / burn / `traction_profile` (`monetized` vs `vanity_traction` vs `unknown`) into per-product rationale lines. If economics analysis fails, the weekly plan still emits with a **`UserWarning`** (not a silent omission).

**Decision context:** `portfolio refresh` embeds a **decision context** block per product in its step payload (and writes `runs/decision_assessment/latest/<id>.json`), summarizing confidence, uncertainty, risk, and escalation pressure for that refresh—see [decision-confidence.md](decision-confidence.md).

**Temporal note:** decision bundles may include **freshness metadata** when `apply_freshness_to_candidates` runs (stale or missing operational signals reduce confidence for gated finding kinds). That is independent of portfolio weights but affects how much to trust each recommendation; see [temporal-intelligence.md](temporal-intelligence.md).
